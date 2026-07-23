#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dev_cmd_retry.py — task 3.3 P0-3：_dev_cmd 生成 session-aware retry 参数。

r2 §6.3 要求「run_daily → RetryPolicy → recover_iteration → **dev-agent args** → SDK」端到端证据。
本文件验证命令行参数生成环节：run_daily 据 RetryPolicy.decide 的 mode，向控制面 dev-agent.py 透传
``--state-dir / --iteration-seq / --resume-session / --fork-session``（run_daily.py:_dev_cmd）。

**flag 门控语义**（baseline 零回归的核心保证）：
  * baseline（session_aware_retry 关）→ 调用点不传 session 参数 → cmd 与 r2 前完全一致（test_baseline_*）；
  * retry 模式 → 据 mode 注入对应参数（test_resume_/fork_/state_dir_/iteration_seq_）。

recover_iteration 接入与 budget 终止的门控逻辑在 run_daily.dispatch_one revise 触发点
（``if _coord.flags.session_aware_retry ...``），由 test_dispatch_flag_integration / test_coordinator
覆盖 flag→路径边界；本文件专注 _dev_cmd 的纯函数参数生成。
"""
import run_daily


def _cmd(**kw) -> list:
    """构造 _dev_cmd 调用（prof 无 conda_env → 宿主 python）。"""
    return run_daily._dev_cmd({"conda_env": ""}, "/tmp/x.prd", "main", "/tmp/src", **kw)


def test_baseline_cmd_no_session_args():
    """baseline（不传 session 参数）→ cmd 不含任何 retry 参数（与 r2 前零差异）。"""
    cmd = _cmd()
    for flag in ("--state-dir", "--iteration-seq", "--resume-session", "--fork-session"):
        assert flag not in cmd, f"baseline cmd 不应含 {flag}: {cmd}"


def test_state_dir_unifies_session_store():
    """state_dir=控制面 STATE_DIR → 注入 --state-dir（dev-agent SessionStore 与控制面同一）。"""
    cmd = _cmd(state_dir="/vault/.project-auto/state", iteration_seq=2)
    assert cmd[cmd.index("--state-dir") + 1] == "/vault/.project-auto/state"


def test_iteration_seq_injected_when_positive():
    """seq>0（retry 衍生 distinct iteration）→ 注入 --iteration-seq N。"""
    cmd = _cmd(state_dir="/x", iteration_seq=3)
    assert cmd[cmd.index("--iteration-seq") + 1] == "3"


def test_iteration_seq_zero_omitted():
    """seq=0（baseline 新 session）→ 不注入 --iteration-seq（dev-agent 走默认 round1）。"""
    cmd = _cmd(state_dir="/x", iteration_seq=0)
    assert "--iteration-seq" not in cmd


def test_resume_session_arg_for_resume_mode():
    """RetryMode.RESUME → 注入 --resume-session <id>，不含 --fork-session。"""
    cmd = _cmd(iteration_seq=2, resume_session="sess-abc")
    assert cmd[cmd.index("--resume-session") + 1] == "sess-abc"
    assert "--fork-session" not in cmd


def test_fork_session_arg_for_fork_mode():
    """RetryMode.FORK → 注入 --fork-session，不含 --resume-session。"""
    cmd = _cmd(iteration_seq=2, fork_session=True)
    assert "--fork-session" in cmd
    assert "--resume-session" not in cmd


def test_new_session_mode_emits_neither():
    """RetryMode.NEW_SESSION → resume/fork 均不注入（仅 state_dir + iteration_seq）。"""
    cmd = _cmd(state_dir="/x", iteration_seq=2)
    assert "--resume-session" not in cmd
    assert "--fork-session" not in cmd
