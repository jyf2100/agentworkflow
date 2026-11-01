"""config_drift.py 单测——六层漂移看门狗 R1-R4（add-config-drift-watchdog 5.1，spec: hub-config-drift-watchdog）。

fake 注入（FakeCfg/FakeVault/FakeDB/FakeNotifier/FakeGateway + monkeypatch subprocess.run）：
零网络零 docker；值不入面断言（键名/长度面）。紧凑写法是仓库既有 test 风格（ruff 仅 E9+F）。
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


def _run_check(cfg, vault=None, dbm=None, seen=None, notifier=None, http=None):
    seen = set() if seen is None else seen
    config_drift.check_drift(cfg, vault, dbm or FakeDB([]), seen=seen,
                             notifier=notifier, http_client=http or _gw(200, []))
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
    gw = _gw(200, ["x" * 8, "y" * 6])  # 网关两侧名都注册（隔离出 R1 单一漂移源）
    config_drift.check_drift(cfg, None, FakeDB([]), seen=seen, notifier=n, http_client=gw)
    assert len(n.sent) == 1
    config_drift.check_drift(cfg, None, FakeDB([]), seen=seen, notifier=n, http_client=gw)
    assert len(n.sent) == 1                                       # 同项去抖不重发
    assert "本轮新报 0" in capsys.readouterr().err
    monkeypatch.setenv("PA_DEV_MODEL", "y" * 6)                   # 修复（两侧一致）
    config_drift.check_drift(cfg, None, FakeDB([]), seen=seen, notifier=n, http_client=gw)
    err = capsys.readouterr().err
    assert "已消解（1 项）" in err and len(n.sent) == 2           # 消解回执一次
    monkeypatch.setenv("PA_DEV_MODEL", "x" * 8)                   # 复发可再告警
    config_drift.check_drift(cfg, None, FakeDB([]), seen=seen, notifier=n, http_client=gw)
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
