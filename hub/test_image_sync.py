"""image_sync 单测——流式传输核心 + 后台 worker + 触发判据（add-runner-image-sync 1.3/2.3）。

fake 子进程管道（monkeypatch image_sync.subprocess.Popen/.run）+ fake runner/db/notifier：
零真 docker、零网络。紧凑写法是仓库既有 test 风格。
"""
from __future__ import annotations

import json
import threading
import types

import image_sync
import lifecycle as lifecycle_mod
import db as db_mod


class FakeRunner:
    """image_exists 三态替身：target = present / missing / unreachable；local = 源侧有无。"""

    def __init__(self, target="missing", local=True):
        self.target, self.local = target, local

    def image_exists(self, tag, docker_host=None):
        if docker_host is None:
            return self.local
        if self.target == "present":
            return True
        if self.target == "unreachable":
            raise RuntimeError("docker daemon 不可达（≠镜像缺失）（模拟）")
        return False


class FakeDB:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.sync_writes: list[tuple] = []

    def list_runners(self):
        return self.rows

    def set_runner_image_sync(self, runner_id, ok, tag, detail):
        self.sync_writes.append((runner_id, ok, tag, detail))


class FakeNotifier:
    def __init__(self):
        self.sent: list[str] = []

    def notify_admin(self, text):
        self.sent.append(text)


class FakeProc:
    def __init__(self, rc=0, timeout=None):
        self.returncode = None
        self._rc, self._timeout = rc, timeout
        self.killed = False
        self.stdout = types.SimpleNamespace(close=lambda: None)
        self.stderr = types.SimpleNamespace(read=lambda: b"")

    def communicate(self, timeout=None):
        if self._timeout is not None:
            raise __import__("subprocess").TimeoutExpired("cmd", timeout)
        self.returncode = self._rc
        return (b"", b"")

    def wait(self, timeout=None):
        self.returncode = self._rc
        return self._rc

    def kill(self):
        self.killed = True
        self._timeout = None  # kill 后 communicate 不再超时（对齐真 subprocess 语义）


def _patch_pipe(monkeypatch, *, save_rc=0, load_rc=0, load_timeout=False, ids=("sha256:AAA", "sha256:AAA")):
    procs: dict[str, FakeProc] = {}

    def _popen(argv, **k):
        p = FakeProc(rc=load_rc if argv[1:] == ["load"] else save_rc,
                     timeout=True if (load_timeout and argv[1:] == ["load"]) else None)
        procs["load" if argv[1:] == ["load"] else "save"] = p
        return p

    def _run(argv, **k):
        if "inspect" in argv:
            which = 0 if k.get("env") is None else 1
            return types.SimpleNamespace(returncode=0, stdout=ids[which] + "\n", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(image_sync.subprocess, "Popen", _popen)
    monkeypatch.setattr(image_sync.subprocess, "run", _run)
    return procs


def _mk_worker(runner=None, dbm=None, notifier=None):
    cfg = types.SimpleNamespace(image_sync_timeout=900)
    return image_sync.ImageSyncWorker(runner or FakeRunner(), cfg, dbm or FakeDB(),
                                      notifier=notifier)


# ── 传输核心（结果 (ok, detail) 返回不抛） ───────────────────────

def test_target_present_noop():
    w = _mk_worker(runner=FakeRunner(target="present"))
    ok, detail = w.sync_image("t:1", "tcp://x:2375")
    assert ok and "no-op" in detail


def test_transfer_success_id_match(monkeypatch):
    _patch_pipe(monkeypatch)
    w = _mk_worker()
    ok, detail = w.sync_image("t:1", "tcp://x:2375")
    assert ok and "ID 一致" in detail and "sha256:AAA" in detail


def test_id_mismatch_deletes_target(monkeypatch):
    rmi_calls: list[list[str]] = []

    def _popen(argv, **k):
        return FakeProc()

    def _run(argv, **k):
        if "inspect" in argv:
            which = 0 if k.get("env") is None else 1
            return types.SimpleNamespace(returncode=0, stdout=("sha256:A", "sha256:B")[which] + "\n", stderr="")
        rmi_calls.append(argv)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(image_sync.subprocess, "Popen", _popen)
    monkeypatch.setattr(image_sync.subprocess, "run", _run)
    w = _mk_worker()
    ok, detail = w.sync_image("t:1", "tcp://x:2375")
    assert not ok and "不一致" in detail and "已删" in detail
    assert any("rmi" in a for a in rmi_calls)  # 目标侧 tag 删除（防污染派发面）


def test_timeout_kills_both_ends(monkeypatch):
    procs = _patch_pipe(monkeypatch, load_timeout=True)
    w = _mk_worker()
    ok, detail = w.sync_image("t:1", "tcp://x:2375")
    assert not ok and "超时" in detail and "终止两端" in detail
    assert procs["save"].killed and procs["load"].killed


def test_source_missing_fail_loud(monkeypatch):
    _patch_pipe(monkeypatch)
    w = _mk_worker(runner=FakeRunner(target="missing", local=False))
    ok, detail = w.sync_image("t:1", "tcp://x:2375")
    assert not ok and "hub 本机无源镜像" in detail and "绝不公网拉取" in detail


def test_daemon_unreachable_no_transfer(monkeypatch):
    procs = _patch_pipe(monkeypatch)
    w = _mk_worker(runner=FakeRunner(target="unreachable"))
    ok, detail = w.sync_image("t:1", "tcp://x:2375")
    assert not ok and "不可达" in detail and "不烧传输" in detail
    assert procs == {}  # 管道未起


# ── 完成动作：写表 + 复测探针 + 告警去抖 ─────────────────────────

def _patch_probe(monkeypatch, result={"daemon": "ok", "image": "ok", "mount": "ok"}):
    calls: list[int] = []
    monkeypatch.setattr(image_sync.health_probe, "probe_and_store",
                        lambda dbm, rid, cfg, docker_bin="docker": (calls.append(rid) or dict(result)))
    return calls


def test_finish_success_writes_and_reprobes(monkeypatch):
    dbm, n = FakeDB(), FakeNotifier()
    probe_calls = _patch_probe(monkeypatch)
    w = _mk_worker(dbm=dbm, notifier=n)
    w._finish(7, "t:1", "gpu-a", True, "ok")
    assert dbm.sync_writes == [(7, True, "t:1", "ok")] and probe_calls == [7]
    assert not n.sent  # 成功不告警（stderr 留痕在 capsys 面不单测）


def test_finish_failure_alert_dedup_and_success_reset(monkeypatch):
    dbm, n = FakeDB(), FakeNotifier()
    _patch_probe(monkeypatch, {"daemon": "ok", "image": "镜像缺失: t:1", "mount": "ok"})
    w = _mk_worker(dbm=dbm, notifier=n)
    w._finish(7, "t:1", "gpu-a", False, "超时")   # 首次失败 → 告警
    w._finish(7, "t:1", "gpu-a", False, "超时")   # 同组合去抖
    assert len(n.sent) == 1 and "失败" in n.sent[0] and "复测探针 仍有红项" in n.sent[0]
    w._finish(7, "t:1", "gpu-a", False, "另一原因")  # 原因变化 → 重告
    assert len(n.sent) == 2
    w._finish(7, "t:1", "gpu-a", True, "ok")       # 成功 → 重置该 (runner, tag) 全部组合
    w._finish(7, "t:1", "gpu-a", False, "超时")
    assert len(n.sent) == 3


def test_offer_dedup_and_inflight_merge(monkeypatch):
    release = threading.Event()

    def _sync(tag, host):
        release.wait(5)
        return True, "ok"

    w = _mk_worker()
    monkeypatch.setattr(w, "sync_image", _sync)
    assert w.offer(1, "t:1", "tcp://x:2375", "a") is True
    assert w.offer(1, "t:1", "tcp://x:2375", "a") is False  # 在飞去重
    assert w.offer(2, "t:1", "tcp://x:2375", "b") is True   # 不同 runner 各自入队
    release.set()
    w._q.join()


# ── 触发判据（lifecycle._offer_image_sync，unbound 调 fake self） ──

def _row(rid, name, enabled=1, daemon="ok", image="镜像缺失: t", mount="ok", bad_json=False):
    pj = "not-json" if bad_json else json.dumps({"daemon": daemon, "image": image, "mount": mount})
    return {"id": rid, "name": name, "enabled": enabled, "docker_host": f"tcp://{rid}:2375",
            "probe_json": pj if daemon or bad_json else None}


def test_trigger_judgment():
    offered: list[tuple] = []
    fw = types.SimpleNamespace(offer=lambda rid, tag, host, name: offered.append((rid, tag, name)) or True)
    fake = types.SimpleNamespace(
        _db=FakeDB([_row(1, "a"),                                    # daemon ok + image 红 → 触发
                    _row(2, "b", daemon="不可达: x"),                 # daemon 红 → 不触发
                    _row(3, "c", image="ok"),                        # 镜像在场 → 不触发
                    _row(4, "d", mount="回读不匹配"),                  # mount 红不拦分发 → 触发
                    _row(5, "e", enabled=0),                          # 禁用 → 不触发
                    _row(6, "f", bad_json=True)]),                    # 坏 json → 跳过
        image_sync=fw, cfg=types.SimpleNamespace(image_tag_default="pa-executor:t9"))
    lifecycle_mod.Lifecycle._offer_image_sync(fake)
    assert offered == [(1, "pa-executor:t9", "a"), (4, "pa-executor:t9", "d")]


def test_trigger_fail_open_on_db_error():
    class BoomDB:
        def list_runners(self):
            raise RuntimeError("sqlite 抽风（模拟）")
    fake = types.SimpleNamespace(_db=BoomDB(), image_sync=types.SimpleNamespace(
        offer=lambda *a: (_ for _ in ()).throw(AssertionError("不应 offer"))),
        cfg=types.SimpleNamespace(image_tag_default="t"))
    lifecycle_mod.Lifecycle._offer_image_sync(fake)  # 不抛即过（观察面 fail-open）


# ── db 迁移幂等 + 写读 ──────────────────────────────────────────

def test_db_migration_idempotent_and_sync_column(tmp_path):
    p = tmp_path / "h.sqlite3"
    d = db_mod.HubDB(p)
    rid = d.register_runner("gpu-a", "tcp://10.0.0.1:2375", "/mnt/nfs/pa", 1)
    d.set_runner_image_sync(rid, True, "pa-executor:t9", "流式完成")
    row = d.list_runners()[0]
    assert json.loads(row["image_sync_json"])["ok"] is True
    d.close()
    d2 = db_mod.HubDB(p)  # 重开：迁移幂等（列已在不炸）
    assert any(r["id"] == rid for r in d2.list_runners())
    d2.close()
