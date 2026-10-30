"""health_probe.py 单测——三项独立成败 / nonce 比对 / 缓存写入 / fail-open（add-hub-runner-pool 4.2）。

全部 monkeypatch subprocess.run（零 docker 依赖）——按 cmd[1] 分流三项 CLI。
"""
import json
from pathlib import Path

import config
import db as hub_db
import health_probe
import lifecycle
import queue_engine as qe
from runner import FakeRunner


def _cfg(data_root: Path) -> config.HubConfig:
    return config.load_config({
        "HUB_USER_ID": "roc",
        "HUB_USER_ANTHROPIC_BASE_URL": "https://litellm.example",
        "HUB_USER_ANTHROPIC_AUTH_TOKEN": "tok-abcdef0123456789abcdef",
        "HUB_USER_GH_TOKEN": "ghp_abcdefghij0123456789",
        "HUB_IMAGE_TAG_DEFAULT": "pa-executor:v0.1.3",
        "HUB_DATA_ROOT": str(data_root),
    })


class _R:  # fake CompletedProcess
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode = rc; self.stdout = stdout; self.stderr = stderr


def _mk(tmp_path):
    data = tmp_path / "data"; data.mkdir()
    d = hub_db.HubDB(tmp_path / "h.sqlite3")
    cfg = _cfg(data)
    lc = lifecycle.Lifecycle(d, qe.QueueEngine(d, 2, 2, "pa-executor:v0.1.3"),
                             FakeRunner(), cfg, clock=None)
    return d, cfg, lc, data


def _probe(monkeypatch, *, daemon=_R(stdout="24.0.7"), image=_R(0), mount=None):
    """注入三项 CLI 应答（mount=None → 回读 stdout 自动取 nonce 比对恒真）。捕获调用供断言。"""
    calls = []
    mount_run = mount  # 延迟：nonce 探测时才知期望 stdout

    def fake_run(cmd, **kw):
        calls.append((list(cmd), kw.get("env")))
        sub = cmd[1] if len(cmd) > 1 else ""
        if sub == "version":
            return daemon
        if sub == "image":
            return image
        if sub == "run":  # 双挂回读：-v <mount_root>/.probe-<nonce>:/probe:ro … cat /probe
            if mount_run is not None:
                return mount_run
            nonce = next(a for a in cmd if "/.probe-" in str(a)).split("/.probe-")[1].split(":")[0]
            return _R(0, stdout=nonce)  # 回读恒匹配（mount=None 语义）
        return _R(0)

    monkeypatch.setattr(health_probe.subprocess, "run", fake_run)
    return calls


def _row(d, name="gpu-a", host="tcp://10.0.0.1:2375", mount="/mnt/nfs/pa"):
    return d.register_runner(name, host, mount, 1)


def test_probe_all_green_writes_cache(tmp_path, monkeypatch):
    d, cfg, lc, data = _mk(tmp_path)
    rid = _row(d)
    calls = _probe(monkeypatch)
    detail = health_probe.probe_and_store(d, rid, cfg)
    assert detail == {"daemon": "ok", "image": "ok", "mount": "ok"}
    row = d.get_runner(rid)
    assert row["probe_ok"] == 1 and json.loads(row["probe_json"]) == detail and row["probed_at"]
    # 三项全路由到目标 daemon（DOCKER_HOST 注入）；探针文件用后即删
    assert all(env is not None and env.get("DOCKER_HOST") == "tcp://10.0.0.1:2375"
               for _, env in calls)
    assert not list(data.glob(".probe-*"))
    # 容器侧挂载路径是 runner_mount_root 投影（daemon 侧视角）
    mount_argv = next(c for c, _ in calls if c[1] == "run")
    assert any(str(a).startswith("/mnt/nfs/pa/.probe-") for a in mount_argv)


def test_probe_items_fail_independently(tmp_path, monkeypatch):
    # 单项失败不遮蔽其余项：daemon 红 + 镜像绿 + mount 红，各自注明是哪项
    d, cfg, lc, data = _mk(tmp_path)
    rid = _row(d)
    _probe(monkeypatch, daemon=_R(1, stderr="cannot connect to docker daemon"),
           image=_R(0), mount=_R(1, stderr="permission denied"))
    detail = health_probe.probe_and_store(d, rid, cfg)
    assert detail["daemon"] != "ok" and "cannot connect" in detail["daemon"]
    assert detail["image"] == "ok"
    assert detail["mount"] != "ok" and "permission denied" in detail["mount"]
    assert d.get_runner(rid)["probe_ok"] == 0  # 任一红 = 红


def test_probe_nonce_mismatch_reports_red(tmp_path, monkeypatch):
    # 回读 rc=0 但内容不匹配 = 双挂断链（两根未指同一数据面）——nonce 比对语义
    d, cfg, lc, data = _mk(tmp_path)
    rid = _row(d)
    _probe(monkeypatch, mount=_R(0, stdout="totally-other-content"))
    detail = health_probe.probe_and_store(d, rid, cfg)
    assert "回读不匹配" in detail["mount"]
    assert d.get_runner(rid)["probe_ok"] == 0


def test_probe_image_missing_red_not_unreachable(tmp_path, monkeypatch):
    # 镜像缺失 = 红（非「不可达」混报）——与派发时 image_exists 同判
    d, cfg, lc, data = _mk(tmp_path)
    rid = _row(d)
    _probe(monkeypatch, image=_R(1, stderr="No such image"))
    detail = health_probe.probe_and_store(d, rid, cfg)
    assert detail["image"] == "镜像缺失: pa-executor:v0.1.3" and detail["daemon"] == "ok"


def test_probe_enabled_runners_fail_open(tmp_path, monkeypatch, capsys):
    # 单行异常 + db 异常均吞（stderr 告警续跑）；禁用行不烧探针
    d, cfg, lc, data = _mk(tmp_path)
    _row(d, "gpu-a"); r2 = _row(d, "gpu-b", host="tcp://10.0.0.2:2375"); _row(d, "gpu-off")
    d.set_runner_enabled(r2, False)

    def boom(*a, **kw):
        # 非 OSError/TimeoutExpired：穿透 probe_runner 单项捕获 → 单行整体异常路径
        raise RuntimeError("boom")

    monkeypatch.setattr(health_probe.subprocess, "run", boom)
    health_probe.probe_enabled_runners(d, cfg)  # 不 raise（fail-open）
    assert "gpu-a" in capsys.readouterr().err
    assert d.get_runner(r2)["probe_ok"] is None  # 禁用行零探针零结论

    class BadDB:  # db 层异常同样吞
        def list_runners(self):
            raise RuntimeError("db down")
    health_probe.probe_enabled_runners(BadDB(), cfg)
    assert "fail-open" in capsys.readouterr().err


def test_probe_missing_runner_raises(tmp_path, monkeypatch):
    # 面板手动触发对不存在行要看见错误（不静默）
    d, cfg, lc, data = _mk(tmp_path)
    _probe(monkeypatch)
    try:
        health_probe.probe_and_store(d, 999, cfg); raised = False
    except KeyError:
        raised = True
    assert raised
