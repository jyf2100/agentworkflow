"""配置六层漂移看门狗——模型路由/凭据的层间一致性断言（add-config-drift-watchdog，spec: hub-config-drift-watchdog）。

六层模型（2026-10-31 真机事故实证）：L1 本机 CLI settings / L2 本机形态路由文件（两层 hub 不可达，
MUST NOT 断言——排障入口在 RUNBOOK 六层模型节）/ L3 hub.env（env_file 仅容器创建时读入，restart
不重读）/ L4 凭据保险箱（vault 优先、env 兜底）/ L5 网关注册面（模型名按网关分家）/ L6 run 容器实况。

断言范围 = hub 可达四层（R1 L3意图vs快照 / R2 L4vsL3兜底源 / R3 L5 membership+消费面探活+alias 形态
（add-drift-l5-health-probe：名册在册≠上游可用，L5 健康=存在∧可用两判据合取——审核 P1-1）/
R4 L6 在场+形态），全部机械判据零人工判读，网络依赖三态分流（UNKNOWN 绝不进漂移项）。

纪律：只读不写（绝不自动修复）；逐规则吞异常（单规则挂不连坐）；值绝不入面（键名/长度先例：
vault_store.placeholder_issues——BASE_URL 是误贴 token 常见通道，片段不进日志/通知）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import vault_store
from runner import cli_env

# claude CLI 常量 alias（run 93 实证：容器 CLI 把 alias 解析成裸 Anthropic id → roc 400）。
# 负向必死黑名单而非正向白名单：四 alias 是 CLI 常量不随 roc 演化腐化（spec 口径）。
ALIAS_BLACKLIST = frozenset({"haiku", "sonnet", "opus", "fable"})

# 凭据键（R2 比对第一档）：与 vault_store.FORGE_TOKEN_KEYS 值域 + anthropic 两值一致
CRED_KEYS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "GH_TOKEN", "GITLAB_TOKEN", "GITCODE_TOKEN")

# R4 必需在场键（注入链断裂判据——只断言在场与形态，绝不比对具体值）
L6_REQUIRED = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN")

_HTTP_TIMEOUT = 10
_EXEC_TIMEOUT = 15

# 去抖键形态：("drift", rule, key) 参与消解清空；（"info"/"premise"/"off"/"degrade", ...）
# 是持续态提示不参与消解（防消解后 info 项重复刷屏）。
HttpGet = Callable[[str, str], tuple[int, list[str] | None]]
# 探活接缝（add-drift-l5-health-probe）：(url, token, model) -> HTTP status（2xx 折叠 200；0=网络/超时）。
HttpPost = Callable[[str, str, str], int]

# 探活确定性死链闭合集：固定 canonical 最小补全遭 4xx 拒绝是「注册的属性而非请求的属性」
# （404 = model_not_found/no deployments 网关权威裁决「此名无可用部署」；400 = invalid_request 类
# 确定性拒绝）。排除 429（负载瞬态）/401/403（探活凭据作用域非模型死活）/5xx（死链原始连接错与
# 过载同形，单发不可机械分辨——非确定性错绝不进漂移，RUNBOOK §23 排障速查给人工复测通道）。
PROBE_DEAD_CODES = frozenset({400, 404})


@dataclass
class DriftItem:
    """一个漂移项：规则 + 层对 + 键名 + 细节（可含长度差，绝不含值）+ 修复指引。"""
    rule: str
    layers: str
    key: str
    detail: str
    fix: str

    def text(self) -> str:
        return f"[pa-hub 漂移] {self.rule}（{self.layers}）键 {self.key}：{self.detail}｜修复：{self.fix}"


def _default_http_get(url: str, token: str) -> tuple[int, list[str] | None]:
    """默认网关探测：GET {base}/v1/models（Bearer token）。返回 (status, model_ids|None)。

    0 = 网络错/超时/解析失败（UNKNOWN 形态）；4xx/5xx 原样返回由调用方三态分流。
    """
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, None
    data = payload.get("data") if isinstance(payload, dict) else None
    ids = [m.get("id") for m in data if isinstance(m, dict) and m.get("id")] if isinstance(data, list) else []
    return 200, ids


def _default_http_probe(url: str, token: str, model: str) -> int:
    """默认消费面探活：POST {base}/v1/messages 最小补全（Bearer token 同 membership）。

    请求体固定 canonical（三必填字段 + max_tokens=1 + "ping"）——4xx 拒绝因此是注册的属性而非
    请求的属性（PROBE_DEAD_CODES 判据根基）。返回 HTTP status（2xx 一律折叠 200）；0 = 网络错/
    超时（UNKNOWN 形态）。响应体不参与判据（status 码锚定，不嗅探 body 错误文本）也不落日志。
    """
    body = json.dumps({"model": model, "max_tokens": 1,
                       "messages": [{"role": "user", "content": "ping"}]}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT):
            return 200
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0


def _parse_intent(path: Path) -> tuple[dict[str, str], list[str]]:
    """intent 文件解析（compose env_file 字面语义）：# 注释/空行跳过，首个 = 后取字面值。

    坏行（无 =）记行号不崩（stderr warn 由调用方出）。
    """
    values: dict[str, str] = {}
    bad: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            bad.append(f"L{lineno}")
            continue
        k, _, v = line.partition("=")
        if k.strip():
            values[k.strip()] = v
    return values, bad


def _dispatch_source(cfg, vault) -> tuple[str, str, str | None]:
    """派发源三值（base/token/model）——与 lifecycle._credentials 同序解析：vault 优先、cfg 兜底。

    绝不另立真相源：vault 有记录按记录（model 缺再按 cfg.user_model 兜底注入，同 #56 语义）。
    """
    rec = None
    if vault is not None:
        try:
            rec = vault.get_user_env(cfg.user_id)
        except vault_store.VaultError:
            rec = None
    if rec:
        base = rec.get("ANTHROPIC_BASE_URL") or cfg.anthropic_base_url
        token = rec.get("ANTHROPIC_AUTH_TOKEN") or cfg.anthropic_auth_token
        model = rec.get("ANTHROPIC_MODEL") or cfg.user_model
    else:
        base, token, model = cfg.anthropic_base_url, cfg.anthropic_auth_token, cfg.user_model
    return base, token, model


def _rule_r1(cfg, drift: list, pending: list, states: dict) -> None:
    """R1：L3 意图（ro 挂载 hub.env 副本）vs L3 快照（hub 进程 env）。"""
    path = cfg.drift_env_intent
    if path is None:
        pending.append(("off", ("off", "r1"),
                        "[drift][warn] R1 关闭：HUB_DRIFT_ENV_INTENT 未配置（intent 面未挂载）——仅提示一次"))
        states["r1"] = "关闭"
        return
    try:
        values, bad = _parse_intent(Path(path))
    except OSError as e:
        # 前提故障（非规则漂移）：挂了读不了——bind mount 保留宿主属主，0600 属主 uid≠1001
        # 时容器进程恒 EACCES。一次性高声（含对齐修法），心跳行标注降级，绝不逐轮刷异常。
        pending.append(("premise", ("premise", "r1_intent"),
                        f"[drift][ERROR] R1 降级（intent 不可读）：{Path(path)} 打开失败"
                        f"（{type(e).__name__}）——bind mount 保留宿主属主，宿主侧对齐："
                        f'chown "$(id -un)":1001 hub.env && chmod 0640 hub.env'
                        "（gid 1001 被占改 setfacl -m u:1001:r hub.env）"))
        states["r1"] = "降级（intent 不可读）"
        return
    for lineno in bad:
        print(f"[drift][warn] intent 坏行（无 =，跳过）：{Path(path)}:{lineno}", file=sys.stderr)
    for k in sorted(values):
        env_v = os.environ.get(k)
        if env_v is None:
            drift.append(DriftItem(
                "R1", "L3意图→L3快照", k,
                f"盘上 hub.env 已配（len={len(values[k])}）但 hub 进程 env 缺键——容器未重建"
                "（若该键由 compose environment: 覆盖，请从 hub.env 移除，属部署配错）",
                "docker compose up -d 重建（restart 不重读 env_file）"))
        elif env_v != values[k]:
            drift.append(DriftItem(
                "R1", "L3意图→L3快照", k,
                f"值不一致（len: 进程 {len(env_v)} vs 盘上 {len(values[k])}）——盘上已改、容器未重建",
                "docker compose up -d 重建（restart 不重读 env_file）"))
    states["r1"] = "ok"


def _rule_r2(cfg, vault, drift: list, pending: list, states: dict) -> None:
    """R2：L4（vault cfg.user_id 记录）vs L3 兜底源（cfg.user_env() 同源键）。"""
    if vault is None:
        states["r2"] = "关闭（env-only）"
        return
    try:
        rec = vault.get_user_env(cfg.user_id)
    except vault_store.VaultError as e:
        pending.append(("premise", ("premise", "r2_vault"),
                        f"[drift][warn] R2 降级：保险箱读取失败（{type(e).__name__}）——仅提示一次"))
        states["r2"] = "降级"
        return
    if rec is None:
        pending.append(("info", ("info", "r2", "no_record"),
                        f"[drift][info] R2：vault 无 user={cfg.user_id} 记录——派发恒走 env 兜底"
                        "（lifecycle warn 同口径），确需 vault 化时 vault-import --from-env"))
        states["r2"] = "无记录"
        return
    env_side = cfg.user_env()  # L3 兜底源（canonical 键面）
    for k in CRED_KEYS:
        if k in rec and k in env_side and rec[k] != env_side[k]:
            drift.append(DriftItem(
                "R2", "L4→L3兜底源", k,
                f"vault 旧快照：派发正走 vault 旧值（vault 优先），改 hub.env 不生效即此因"
                f"（len: vault {len(rec[k])} vs env {len(env_side[k])}）",
                "hub 容器内 python cli.py vault-import --from-env（merge 幂等：旧记录打底 + env 非空覆盖）"))
    vm, em = rec.get("ANTHROPIC_MODEL"), env_side.get("ANTHROPIC_MODEL")
    if vm and em and vm != em:
        # 信息项不进漂移（显式选择保护）：派发以 vault 值优先是合法语义，机械不可辨
        # 「显式选择」与「导入早于 hub.env 改模型」——降档并警示 merge 会覆盖显式选择。
        pending.append(("info", ("info", "r2", "model"),
                        f"[drift][info] R2 模型键分歧（len: vault {len(vm)} vs HUB_USER_MODEL {len(em)}）："
                        f"派发按既有语义以 vault 值优先（合法显式选择）；确系旧快照可 vault-import --from-env"
                        f" 重导，但 merge 会以 hub.env 非空值覆盖该显式选择（面板找回需重录入）——不标为漂移"))
    states["r2"] = "ok"


def _rule_r3(cfg, vault, drift: list, pending: list, states: dict,
             http_client: HttpGet, probe_client: HttpPost) -> None:
    """R3：L5 membership（三态）+ 消费面探活（三态）+ alias 形态预检（负向黑名单，零网络）。"""
    base, token, model = _dispatch_source(cfg, vault)
    names: set[str] = set()
    for k, v in os.environ.items():
        if k.startswith("PA_PERSONA_MODEL_") and v:
            names.add(v)
    if os.environ.get("PA_DEV_MODEL"):
        names.add(os.environ["PA_DEV_MODEL"])
    if model:
        names.add(model)
    if not names:
        states["r3"] = "关闭（无消费面模型名）"
        return
    for m in sorted(names & ALIAS_BLACKLIST):
        drift.append(DriftItem(
            "R3", "L5形态预检", m,
            "claude CLI 常量 alias 将透传进 run 容器——容器 CLI 把 alias 解析成裸 Anthropic id 遭网关 400（run 93 实证）",
            "改用裸模型名（网关侧有效注册名）"))
    if not (base and token):
        states["r3"] = "关闭（无可解析凭据）"
        return
    status, ids = http_client(f"{base.rstrip('/')}/v1/models", token)
    if status == 200 and ids is not None:
        registered = set(ids)
        for m in sorted((names - ALIAS_BLACKLIST) - registered):  # 精确匹配（大小写敏感——分家实证）
            drift.append(DriftItem(
                "R3", "L5注册→消费面", m,
                "网关 /v1/models 未注册该模型名（精确匹配，大小写敏感——roc 认 Qwen3.8-27B 不认"
                " qwen3.8-27b-fast 的分家实证）",
                "LiteLLM admin API /model/new 注册/修正（需 master key = litellm 容器 env LITELLM_MASTER_KEY）"))
        # 探活步（add-drift-l5-health-probe，审核 P1-1）：名册在册 ≠ 上游可用——在册名逐名一次
        # 最小补全（max_tokens=1，与 membership 同轮内、缺名者不探活不重复计费）三态分流：
        # 2xx 可用零报 / 404·400 确定性死链进漂移 / 其余（网络/超时/5xx/429/401/403）UNKNOWN
        # 绝不进漂移——确定性错绝不归 UNKNOWN 静默，反向同样成立（design D1）。
        # 键面 = 模型名（探活）——与 membership 缺名项（键 = 裸模型名）去抖身份独立：修复路径的
        # 跨轮形态转换（/model/delete 删注册→缺名 / 重新注册→死链）无消解轮隔开也恢复告警
        # （冷评审 R2：共用裸键会让「按指引删除」后的缺名被 new_drift 吞掉且永不消解）。
        unknown = 0
        for m in sorted((names - ALIAS_BLACKLIST) & registered):
            code = probe_client(f"{base.rstrip('/')}/v1/messages", token, m)
            if 200 <= code < 300:
                continue
            if code in PROBE_DEAD_CODES:
                drift.append(DriftItem(
                    "R3", "L5名册→L5可用", f"{m}（探活）",
                    f"网关名册在册但消费面探活遭确定性拒绝（HTTP {code}，max_tokens=1 最小补全）——"
                    "名册在册≠上游可达（2026-10-31 死链 qwen3.6-35b→8896 形态，审核 P1-1）",
                    "LiteLLM admin API 修正该注册上游（/model/update 改 litellm_params 指向活上游）"
                    "或下线死注册（/model/delete；需 master key = litellm 容器 env LITELLM_MASTER_KEY）"))
            else:
                unknown += 1  # 瞬时族：单发探活无法机械分辨持续死与瞬时抖（心跳可见不进 MM）
        states["r3"] = "ok" if not unknown else f"ok（探活 UNKNOWN×{unknown}）"
    elif status in (401, 403):
        pending.append(("degrade", ("degrade", "r3_403"),
                        "[drift][warn] R3 membership 断言降级：网关 /v1/models 401/403"
                        "（virtual key 限 llm_api_routes，列表路由可能不在内）——仅提示一次，alias 预检不受影响"))
        states["r3"] = "UNKNOWN（401/403，membership 降级）"
    else:
        states["r3"] = "UNKNOWN（网络/5xx）"


def _rule_r4(dbm, drift: list, states: dict, docker_bin: str) -> None:
    """R4：L6 在场 + 形态（最老 running run 容器 printenv——只断言在场与形态，绝不比值）。"""
    try:
        runs = dbm.runs_in_status("running")
    except Exception as e:  # noqa: BLE001——观察面：查询失败跳过本轮不误报
        print(f"[drift][warn] R4 查询 running 失败（跳过本轮）：{type(e).__name__}", file=sys.stderr)
        states["r4"] = "跳过（查询失败）"
        return
    if not runs:
        states["r4"] = "无 running"
        return
    run = runs[0]  # ORDER BY id → 最老（1:N 即弃常态下取样一个）
    try:
        r = subprocess.run(
            [docker_bin, "exec", run["container_ref"], "printenv"],
            capture_output=True, text=True, timeout=_EXEC_TIMEOUT,
            env=cli_env(run["runner_docker_host"]),
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[drift][warn] R4 exec 失败（跳过本轮，即弃容器常态不误报）：{type(e).__name__}", file=sys.stderr)
        states["r4"] = "跳过（exec 失败）"
        return
    if r.returncode != 0:
        states["r4"] = "跳过（容器终态/exec 非零）"
        return
    container_env: dict[str, str] = {}
    for line in r.stdout.splitlines():  # printenv 输出仅在内存断言，绝不落日志（值不入面）
        k, sep, v = line.partition("=")
        if sep:
            container_env[k] = v
    missing = [k for k in L6_REQUIRED if not container_env.get(k)]
    checked = {k: v for k, v in container_env.items()
               if k in (*L6_REQUIRED, "GH_TOKEN", "GITLAB_TOKEN", "GITCODE_TOKEN", "ANTHROPIC_MODEL")}
    bad = vault_store.placeholder_issues(checked)
    if missing or bad:
        drift.append(DriftItem(
            "R4", "L6实况", " + ".join(missing) if missing else "形态",
            ("必需键缺席（注入链断裂）：" + " + ".join(missing)) if missing else "；".join(bad),
            "修上游层（hub.env 重建 / vault 重导入）后重派——L6 无直写；排障通道 docker exec printenv"))
        states["r4"] = "漂移"
    else:
        states["r4"] = "ok"


def check_drift(cfg, vault, dbm, *, seen: set, notifier=None,
                http_client: HttpGet | None = None, probe_client: HttpPost | None = None,
                docker_bin: str = "docker") -> None:
    """单轮漂移检查（daemon 每 drift_ticks 个 tick 调一次）。

    seen = 去抖集合（调用方持有，进程生命周期）；notifier = 通道（None = 全降级 stderr）。
    报告三分：漂移项 → MM + stderr 去抖一次；前提故障 → MM 一次性 + 心跳降级标注；
    信息项 → 仅 stderr 去抖（不进 MM，值班私信只承载真漂移）。每轮 stderr 一行心跳。
    异常逐规则吞（单规则挂不连坐），本函数正常返回；不写任何面（只读观察）。
    probe_client（add-drift-l5-health-probe）缺省 _default_http_probe——lifecycle 挂点零改动。
    """
    if http_client is None:
        http_client = _default_http_get
    if probe_client is None:
        probe_client = _default_http_probe
    drift: list[DriftItem] = []
    pending: list[tuple[str, tuple, str]] = []  # (kind, 去抖键, 文本)
    states: dict[str, str] = {}
    for rid, fn in (("r1", lambda: _rule_r1(cfg, drift, pending, states)),
                    ("r2", lambda: _rule_r2(cfg, vault, drift, pending, states)),
                    ("r3", lambda: _rule_r3(cfg, vault, drift, pending, states, http_client, probe_client)),
                    ("r4", lambda: _rule_r4(dbm, drift, states, docker_bin))):
        try:
            fn()
        except Exception as e:  # noqa: BLE001——单规则异常不连坐他规则（D5）
            states[rid.upper()] = "异常（跳过本轮该规则）"
            print(f"[drift][warn] {rid.upper()} 异常（不连坐）：{type(e).__name__}: {e}", file=sys.stderr)

    def _emit(text: str, *, mm: bool) -> None:
        print(text, file=sys.stderr)
        if mm and notifier is not None:
            notifier.notify_admin(text)  # 内部 fail-open 不抛；未配通道 = 可见的丢（stderr 已留痕）

    new_drift = [d for d in drift if ("drift", d.rule, d.key) not in seen]
    for d in new_drift:
        seen.add(("drift", d.rule, d.key))
        _emit(d.text(), mm=True)
    for kind, key, text in pending:
        if key in seen:
            continue
        seen.add(key)
        _emit(text, mm=(kind == "premise"))  # off/info/degrade 仅 stderr；premise 一次性进 MM
    # 消解回执：当轮零漂移且历史漂移键非空 → 一次回执 + 仅清漂移类键（复发可再告警；
    # info/premise 类是持续态提示不清，防消解后重复刷屏）。
    drift_keys = {s for s in seen if s[0] == "drift"}
    if not drift and drift_keys:
        for s in drift_keys:
            seen.discard(s)
        _emit(f"[pa-hub] 配置漂移已消解（{len(drift_keys)} 项）——本轮四规则零漂移", mm=True)
    hb = "｜".join(f"{k.upper()}={states.get(k.upper(), states.get(k, '?'))}" for k in ("r1", "r2", "r3", "r4"))
    print(f"[drift] 心跳：漂移 {len(drift)} 项（本轮新报 {len(new_drift)}）｜{hb}", file=sys.stderr)
