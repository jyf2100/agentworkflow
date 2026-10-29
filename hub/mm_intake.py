"""hub MM 入站面——/pa slash command 处理核心（add-mm-channel-intake，spec: hub-mm-intake）。

api.py 的 POST /mm/intake 是薄壳（token 恒时校验 + form 解析 + 调本模块 + 回
ephemeral JSON + 后置频道 post）；本文件承载全部语义面：

- 成员三态授权（D3）：FOUND 在集授权/不在集拒绝；UNKNOWN（网络错/5xx/403 bot
  无权限——判据不可用 ≠ 授权结论）fail-closed 拒绝且绝不回执「非成员」；
  404 频道不存在 = 确定性配置错回执检查绑定。记忆化缓存 TTL 60s 仅 FOUND 可
  缓存（零持久写面不变式）。
- evidence 闸：正文含 `出处:` / `出处：`（半/全角冒号两形态——中文输入法高频）
  且行内值非空；同面板「空依据闸前拒」语义，机械形态各自适配（表单字段 vs
  文本标记）。
- 确定性 wrap（D5 幂等前提）：frontmatter 零时间戳零随机成分；文件名
  `mm-<sha[:8]>.md`（与面板 stamp 前缀形态有意偏离——确定性是幂等前提，勿
  「顺手统一」加回日期/uuid）；deliver_key 小时窗（UTC）撞键回既有项幂等。
- 子命令分流（D3 双轨）：run 仅频道成员判据；bind/unbind 仅 admin 白名单判据
  （HUB_PANEL_ADMINS 同键不同空态：面板空 = OAuth 会话回退放行，slash 空 =
  fail-closed 全拒——无会话判据兜底）。

双回执（D7）：HTTP 响应 = ephemeral（仅发起人可见）；入队成功后 bot 另 post
非 ephemeral 进频道（后置于响应、fail-open）；拒绝不进频道（个人错误不公开）。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import prd_store
from db import MM_CHANNEL_ID_RE
from lifecycle import PROJECT_RE
from notify import MMChannelNotFound

# 与 api._DELIVER_MAX_BYTES 同源护栏（1MB）：超长拒绝不截断（截断的 PRD 是语义
# 损坏），提示走面板。mm text 实际量级远低于此（slash 上限 ~2000 字符），此闸
# 是同源防御非主要限流。
MAX_TEXT_BYTES = 1024 * 1024
# evidence 提取值上限（与 api._PANEL_EVIDENCE_MAX 同量级：frontmatter 单行）
_EVIDENCE_MAX = 2048
# 成员判定缓存 TTL（秒）——入站频率低，60s 平衡「踢人即时生效」与「查询压力」
MEMBER_CACHE_TTL_S = 60.0

# `出处:` / `出处：`（半/全角冒号，冒号前空白容忍）
_EVIDENCE_RE = re.compile(r"出处\s*[:：]")

MEMBER = "member"
NOT_MEMBER = "not_member"
UNKNOWN = "unknown"
CHANNEL_MISSING = "channel_missing"

_USAGE = (
    "用法：\n"
    "• `/pa run <需求正文> 出处: <链接或说明>` —— 需求入队（project 由频道推断）\n"
    "• `/pa bind <project>` —— 绑定本频道与项目（仅 admin）\n"
    "• `/pa unbind` —— 解除本频道绑定（仅 admin）\n"
    "• `/pa confirm #<行id>` —— 拍板指标清单并入 PRD 入队（仅发起人）\n"
    "• `/pa reject #<行id>` —— 放弃指标清单（仅发起人）\n"
    "• `/pa metrics #<行id> <补充>` —— 补充上下文重新拟指标（仅发起人）"
)

# D5 重入队上界：同一原始需求（origin_sha 祖先链）confirm/metrics 重入队达 3 次
# 未收敛 → 回执人工改写（对照 revise 既有 1 次额度，全链重试语义有界）
CONFIRM_REENQUEUE_MAX = 3


def readable_projects(data_root: Path) -> list[str]:
    """project 清单 = data_root 下有启动必读面的子目录（与派发 fail fast 同判据，
    lifecycle.py 启动必读面检查）——与 api._panel_projects 同源（两皮一芯）。"""
    if not data_root.is_dir():
        return []
    return sorted(
        sub.name for sub in data_root.iterdir()
        if sub.is_dir() and PROJECT_RE.fullmatch(sub.name)
        and (sub / "profiles").is_dir() and (sub / "sources.yaml").is_file()
    )


class MemberCache:
    """(channel_id, user_id) 判定缓存——进程内存（通知零持久写面不变式不动）。
    仅 member/not_member（FOUND）可缓存；unknown 不缓存（下次重试判据——MM 抖动
    恢复后立即放行）；channel_missing 不缓存（bind 修正后即恢复）。"""

    def __init__(self, clock=None):
        self._clock = clock or time.monotonic
        self._entries: dict[tuple[str, str], tuple[float, str]] = {}

    def get(self, channel_id: str, user_id: str) -> str | None:
        hit = self._entries.get((channel_id, user_id))
        if hit is None:
            return None
        expires, verdict = hit
        if self._clock() >= expires:
            self._entries.pop((channel_id, user_id), None)
            return None
        return verdict

    def put(self, channel_id: str, user_id: str, verdict: str) -> None:
        self._entries[(channel_id, user_id)] = (self._clock() + MEMBER_CACHE_TTL_S, verdict)


def membership_verdict(mm, channel_id: str, user_id: str) -> str:
    """无缓存三态判定。HttpMMClient 网络错已在 client 内归 None；Fake 注入的
    crash 异常在此捕获——两 client 的异常路径归一到判层面 = UNKNOWN。"""
    if mm is None:
        return UNKNOWN  # bot 通道未配置 = 判据不可得（fail-closed，绝不旁路）
    try:
        ids = mm.channel_member_ids(channel_id)
    except MMChannelNotFound:
        return CHANNEL_MISSING
    except Exception:  # noqa: BLE001——任何查询异常都是判据不可用，非授权结论
        return UNKNOWN
    if ids is None:
        return UNKNOWN
    return MEMBER if user_id in ids else NOT_MEMBER


def check_membership(mm, channel_id: str, user_id: str, cache: MemberCache | None) -> str:
    """带缓存三态判定（D3 + 3.2：仅 FOUND 可缓存）。"""
    if cache is not None:
        hit = cache.get(channel_id, user_id)
        if hit is not None:
            return hit
    verdict = membership_verdict(mm, channel_id, user_id)
    if cache is not None and verdict in (MEMBER, NOT_MEMBER):
        cache.put(channel_id, user_id, verdict)
    return verdict


def has_evidence(text: str) -> bool:
    """evidence 机械判据：`出处:`/`出处：` 标记存在且行内值非空（空依据 =
   闸前拒，与面板同语义——表单字段 vs 文本标记，形态各自适配）。"""
    m = _EVIDENCE_RE.search(text)
    if m is None:
        return False
    rest = text[m.end():]
    line = rest.splitlines()[0] if rest.splitlines() else ""
    return line.strip() != ""


def extract_evidence(text: str) -> str:
    """标记行内值（单行化，超 _EVIDENCE_MAX 抛 ValueError——调用方转拒绝回执）。"""
    m = _EVIDENCE_RE.search(text)
    rest = text[m.end():]
    line = rest.splitlines()[0] if rest.splitlines() else ""
    ev = " ".join(line.split())
    if len(ev.encode("utf-8")) > _EVIDENCE_MAX:
        raise ValueError(f"出处超 {_EVIDENCE_MAX} 字节上限（压缩单行后）")
    return ev


def wrap_mm_prd(project: str, body: str, user_id: str, username: str,
                channel_id: str, evidence: str) -> str:
    """确定性 wrap（D5）：frontmatter 零时间戳零随机成分——同 (user, text) 重试
    产出 byte-identical 文件，是 deliver_key 幂等的前提。正文放原始 text 全文
    （机械拼装零语义肢解），evidence 提取值进 frontmatter 可审计单行。"""
    ev = " ".join(evidence.split())
    esc = ev.replace("\\", "\\\\").replace('"', '\\"')
    front = (
        f"project: \"{project}\"\n"
        f"source_path: ''\n"
        f"source: mm-channel\n"
        f"user_id: \"{user_id}\"\n"
        f"username: \"{username}\"\n"
        f"channel_id: \"{channel_id}\"\n"
        f"evidence: \"{esc}\"\n"
    )
    return f"---\n{front}---\n\n{body.lstrip()}"


def deliver_key_for(project: str, user_id: str, wrapped: str,
                    now_utc: datetime | None = None) -> str:
    """`mm:{project}:{user_id}:{sha256(wrapped)[:16]}:{yyyymmddhh}`（UTC 小时窗——
    对齐 hub db 既有 ts UTC 先例）：同窗 MM webhook 重试幂等回既有项；跨窗有意
    重发（隔天重跑同需求）不被拦。"""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    sha = hashlib.sha256(wrapped.encode("utf-8")).hexdigest()
    return f"mm:{project}:{user_id}:{sha[:16]}:{now_utc:%Y%m%d%H}"


def _ephemeral(text: str, channel_post: str | None = None) -> dict:
    return {"ephemeral": text, "channel_post": channel_post}


def handle_command(*, database, engine, cfg, mm, form: dict,
                   member_cache: MemberCache | None = None,
                   now_utc: datetime | None = None) -> dict:
    """/pa 子命令分流（api 薄壳调用）。返回 {"ephemeral": str, "channel_post": str|None}
    ——channel_post 非 None = 成功后 bot 进频道发（后置 fail-open，拒绝恒 None）。"""
    text = (form.get("text") or "").strip()
    cmd, _, rest = text.partition(" ")
    cmd, arg = cmd.lower(), rest.strip()
    channel_id = form.get("channel_id") or ""
    user_id = form.get("user_id") or ""
    user_name = form.get("user_name") or ""

    if cmd == "bind":
        return _handle_bind(database=database, cfg=cfg, mm=mm,
                            channel_id=channel_id, user_name=user_name, arg=arg)
    if cmd == "unbind":
        return _handle_unbind(database=database, cfg=cfg,
                              channel_id=channel_id, user_name=user_name)
    if cmd == "run":
        return _handle_run(database=database, engine=engine, cfg=cfg, mm=mm,
                           channel_id=channel_id, user_id=user_id, user_name=user_name,
                           body=arg, member_cache=member_cache, now_utc=now_utc)
    if cmd == "confirm":
        return _handle_confirm(database=database, engine=engine, cfg=cfg,
                               user_id=user_id, arg=arg, now_utc=now_utc)
    if cmd == "reject":
        return _handle_reject(database=database, user_id=user_id, arg=arg)
    if cmd == "metrics":
        return _handle_metrics(database=database, engine=engine, cfg=cfg,
                               user_id=user_id, arg=arg, now_utc=now_utc)
    return _ephemeral(_USAGE)  # 空/未知子命令：用法清单（教育回执）


def _handle_run(*, database, engine, cfg, mm, channel_id: str, user_id: str,
                user_name: str, body: str, member_cache, now_utc) -> dict:
    binding = database.get_channel_binding_by_channel(channel_id)
    if binding is None:
        names = "、".join(readable_projects(cfg.data_root)) or "（暂无——先在 data_root 建项目必读面）"
        return _ephemeral(f"本频道未绑定项目——请 admin 在本频道执行 `/pa bind <project>`（可绑定：{names}）")

    verdict = check_membership(mm, channel_id, user_id, member_cache)
    if verdict == CHANNEL_MISSING:
        return _ephemeral("频道不存在（绑定表指向已删除频道）——请 admin 检查绑定：`/pa unbind` 后重新 bind")
    if verdict == UNKNOWN:
        # fail-closed（D3）：UNKNOWN 绝不当授权通过，也绝不回执「非成员」（403 是
        # hub 侧通道故障，非发起人身份结论）
        return _ephemeral("授权校验暂不可用（MM 侧不可达或 bot 无权限）——稍后重试；持续出现请联系管理员检查 bot")
    if verdict == NOT_MEMBER:
        return _ephemeral("你不是本频道成员——无提交权限（频道成员即授权；入队权限随频道成员管理）")

    if not body:
        return _ephemeral("缺需求正文——例：`/pa run 修复登录超时问题 出处: https://issues.example/123`")
    if not has_evidence(body):
        return _ephemeral(
            "缺出处——正文须含 `出处: <链接或说明>`（半/全角冒号均可）。\n"
            "例：`/pa run 修复登录超时问题 出处: https://issues.example/123`"
        )
    if len(body.encode("utf-8")) > MAX_TEXT_BYTES:
        return _ephemeral("需求正文超长（1MB 上限）——长需求请走面板提交（不截断：截断的 PRD 是语义损坏）")

    project = binding["project"]
    try:
        evidence = extract_evidence(body)
        wrapped = wrap_mm_prd(project, body, user_id, user_name, channel_id, evidence)
    except ValueError as e:
        return _ephemeral(str(e))
    sha = hashlib.sha256(wrapped.encode("utf-8")).hexdigest()
    key = deliver_key_for(project, user_id, wrapped, now_utc)

    existing = database.get_item_by_deliver_key(key)
    if existing is not None:
        # 同小时窗重试幂等回执（D5）——改写措辞即绕过（有意重发不被拦）
        return _ephemeral(f"近期同内容已入队：#{existing['id']}（{project}，一小时内重复请求幂等）——如需重发请调整措辞或稍后再试")

    try:
        target = prd_store.store_prd(
            cfg.data_root, project, f"mm-{sha[:8]}.md", wrapped,
            declared_sha=sha, overwrite=False,
        )
    except prd_store.PrdStoreError as e:
        return _ephemeral(f"落盘失败：{e}——同内容换措辞重试或走面板")
    try:
        item_id = engine.enqueue(project, str(target), user_id, deliver_key=key)
    except sqlite3.IntegrityError:
        winner = database.get_item_by_deliver_key(key)  # 并发撞键 db 层兜底（/deliver 先例）
        if winner is None:
            return _ephemeral("入队状态不一致（撞键后查无既有项）——请重试或走面板")
        return _ephemeral(f"近期同内容已入队：#{winner['id']}（{project}，一小时内重复请求幂等）")

    first_line = " ".join(body.splitlines()[0].split())[:80]
    return _ephemeral(
        f"已入队 #{item_id}（{project}）——run 终态与本频道同步",
        channel_post=f"<@{user_id}> 已入队 #{item_id}（{project}）：{first_line}",
    )


def _require_bind_admin(cfg, user_name: str) -> str | None:
    """bind/unbind 管理面白名单（D3 双轨分立）：仅 HUB_PANEL_ADMINS 判据，不串联
    频道成员校验。空白名单 = fail-closed 全拒（与面板分立：面板空 = OAuth 会话
    回退态放行——slash 无会话判据，空 = 无任何附加判据，全拒防误开放）。"""
    if not cfg.panel_admins:
        return "bind 未开放：HUB_PANEL_ADMINS 未配置（slash 通道空白名单 = 全拒）——配置白名单后可用"
    if user_name not in cfg.panel_admins:
        return f"仅管理员可执行（HUB_PANEL_ADMINS 白名单外：{user_name}）"
    return None


def _handle_bind(*, database, cfg, mm, channel_id: str, user_name: str, arg: str) -> dict:
    denied = _require_bind_admin(cfg, user_name)
    if denied:
        return _ephemeral(denied)
    project = arg.split()[0] if arg else ""
    if not project or not PROJECT_RE.fullmatch(project):
        return _ephemeral(f"project 名非法（须匹配 {PROJECT_RE.pattern}）——用法：`/pa bind <project>`")
    names = readable_projects(cfg.data_root)
    if project not in names:
        return _ephemeral(f"project {project!r} 不在可绑定清单（必读面扫描未命中）——可绑定：{'、'.join(names) or '（无）'}")
    if not channel_id or not MM_CHANNEL_ID_RE.fullmatch(channel_id):
        return _ephemeral("channel_id 形态非法（须 MM 26 字符频道 id）——防回归判据，请经 MM slash command 发起")

    # bot 可达校验（D3 部署前提：bot 须已入频道，否则后续成员查询 403）
    if mm is None:
        return _ephemeral("通知 bot 未配置（HUB_MM_BOT_TOKEN + HUB_MM_BASE_URL）——bind 需 bot 先进频道")
    try:
        ids = mm.channel_member_ids(channel_id)
        me = mm.me_id() if ids is not None else None
    except MMChannelNotFound:
        return _ephemeral("频道不存在（MM 侧查无此频道）——请在本频道内执行 bind")
    except Exception as e:  # noqa: BLE001——bot 探活异常归不可达
        print(f"[mm-intake][warn] bind 探活异常: {type(e).__name__}", file=sys.stderr)
        ids, me = None, None
    if ids is None or me is None or me not in ids:
        return _ephemeral("bot 不在本频道（或 bot 侧不可达）——先把 bot 拉进本频道，再执行 `/pa bind`")

    replaced = database.upsert_bind(project, channel_id, user_name)
    lines = [f"已绑定：{project} ↔ 本频道（by {user_name}）"]
    for old in replaced:
        lines.append(f"替换旧绑定：{old['project']} ↔ {old['channel_id']}（已解除——通知按当前绑定）")
    return _ephemeral("\n".join(lines))


def _handle_unbind(*, database, cfg, channel_id: str, user_name: str) -> dict:
    denied = _require_bind_admin(cfg, user_name)
    if denied:
        return _ephemeral(denied)
    row = database.unbind_by_channel(channel_id)
    if row is None:
        return _ephemeral("本频道未绑定项目（无需解绑）——`/pa bind <project>` 可绑定")
    return _ephemeral(f"已解绑：{row['project']} ↔ 本频道——入站将拒绝、出站通知回落私信")


# ── 确认环三子命令（add-acceptance-spec-confirmation 2.3，spec: confirm/reject/metrics）──
# 共同判据链：行存在 → 发起人 user_id 相等 → requested 态（终态幂等回执）。发起人
# 判据走行内 initiator（事实判据，不随频道成员/绑定漂移）；不做频道成员校验。

def _parse_rid(arg: str) -> int | None:
    """`#77` / `77` → 77；非法/空 → None（# 前缀容忍——spec 写法 `#<id>`）。"""
    s = arg.split()[0].lstrip("#") if arg.split() else ""
    return int(s) if s.isdigit() else None


def _confirmation_gate(database, rid: int | None, user_id: str) -> tuple[dict | None, dict | None]:
    """共享前置闸：行查无 / 非发起人 / 非 requested（终态幂等回执）。

    返回 (row, denial)——denial 非 None 即调用方直接回执；row 仅在 requested 且
    发起人本人时非 None。
    """
    if rid is None:
        return None, _ephemeral("缺行 id——用法：`/pa confirm #<行id>`（行 id 见指标清单通知）")
    row = database.get_confirmation(rid)
    if row is None:
        return None, _ephemeral(f"确认行 #{rid} 不存在——行 id 见指标清单通知")
    if row["initiator"] != user_id:
        return None, _ephemeral(f"确认行 #{rid} 仅发起人本人可操作")
    if row["status"] != "requested":
        label = {"confirmed": "已确认", "rejected": "已放弃", "expired": "已过期"}.get(
            row["status"], row["status"])
        return None, _ephemeral(f"确认行 #{rid} {label}——无需重复处理（幂等回执）")
    return row, None


def _reenqueue_guard(database, row: dict) -> dict | None:
    """D5 祖先链重入队上界：同 origin_sha 已 confirmed 达 3 次 → 回执人工改写。"""
    if database.count_confirm_origin(row["origin_sha"]) >= CONFIRM_REENQUEUE_MAX:
        return _ephemeral(
            f"同一需求的重拟次数已达上界（{CONFIRM_REENQUEUE_MAX} 次）——"
            "请人工改写需求后重新提交（`/pa run <改写后的需求> 出处: ...`）")
    return None


def _metrics_section(metrics: list) -> str:
    """指标清单 →「验收指标（发起人确认）」节（机械并入，id 前缀保留供 test-author
    criterion 对位；proxy 逐条标注；id=infra 的基建缺口尾注条目不是指标，跳过）。"""
    lines = ["## 验收指标（发起人确认）", ""]
    for m in metrics:
        if not isinstance(m, dict) or m.get("id") == "infra":
            continue
        tag = "〔代理指标〕" if m.get("coverage") == "proxy" else ""
        lines.append(f"- [ ] {m.get('id')}: {m.get('metric')}{tag}")
    return "\n".join(lines)


def _store_and_enqueue(database, engine, cfg, project: str, user_id: str,
                       merged: str, now_utc) -> tuple[int | None, str | None]:
    """confirm/metrics 共用入队尾段（复用 run 面路径：新 sha 新文件名、deliver_key
    小时窗幂等、落盘/入队异常转拒绝回执）。返回 (item_id, denial)。"""
    sha = hashlib.sha256(merged.encode("utf-8")).hexdigest()
    key = deliver_key_for(project, user_id, merged, now_utc)
    existing = database.get_item_by_deliver_key(key)
    if existing is not None:  # 同窗重试幂等（改写措辞即绕过——有意）
        return existing["id"], None
    try:
        target = prd_store.store_prd(
            cfg.data_root, project, f"mm-{sha[:8]}.md", merged,
            declared_sha=sha, overwrite=False,
        )
    except prd_store.PrdStoreError as e:
        return None, _ephemeral(f"落盘失败：{e}——请重试或走面板")
    try:
        return engine.enqueue(project, str(target), user_id, deliver_key=key), None
    except sqlite3.IntegrityError:
        winner = database.get_item_by_deliver_key(key)  # 并发撞键 db 层兜底（run 面先例）
        if winner is None:
            return None, _ephemeral("入队状态不一致（撞键后查无既有项）——请重试")
        return winner["id"], None


def _decide(database, rid: int, new_status: str) -> None:
    """置态（requested 已在闸内验过；ValueError 防御转静默——单线程内不期望出现）。"""
    try:
        database.decide_confirmation(rid, new_status)
    except ValueError as e:
        print(f"[mm-intake][warn] 确认行 #{rid} 置态异常: {e}", file=sys.stderr)


def _handle_confirm(*, database, engine, cfg, user_id: str, arg: str,
                    now_utc) -> dict:
    rid = _parse_rid(arg)
    row, denial = _confirmation_gate(database, rid, user_id)
    if denial:
        return denial
    capped = _reenqueue_guard(database, row)
    if capped:
        return capped
    binding = database.get_channel_binding_by_project(row["project"])
    if binding is None:  # spec：行保持 requested，绑定恢复后可再 confirm
        return _ephemeral(f"项目 {row['project']} 当前未绑定频道——确认行 #{rid} 保持待确认，绑定恢复后可再拍板")
    try:
        metrics = json.loads(row["metrics_json"] or "[]")
    except ValueError:
        metrics = []
    merged = row["prd_text"].rstrip() + "\n\n" + _metrics_section(metrics) + "\n"
    item_id, failure = _store_and_enqueue(database, engine, cfg, row["project"],
                                          user_id, merged, now_utc)
    if failure:
        return failure
    _decide(database, rid, "confirmed")
    return _ephemeral(
        f"已拍板入队 #{item_id}（{row['project']}）——指标并入 PRD，run 终态与本频道同步",
        channel_post=f"<@{user_id}> 已确认指标并入 PRD：#{item_id}（{row['project']}，确认行 #{rid}）",
    )


def _handle_reject(*, database, user_id: str, arg: str) -> dict:
    rid = _parse_rid(arg)
    row, denial = _confirmation_gate(database, rid, user_id)
    if denial:
        return denial
    _decide(database, rid, "rejected")
    return _ephemeral(
        f"已放弃确认行 #{rid} 的指标清单（需求未入队——可 `/pa run` 改写后重提）",
        channel_post=f"<@{user_id}> 放弃了确认行 #{rid} 的指标清单（{row['project']}）",
    )


def _handle_metrics(*, database, engine, cfg, user_id: str, arg: str,
                    now_utc) -> dict:
    parts = arg.split(None, 1)
    rid = _parse_rid(parts[0]) if parts else None
    extra = parts[1].strip() if len(parts) > 1 else ""
    if rid is None or not extra:
        return _ephemeral("用法：`/pa metrics #<行id> <补充说明>`——补充上下文后重新拟指标")
    row, denial = _confirmation_gate(database, rid, user_id)
    if denial:
        return denial
    capped = _reenqueue_guard(database, row)
    if capped:
        return capped
    binding = database.get_channel_binding_by_project(row["project"])
    if binding is None:  # 与 confirm 同判据：未绑定不入队，行保持 requested
        return _ephemeral(f"项目 {row['project']} 当前未绑定频道——确认行 #{rid} 保持待确认，绑定恢复后可再补充")
    merged = row["prd_text"].rstrip() + f"\n\n## 发起人补充\n\n{extra}\n"
    item_id, failure = _store_and_enqueue(database, engine, cfg, row["project"],
                                          user_id, merged, now_utc)
    if failure:
        return failure
    # 置 confirmed（非语义上的「拍板」）：D5 计数面 = COUNT(status='confirmed')，
    # metrics 重拟与 confirm 同计数才能让 3 次上界覆盖两种重入队路径
    _decide(database, rid, "confirmed")
    return _ephemeral(
        f"已带补充重新入队 #{item_id}（{row['project']}）——新指标清单拟好后将再次通知你确认",
        channel_post=f"<@{user_id}> 补充了需求上下文，重新入队：#{item_id}（{row['project']}，源确认行 #{rid}）",
    )
