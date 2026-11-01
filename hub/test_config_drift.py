"""config_drift.py 单测——六层漂移看门狗 R1-R4（add-config-drift-watchdog 5.1，spec: hub-config-drift-watchdog）。

fake 注入（FakeCfg/FakeVault/FakeDB/FakeNotifier/FakeGateway + monkeypatch subprocess.run）：
零网络零 docker；值不入面断言（键名/长度面）。紧凑写法是仓库既有 test 风格（ruff 仅 E9+F）。
add-drift-l5-health-probe 增量：R3 消费面探活（_probe fake 与 _gw 列表 fake 分别注入）——
确定性死链（404/400）→漂移 / 瞬时（0/5xx/429/40x 鉴权）→UNKNOWN / 可用 2xx 零报 / 与 membership 共存。
"""
from __future__ import annotations

import subprocess
import types
from pathlib import Path

import config_drift
import vault_store


def _mk_cfg(*, intent=None, base="http://gw:4000", token="t" * 40, model=None, user_id="rocju"):
    cfg = types.SimpleNamespace(
        user_id=user_id, anthropic_base_url=base, anthropic_auth_token=token,
        user_model=model, drift_env_intent=intent,
        user_env=lambda: {
            "ANTHROPIC_BASE_URL": base, "ANTHROPIC_AUTH_TOKEN": token, "GH_TOKEN": "g" * 40,
            **({"ANTHROPIC_MODEL": model} if model else {}),
        },
    )
    return cfg


class FakeVault:
    def __init__(self, rec=None, error=False):
        self.rec, self.error = rec, error

    def get_user_env(self, user):
        if self.error:
            raise vault_store.VaultError("解密失败：master key 不匹配（模拟）")
        return self.rec


class FakeDB:
    def __init__(self, runs):
        self.runs = runs

    def runs_in_status(self, status):
        assert status == "running"
        return self.runs


class FakeNotifier:
    def __init__(self):
        self.sent: list[str] = []

    def notify_admin(self, text):
        self.sent.append(text)


def _gw(status=200, ids=()):
    return lambda url, token: (status, list(ids))


def _probe(status=200):
    """探活 fake（add-drift-l5-health-probe）：缺省 2xx=可用，保既有测试密闭零网络。"""
    return lambda url, token, model: status


def _run_check(cfg, vault=None, dbm=None, seen=None, notifier=None, http=None, probe=None):
    seen = set() if seen is None else seen
    config_drift.check_drift(cfg, vault, dbm or FakeDB([]), seen=seen,
                             notifier=notifier, http_client=http or _gw(200, []),
                             probe_client=probe or _probe(200))
    return seen


# ── R1：L3 意图 vs 快照 ──────────────────────────────────────────

def test_r1_value_drift_and_missing_key(tmp_path, monkeypatch, capsys):
    # 值漂移断言用非模型键（模型名会被 R3 合法入面——值不入面纪律管凭据值）
    intent = tmp_path / "hub.env"
    monkeypatch.setenv("HUB_TUNNEL_KEY", "x" * 8)
    intent.write_text(f"# 注释行\nHUB_TUNNEL_KEY={'y' * 6}\nPA_NEW_KEY=v1\n\nBADLINE\n", encoding="utf-8")
    n = FakeNotifier()
    _run_check(_mk_cfg(intent=intent), notifier=n)
    err = capsys.readouterr().err
    assert "HUB_TUNNEL_KEY" in err and "len: 进程 8 vs 盘上 6" in err  # 值漂移（长度面）
    assert "y" * 6 not in err and "x" * 8 not in err                # 凭据值绝不入面
    assert "PA_NEW_KEY" in err and "缺键" in err                    # 盘上有进程无
    assert "docker compose up -d" in err                            # 修复指引
    assert "坏行" in err and ":L5" in err                          # 坏行 warn 记行号（不回显内容）
    assert len(n.sent) == 2                                         # 两漂移项各一私信
    assert "y" * 6 not in "".join(n.sent)


def test_r1_off_and_unreadable(tmp_path, capsys):
    n = FakeNotifier()
    _run_check(_mk_cfg(intent=None), notifier=n)
    assert "R1 关闭" in capsys.readouterr().err and not n.sent       # 未配 = 关闭零漂移
    d = tmp_path / "d"
    d.mkdir()
    intent = d / "hub.env"
    intent.write_text("K=V", encoding="utf-8")
    orig = Path.read_text

    def _denied(self, *a, **k):
        if self.name == "hub.env":
            raise PermissionError(13, "Permission denied")  # bind mount 属主未对齐形态
        return orig(self, *a, **k)

    Path.read_text = _denied
    try:
        _run_check(_mk_cfg(intent=intent), notifier=n)
    finally:
        Path.read_text = orig
    err = capsys.readouterr().err
    assert "降级（intent 不可读）" in err and "chmod 0640" in err     # 一次性前提告警含对齐修法
    assert len(n.sent) == 1                                          # 前提告警进 MM（一次性）


# ── R2：L4 vs L3 兜底源 ──────────────────────────────────────────

def test_r2_cred_snapshot_drift_keyface_only(capsys):
    old = {"ANTHROPIC_BASE_URL": "http://old:4000", "ANTHROPIC_AUTH_TOKEN": "t" * 40, "GH_TOKEN": "g" * 40}
    n = FakeNotifier()
    _run_check(_mk_cfg(), vault=FakeVault(old), notifier=n, http=_gw(200, []))
    err = capsys.readouterr().err
    assert "ANTHROPIC_BASE_URL" in err and "vault 旧快照" in err
    assert "vault-import --from-env" in err and "http://old:4000" not in err  # 值不入面
    assert len(n.sent) == 1


def test_r2_model_key_info_and_singleside_and_states(capsys):
    rec = {"ANTHROPIC_BASE_URL": "http://gw:4000", "ANTHROPIC_AUTH_TOKEN": "t" * 40,
           "GH_TOKEN": "g" * 40, "GITLAB_TOKEN": "glpat-x" * 4, "ANTHROPIC_MODEL": "m-old"}
    n = FakeNotifier()
    _run_check(_mk_cfg(model="m-new"), vault=FakeVault(rec), notifier=n, http=_gw(200, ["m-old"]))
    err = capsys.readouterr().err
    assert "模型键分歧" in err and "覆盖" in err          # 信息项：中性口径 + merge 覆盖警示
    assert not n.sent                                     # 信息项不进 MM
    assert "GITLAB_TOKEN" not in err                      # 单侧独有键不报（只增不减 by-design）
    # 无记录 = 信息项；vault None = R2 关闭零报
    _run_check(_mk_cfg(), vault=FakeVault(None), notifier=n)
    assert "无 user=rocju 记录" in capsys.readouterr().err
    _run_check(_mk_cfg(), vault=None, notifier=n, http=_gw(200, ["m-new"]))
    assert "R2=关闭" in capsys.readouterr().err


# ── R3：L5 membership + alias 预检 ───────────────────────────────

def test_r3_membership_case_sensitive_and_alias(monkeypatch, capsys):
    monkeypatch.setenv("PA_DEV_MODEL", "qwen3.8-27b-fast")
    monkeypatch.setenv("PA_PERSONA_MODEL_PA_PRD", "sonnet")  # alias 形态（run 93 类）
    cfg = _mk_cfg(model="Qwen3.8-27B")                       # 派发源（大小写分家的注册侧名）
    n = FakeNotifier()
    _run_check(cfg, notifier=n, http=_gw(200, ["Qwen3.8-27B"]))  # 网关只注册大写名
    err = capsys.readouterr().err
    assert "qwen3.8-27b-fast" in err and "L5注册→消费面" in err   # 精确匹配缺名 = 漂移
    assert "sonnet" in err and "alias" in err                     # alias 负向预检 = 漂移
    assert "/model/new" in err and "LITELLM_MASTER_KEY" in err    # 修复指引


def test_r3_unknown_two_states_and_degrade_once(monkeypatch, capsys):
    monkeypatch.setenv("PA_DEV_MODEL", "m1")
    _run_check(_mk_cfg(), http=_gw(0))                        # 网络错 → UNKNOWN 仅日志不进漂移
    assert "UNKNOWN（网络/5xx）" in capsys.readouterr().err
    seen = set()
    _run_check(_mk_cfg(), seen=seen, http=_gw(403))           # 403 → UNKNOWN + 降级提示
    assert "membership 断言降级" in capsys.readouterr().err
    _run_check(_mk_cfg(), seen=seen, http=_gw(403))           # 同 seen 第二轮：去抖不重发
    assert capsys.readouterr().err.count("membership 断言降级") == 0


# ── R3 探活（add-drift-l5-health-probe，审核 P1-1：名册在册≠上游可用）──

def test_r3_probe_deterministic_dead_link_and_dedup(monkeypatch, capsys):
    monkeypatch.setenv("PA_DEV_MODEL", "m404")
    monkeypatch.setenv("PA_PERSONA_MODEL_PA_PRD", "m400")
    cfg, n = _mk_cfg(), FakeNotifier()
    http = _gw(200, ["m404", "m400"])                          # 两名都在册（membership 全过）
    probe = lambda u, t, m: {"m404": 404, "m400": 400}[m]      # 404/400 确定性闭合集两形态
    seen = _run_check(cfg, notifier=n, http=http, probe=probe)
    err = capsys.readouterr().err
    assert "m404（探活）" in err and "HTTP 404" in err and "L5名册→L5可用" in err  # 在册死链 = 漂移（键面带形态标记）
    assert "m400（探活）" in err and "HTTP 400" in err                            # 400 同属确定性集
    assert "名册在册≠上游可达" in err and "qwen3.6-35b" in err                      # 事故形态口径
    assert "/model/update" in err and "/model/delete" in err                      # 修复指引（修上游/下线）
    assert len(n.sent) == 2 and all("Bearer" not in s for s in n.sent)            # 各一私信；token 不入面
    _run_check(cfg, seen=seen, notifier=n, http=http, probe=probe)
    assert len(n.sent) == 2 and "本轮新报 0" in capsys.readouterr().err           # 复用 drift 键去抖不重发


def test_r3_probe_key_independent_from_membership_on_delete(monkeypatch, capsys):
    # 冷评审 R2：死链→按指引 /model/delete→缺名，无消解轮隔开——缺名告警不得被探活同键吞掉
    monkeypatch.setenv("PA_DEV_MODEL", "m-dead")
    cfg, n = _mk_cfg(), FakeNotifier()
    seen = _run_check(cfg, notifier=n, http=_gw(200, ["m-dead"]), probe=_probe(404))
    assert len(n.sent) == 1 and "m-dead（探活）" in capsys.readouterr().err        # 轮 N：死链告警入 seen
    _run_check(cfg, seen=seen, notifier=n, http=_gw(200, []), probe=_probe(200))  # 删注册后名册缺名（无零漂移轮）
    err = capsys.readouterr().err
    assert "m-dead" in err and "L5注册→消费面" in err and len(n.sent) == 2         # 缺名漂移照报（键身份独立）
    assert "已消解" not in err                                                    # 漂移非空不消解（语义自洽）


def test_r3_probe_transient_unknown_no_drift(monkeypatch, capsys):
    monkeypatch.setenv("PA_DEV_MODEL", "m1")
    n = FakeNotifier()
    for code in (0, 500, 429, 403):                            # 网络/5xx/限流/鉴权 → 全 UNKNOWN
        _run_check(_mk_cfg(), notifier=n, http=_gw(200, ["m1"]), probe=_probe(code))
    err = capsys.readouterr().err
    assert "L5名册→L5可用" not in err and not n.sent           # 绝不进漂移不进 MM
    assert err.count("探活 UNKNOWN×1") == 4                    # 心跳可见（stderr）但不告警


def test_r3_probe_available_zero_report(monkeypatch, capsys):
    monkeypatch.setenv("PA_DEV_MODEL", "m1")
    n = FakeNotifier()
    _run_check(_mk_cfg(), notifier=n, http=_gw(200, ["m1"]), probe=_probe(200))
    err = capsys.readouterr().err
    assert "R3=ok" in err and "UNKNOWN" not in err             # 2xx 可用 → 零报零标注
    assert "漂移 0 项" in err and not n.sent


def test_r3_probe_coexists_with_membership_no_crossfire(monkeypatch, capsys):
    monkeypatch.setenv("PA_DEV_MODEL", "m-dead")               # 在册（探活 404 死链）
    monkeypatch.setenv("PA_PERSONA_MODEL_PA_PRD", "m-missing")  # 缺名（membership 漂移）
    calls: list[str] = []

    def probe(url, token, model):
        calls.append(model)
        return 404

    n = FakeNotifier()
    _run_check(_mk_cfg(), notifier=n, http=_gw(200, ["m-dead"]), probe=probe)
    err = capsys.readouterr().err
    assert "m-missing" in err and "L5注册→消费面" in err        # 缺名漂移照常报
    assert "m-dead" in err and "L5名册→L5可用" in err           # 在册死链漂移同轮共存
    assert calls == ["m-dead"]                                 # 缺名者不探活（成本面+判据互斥不连坐）
    assert len(n.sent) == 2                                    # 两漂移项各一私信


# ── R4：L6 在场 + 形态 ──────────────────────────────────────────

def _fake_exec(monkeypatch, stdout="", rc=0, raise_exc=None):
    def _run(argv, **k):
        if raise_exc:
            raise raise_exc
        return subprocess.CompletedProcess(argv, rc, stdout, stderr="")
    monkeypatch.setattr(config_drift.subprocess, "run", _run)


def test_r4_present_and_shape(monkeypatch, capsys):
    dbm = FakeDB([{"id": 9, "container_ref": "c1", "runner_docker_host": None}])
    _fake_exec(monkeypatch, stdout=f"ANTHROPIC_BASE_URL=http://gw:4000\nANTHROPIC_AUTH_TOKEN={'t'*40}\n")
    _run_check(_mk_cfg(), dbm=dbm, http=_gw(200, []))
    assert "R4=ok" in capsys.readouterr().err                    # 在场 + 形态合法零漂移
    _fake_exec(monkeypatch, stdout="ANTHROPIC_AUTH_TOKEN=t\n")   # 缺键 + len=1 占位形态
    _run_check(_mk_cfg(), dbm=dbm, http=_gw(200, []))
    err = capsys.readouterr().err
    assert "R4" in err and "注入链断裂" in err and "ANTHROPIC_BASE_URL" in err


def test_r4_skip_cases(monkeypatch, capsys):
    _run_check(_mk_cfg(), dbm=FakeDB([]), http=_gw(200, []))     # 无 running
    assert "R4=无 running" in capsys.readouterr().err
    dbm = FakeDB([{"id": 1, "container_ref": "c", "runner_docker_host": "tcp://x:2375"}])
    _fake_exec(monkeypatch, rc=1)
    _run_check(_mk_cfg(), dbm=dbm, http=_gw(200, []))
    assert "跳过" in capsys.readouterr().err                      # exec 非零 → 跳过不误报
    _fake_exec(monkeypatch, raise_exc=subprocess.TimeoutExpired("cmd", 1))
    _run_check(_mk_cfg(), dbm=dbm, http=_gw(200, []))
    assert "exec 失败" in capsys.readouterr().err


# ── 去抖 / 消解 / 隔离 ─────────────────────────────────────────

def test_dedup_once_and_resolution_receipt(tmp_path, monkeypatch, capsys):
    intent = tmp_path / "hub.env"
    monkeypatch.setenv("PA_DEV_MODEL", "x" * 8)
    intent.write_text("PA_DEV_MODEL=" + "y" * 6 + "\n", encoding="utf-8")
    cfg, n, seen = _mk_cfg(intent=intent), FakeNotifier(), set()
    gw, pgw = _gw(200, ["x" * 8, "y" * 6]), _probe(200)  # 网关两侧名都注册（隔离出 R1 单一漂移源）
    config_drift.check_drift(cfg, None, FakeDB([]), seen=seen, notifier=n,
                             http_client=gw, probe_client=pgw)
    assert len(n.sent) == 1
    config_drift.check_drift(cfg, None, FakeDB([]), seen=seen, notifier=n,
                             http_client=gw, probe_client=pgw)
    assert len(n.sent) == 1                                       # 同项去抖不重发
    assert "本轮新报 0" in capsys.readouterr().err
    monkeypatch.setenv("PA_DEV_MODEL", "y" * 6)                   # 修复（两侧一致）
    config_drift.check_drift(cfg, None, FakeDB([]), seen=seen, notifier=n,
                             http_client=gw, probe_client=pgw)
    err = capsys.readouterr().err
    assert "已消解（1 项）" in err and len(n.sent) == 2           # 消解回执一次
    monkeypatch.setenv("PA_DEV_MODEL", "x" * 8)                   # 复发可再告警
    config_drift.check_drift(cfg, None, FakeDB([]), seen=seen, notifier=n,
                             http_client=gw, probe_client=pgw)
    assert len(n.sent) == 3


def test_rule_isolation(monkeypatch, capsys):
    def boom(path):
        raise RuntimeError("IO 抽风（模拟）")
    monkeypatch.setattr(config_drift, "_parse_intent", boom)
    cfg = _mk_cfg(intent="/tmp/whatever")
    n = FakeNotifier()
    _run_check(cfg, vault=FakeVault(None), notifier=n, http=_gw(200, []))
    err = capsys.readouterr().err
    assert "R1 异常（不连坐）" in err          # R1 挂了
    assert "R2=无记录" in err                   # R2 照常执行


def test_heartbeat_always_and_no_notifier_degrades(capsys):
    _run_check(_mk_cfg(), vault=None, notifier=None, http=_gw(200, []))
    err = capsys.readouterr().err
    assert "[drift] 心跳" in err and "R1=关闭" in err   # 未配 bot 全降级 stderr（可见的丢）


# ── add-drift-l5-health-probe 补强：连续失败升级（真死链 500 族闭环） ──

def _r3_env(monkeypatch, model):
    monkeypatch.setenv("PA_DEV_MODEL", model)


def test_probe_streak_escalation_and_recovery(monkeypatch, capsys):
    cfg = _mk_cfg()  # 既有 helper：base/token
    _r3_env(monkeypatch, "dead-model")
    gw = _gw(200, ["dead-model"])          # 名册在册
    n_call = {"i": 0}
    def probe(url, token, model):
        n_call["i"] += 1
        return n_call["code"]
    seen = set()
    n = FakeNotifier()
    def run(code):
        n_call["code"] = code
        config_drift.check_drift(cfg, None, FakeDB([]), seen=seen, notifier=n, http_client=gw, probe_client=probe)
    run(500); run(500)                     # 连续两轮 UNKNOWN：不告警（零误报）
    assert len(n.sent) == 0
    run(500)                               # 第三轮：升级漂移
    assert len(n.sent) == 1 and "连续" in n.sent[0] and "dead-model" in n.sent[0]
    err = capsys.readouterr().err
    assert "连续 3 轮" in err and "死链形态" in err
    run(500)                               # 第四轮：漂移键去抖不重发
    assert len(n.sent) == 1
    run(200)                               # 恢复：连败清零 + 消解回执
    err2 = capsys.readouterr().err
    assert "已消解" in err2
    run(500)                               # 恢复后再失败：从 1 重新计数
    assert len(n.sent) == 2  # 仅消解回执，无新漂移
    assert capsys.readouterr().err.count("连续") == 0


def test_probe_deterministic_dead_immediate(monkeypatch):
    cfg = _mk_cfg()
    _r3_env(monkeypatch, "dead-model")
    n = FakeNotifier()
    seen = set()
    config_drift.check_drift(cfg, None, FakeDB([]), seen=seen, notifier=n,
                             http_client=_gw(200, ["dead-model"]),
                             probe_client=lambda u, t, m: 404)
    assert len(n.sent) == 1 and "确定性拒绝" in n.sent[0]  # 404 单发即漂移（不等待连败）
