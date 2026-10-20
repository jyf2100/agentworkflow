# test_webapp.py — webapp 注入面板单测（productize-pa-template task 5.5）。
#
# 覆盖（prd-injection-console spec 四 Requirement）：
#   ① 注入触发：上传合法 md → 落盘 + subprocess 起 run_daily --from-stage inject（共享 CLI 注入路径，
#      fake pipeline 记 argv 实证）；非 .md / 非文件载荷 / 空文件 → 400 且零子进程；
#   ② 锁互斥：编排器 .run.lock 占用（活 pid）→ 409「进行中」不 spawn；双触发（面板内互斥）→ 409；
#      陈旧锁（pid 失活）不视占用；
#   ③ 只读状态：dispatch_*.json 解析渲染；GET 后产物字节不变（零 verdict 改写）；
#   ④ localhost 边界：DEFAULT_HOST == 127.0.0.1；fail-open：child start_new_session 脱离面板
#      进程组且独立存活（面板崩溃不拖 run）。
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent
WEBAPP_DIR = SCRIPTS_DIR.parent / "webapp"

if not (WEBAPP_DIR / "app.py").exists():
    pytest.skip("webapp/ 不在本 checkout", allow_module_level=True)

try:
    from fastapi.testclient import TestClient
except ImportError:   # pragma: no cover - 装齐 dev extras 后不触发
    pytest.skip("缺 fastapi/httpx——pip install -e '.[dev,webapp]'", allow_module_level=True)

sys.path.insert(0, str(WEBAPP_DIR))
import app as webapp_app  # noqa: E402  # webapp 模块经 sys.path 引入（非 scripts 扁平约定面）

_PRD_MD = ("---\nproject: demo\nsource_path: Projects/x/信号.md\n---\n"
           "# 修复登录超时\n\n验收：npm test 全绿\n").encode("utf-8")

# fake pipeline：把收到的 argv 写进 FAKE_MARKER（env 经 Popen 继承）——实证注入参数
_FAST = (
    "import json, os, sys\n"
    "open(os.environ['FAKE_MARKER'], 'w').write(json.dumps(sys.argv[1:]))\n"
)
# 长跑 fake：写 marker 后 sleep（模拟进行中的 run；测试结束 terminate 清理）
_SLOW = (
    "import os, time\n"
    "open(os.environ['FAKE_MARKER'], 'w').write('started')\n"
    "time.sleep(300)\n"
)


def _client(tmp_path: Path, body: str):
    marker = tmp_path / "marker.txt"
    fake = tmp_path / "fake_pipeline.py"
    fake.write_text(body, encoding="utf-8")
    application = webapp_app.create_app(state_dir=tmp_path / "state",
                                        pipeline_cmd=[sys.executable, str(fake)])
    return TestClient(application), marker


def _wait_marker(marker: Path, tries: int = 40) -> str:
    for _ in range(tries):   # 子进程写 marker 有微小延迟，轮询等它
        if marker.exists() and marker.read_text(encoding="utf-8"):
            return marker.read_text(encoding="utf-8")
        time.sleep(0.05)
    raise AssertionError("fake pipeline 未写 marker（未触发子进程？）")


def _marker_json(marker: Path) -> list[str]:
    return json.loads(_wait_marker(marker))


def _write_lock(state: Path, pid: int, age_seconds: float = 0.0) -> None:
    from datetime import datetime, timedelta, timezone
    started = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    state.mkdir(parents=True, exist_ok=True)
    (state / ".run.lock").write_text(json.dumps({"pid": pid, "started": started}), encoding="utf-8")


# ─── ① 注入触发 ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_current_run():
    webapp_app._current_run = None       # 面板内互斥状态测试间隔离
    yield
    proc = webapp_app._current_run
    if proc is not None and proc.poll() is None:
        proc.terminate()


def test_upload_triggers_inject_subprocess(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MARKER", str(tmp_path / "marker.txt"))
    client, marker = _client(tmp_path, _FAST)
    r = client.post("/api/inject", files={"file": ("prd.md", _PRD_MD, "text/markdown")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "accepted"
    argv = _marker_json(marker)
    # 共享 CLI 注入路径：子进程参数就是 run_daily 的手动注入参数
    assert argv[-4:] == ["--from-stage", "inject", "--inject-prd", body["prd"]]
    assert Path(body["prd"]).read_bytes() == _PRD_MD          # 落盘即上传内容
    assert Path(body["prd"]).parent.name == "inbox"


def test_upload_rejects_non_markdown_and_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MARKER", str(tmp_path / "marker.txt"))
    client, marker = _client(tmp_path, _FAST)
    r = client.post("/api/inject", files={"file": ("evil.sh", b"rm -rf /", "application/x-sh")})
    assert r.status_code == 400
    r = client.post("/api/inject", files={"file": ("prd.md", b"   \n", "text/markdown")})
    assert r.status_code == 400                                  # 空文件非合法 PRD
    assert not marker.exists()                                   # 两次拒绝均零子进程


def test_upload_filename_path_injection_sanitized(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MARKER", str(tmp_path / "marker.txt"))
    client, marker = _client(tmp_path, _FAST)
    r = client.post("/api/inject", files={"file": ("../../etc/passwd.md", _PRD_MD, "text/markdown")})
    assert r.status_code == 200
    assert "/" not in Path(r.json()["prd"]).name                 # basename + 白名单后落 inbox


# ─── ② 锁互斥（复用编排器 .run.lock 语义）───────────────────────────────────

def test_lock_busy_returns_running_no_spawn(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MARKER", str(tmp_path / "marker.txt"))
    client, marker = _client(tmp_path, _FAST)
    _write_lock(tmp_path / "state", pid=os.getpid())             # 活 pid = 占用
    r = client.post("/api/inject", files={"file": ("prd.md", _PRD_MD, "text/markdown")})
    assert r.status_code == 409 and r.json()["status"] == "running"
    assert not marker.exists()                                   # 不排队不并发：零子进程


def test_stale_lock_by_dead_pid_not_held(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MARKER", str(tmp_path / "marker.txt"))
    client, marker = _client(tmp_path, _FAST)
    _write_lock(tmp_path / "state", pid=999_999_999)             # 失活 pid → run_daily 会接管
    r = client.post("/api/inject", files={"file": ("prd.md", _PRD_MD, "text/markdown")})
    assert r.status_code == 200
    assert _marker_json(marker)                                  # 正常触发


def test_double_trigger_second_gets_409(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MARKER", str(tmp_path / "marker.txt"))
    client, marker = _client(tmp_path, _SLOW)
    r1 = client.post("/api/inject", files={"file": ("prd.md", _PRD_MD, "text/markdown")})
    assert r1.status_code == 200
    _wait_marker(marker)                                         # 第一个 run 已在跑
    r2 = client.post("/api/inject", files={"file": ("prd2.md", _PRD_MD, "text/markdown")})
    assert r2.status_code == 409 and r2.json()["status"] == "running"
    # fail-open：面板内互斥挡住第二次 spawn——同一时刻只有一个编排器进程
    assert webapp_app._current_run.poll() is None


# ─── ③ 只读状态视图 ──────────────────────────────────────────────────────────

def _write_dispatch(state: Path, records: list[dict]) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / "dispatch_20261020-0317.json").write_text(json.dumps(records), encoding="utf-8")


def test_status_parses_dispatch_and_prs(tmp_path):
    client, _ = _client(tmp_path, _FAST)
    _write_dispatch(tmp_path / "state", [
        {"project": "demo", "slug": "xiu-fu-deng-lu", "status": "pr_open",
         "pr_url": "https://github.com/o/r/pull/7", "verify_verdict": "green"},
        {"project": "demo", "slug": "qi-ta", "status": "skip", "skip_reason": "x"},
    ])
    j = client.get("/api/status").json()
    assert j["dispatch_file"] == "dispatch_20261020-0317.json"
    assert j["prs"] == ["https://github.com/o/r/pull/7"]
    assert j["counts"] == {"pr_open": 1, "skip": 1}
    assert j["records"][0]["verify_verdict"] == "green"


def test_status_lock_running_and_readonly(tmp_path):
    client, _ = _client(tmp_path, _FAST)
    _write_dispatch(tmp_path / "state", [{"project": "d", "slug": "s", "status": "pr_open", "pr_url": "u"}])
    _write_lock(tmp_path / "state", pid=os.getpid())
    before = (tmp_path / "state" / "dispatch_20261020-0317.json").read_bytes()
    j = client.get("/api/status").json()
    assert j["running"] is True and j["lock"]["pid"] == os.getpid()
    # 只读不变式：GET 后产物字节不变（面板零 verdict 改写）
    assert (tmp_path / "state" / "dispatch_20261020-0317.json").read_bytes() == before


def test_status_empty_state_is_graceful(tmp_path):
    client, _ = _client(tmp_path, _FAST)
    j = client.get("/api/status").json()
    assert j["running"] is False and j["records"] == [] and j["prs"] == []


# ─── ④ localhost 边界 + fail-open ────────────────────────────────────────────

def test_default_host_is_localhost():
    assert webapp_app.DEFAULT_HOST == "127.0.0.1"    # spec：默认必须 localhost，无远程绑定默认值


def test_child_detached_from_panel_session(tmp_path, monkeypatch):
    """fail-open：触发的 run 在独立 session（start_new_session）——面板崩溃/被杀不拖 run。"""
    monkeypatch.setenv("FAKE_MARKER", str(tmp_path / "marker.txt"))
    client, marker = _client(tmp_path, _SLOW)
    r = client.post("/api/inject", files={"file": ("prd.md", _PRD_MD, "text/markdown")})
    assert r.status_code == 200
    pid = r.json()["pid"]
    _wait_marker(marker)
    assert webapp_app._current_run.poll() is None
    # 脱离面板 session：child 的 pgid == 自身 pid（新 session leader），≠ 测试/面板进程组
    assert os.getpgid(pid) == pid and os.getpgid(pid) != os.getpgid(0)
    # 面板侧进程对象销毁后 child 仍活（kill 面板不产副作用——child 独立存活即证）
    webapp_app._current_run = None
    time.sleep(0.2)
    os.kill(pid, 0)                                # 仍存活（ProcessLookupError 会抛）
    subprocess.run(["kill", str(pid)], check=False)  # 清理
