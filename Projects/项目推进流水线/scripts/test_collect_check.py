#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_collect_check.py — add-acceptance-preflight-guard P1：dispatch 期收集级自检单测。

第二层（design D3）：worktree 内 `pytest <注入文件> --collect-only -q` 一次子进程真判活——
相对导入按文件系统解析到仓源码（P0 管「无事实时保守」，P1 管「有仓源码时验证」）。
三态：pass（exit 0）/ fail（失败签名锚定注入文件 → 扣套件）/ indeterminate（锚定外部/
超时/pytest 缺失——环境 UNKNOWN ≠ 套件坏，不扣）。mock subprocess，零真跑。

跑：python3 -m pytest scripts/test_collect_check.py -q
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402


def _fake_run(monkeypatch, returncode=0, out="", err="", exc=None):
    calls: list[list[str]] = []

    def fake(cmd, **kw):
        calls.append(cmd)
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(cmd, returncode, out, err)

    monkeypatch.setattr(run_daily.subprocess, "run", fake)
    return calls


_INJ = {"test_login.py": "# criterion: demo:ac1\ndef test_x():\n    assert True\n"}


def test_collect_pass_exit_zero(monkeypatch):
    calls = _fake_run(monkeypatch, returncode=0, out="1 test collected in 0.01s")
    verdict, note = run_daily._collect_check(Path("/wt"), _INJ)
    assert verdict == "pass"
    assert calls[0][:3] == [sys.executable, "-m", "pytest"] and "--collect-only" in calls[0]
    assert calls[0][-3] == str(Path("/wt") / "test_login.py")   # 圈定注入文件不全仓
    assert calls[0][-2:] == ["--collect-only", "-q"]


def test_collect_fail_anchored_on_injected_file(monkeypatch):
    out = ("test_login.py:1: in <module>\n    import requests\n"
           "E   ModuleNotFoundError: No module named 'requests'")
    _fake_run(monkeypatch, returncode=2, out=out)
    verdict, note = run_daily._collect_check(Path("/wt"), _INJ)
    assert verdict == "fail" and "test_login.py" in note


def test_collect_indeterminate_when_conftest_missing(monkeypatch):
    # conftest 缺依赖：header 提及收集入口但最后 traceback 帧锚 conftest.py → 归环境不扣
    out = ("_test_login.py:1: in <module>\n"
           "/wt/conftest.py:5: in <module>\n    import nonexistent\n"
           "E   ModuleNotFoundError: No module named 'nonexistent'")
    _fake_run(monkeypatch, returncode=2, out=out)
    verdict, note = run_daily._collect_check(Path("/wt"), _INJ)
    assert verdict == "indeterminate" and "锚定外部" in note


def test_collect_indeterminate_on_timeout(monkeypatch):
    _fake_run(monkeypatch, exc=subprocess.TimeoutExpired(cmd="pytest", timeout=120))
    verdict, note = run_daily._collect_check(Path("/wt"), _INJ)
    assert verdict == "indeterminate" and "超时" in note


def test_collect_indeterminate_when_env_broken(monkeypatch):
    _fake_run(monkeypatch, exc=FileNotFoundError("python"))
    verdict, note = run_daily._collect_check(Path("/wt"), _INJ)
    assert verdict == "indeterminate" and "环境" in note


def test_dev_cmd_without_bundle_carries_no_acceptance_args(monkeypatch, tmp_path):
    # 2.3 fail 路径：_suite_snap 置 None → _dev_cmd(acceptance_bundle=None) → dev 无 acceptance_injection 启动
    monkeypatch.setattr(run_daily, "DEV_AGENT_PY", tmp_path / "dev-agent.py")
    (tmp_path / "dev-agent.py").write_text("#!/bin/sh\n", encoding="utf-8")
    prof = {"conda_env": ""}
    cmd = run_daily._dev_cmd(prof, "/tmp/prd.md", "main", None,
                             acceptance_bundle=None, acceptance_digest=None)
    assert cmd is not None
    assert not any(a.startswith("--acceptance-tests") or a.startswith("--acceptance-digest") for a in cmd)


def test_acceptance_collect_event_tolerated_by_reducer():
    # 2.5：acceptance_collect 不在 _EVENT_STATUS_MAP → reducer 幂等跳过（status 不变、无迁移错误），
    # recovery_cli / reconcile 走同一归约面——新事件类型零破坏。
    import loop_state
    ev0 = loop_state.JournalEvent(schema_version=1, event_id="e0", timestamp="t0",
                                  run_id="r", prd_id="p", iteration_id="i",
                                  event_type="running", payload={"base": "main"})
    ev1 = loop_state.JournalEvent(schema_version=1, event_id="e1", timestamp="t1",
                                  run_id="r", prd_id="p", iteration_id="i",
                                  event_type="acceptance_collect",
                                  payload={"verdict": "fail", "note": "anchored", "digest": "d"})
    st = loop_state.reduce([ev0, ev1])
    assert st.status == loop_state.IterationStatus.RUNNING   # 新事件不推进/不破坏 status
    assert "acceptance" not in (st.last_transition_error or "")   # 也无迁移错误污染
