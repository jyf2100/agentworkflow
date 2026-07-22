#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dispatch_flag_integration.py — task 2.4 subprocess 级 flag→真实 dispatch/SDK 路径集成测试。

design.md「subprocess-level SDK hook wiring tests」+ tasks 2.4「证明每个 feature flag 改变真实
dispatch/SDK 路径，而非只在 helper/演练函数里测」。

**与 unit test 的区别**：test_coordinator / test_hook_bridge 是 in-process 测**内部 helper**（coordinator
方法、hook_bridge 映射函数，用 fake adapter/dict）。本文件用**独立子进程**跑**生产入口接线**——
``build_coordinator``（dispatch_one 与 dev-agent main 共用的唯一 coordinator 边界，run_daily:1236 /
dev-agent main）+ ``build_dev_hooks``（dev-agent main 的 SDK hook wiring）——flag 经真实 env（``PA_LOOP_*``，
同生产 env 解析路径）控制，对比开/关的真实副作用。

Section 2 已接入生产路径的两个 flag 的真实路径证据：
  * **journal_shadow**（dispatch 路径）：dispatch_one lifecycle 在 run_daily:1247 ``_sj.emit("planned",...)``
    （``_sj=_coord.journal``）。on → 落盘 journal.jsonl（含 planned 事件）；off → ShadowJournal no-op
    （loop_runtime:65），journal 不创建（baseline 不留痕）。
  * **lifecycle_hooks**（SDK 路径）：dev-agent main 调 build_dev_hooks → ``ClaudeAgentOptions.hooks``。
    on → 6-event hooks dict，真实 claude_agent_sdk 接受；off → (None, None)（baseline 不注册 SDK hooks）。

其余 4 个 flag（driven/retry/sandbox/telemetry）的**真实 dispatch/SDK 路径**在后续 Section 接入
（journal_driven_dispatch→7.5、session_aware_retry→3、container_sandbox→5、telemetry_export→6）；
本文件钉死它们已正确经生产 coordinator 边界解析（env > 默认），后续 Section 接入时此契约不变。

子进程 stdout 输出 JSON；主进程解析断言。跑：``python3 -m pytest scripts/test_dispatch_flag_integration.py -q``
AAA 结构（Arrange / Act / Assert）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent)

# 子进程内联脚本：跑真实生产函数链，按 ``PA_TEST_MODE`` 分流，stdout 输出 JSON 结果。
# 每个 mode 只 import 它需要的模块——journal-shadow/parse-flags/preflight 不 import claude_agent_sdk
# （保 SDK-free 隔离友好，间接证明 build_coordinator/emit/preflight 非 SDK 依赖）；lifecycle-hooks 才
# import 真实 SDK（本就是要测 SDK hook wiring）。
_CHILD_SCRIPT = r'''
import json, os, sys
sys.path.insert(0, ".")

def _result(**kw):
    print(json.dumps(kw))

mode = os.environ["PA_TEST_MODE"]
state = os.environ["PA_TEST_STATE"]
stamp = "20260722-0900"

if mode == "journal-shadow":
    from coordinator import build_coordinator
    coord = build_coordinator(stamp=stamp, prd_path="prd/x.md", proj="p", slug="s",
                              state_dir=state, env=os.environ)
    eid = coord.emit("planned", {"source": "task_2_4"})
    _result(flag=coord.flags.journal_shadow,
            journal_exists=os.path.exists(coord.journal.path),
            journal_path=str(coord.journal.path), emit_id=eid)

elif mode == "lifecycle-hooks":
    from coordinator import build_coordinator
    from hook_bridge import build_dev_hooks
    from claude_agent_sdk import ClaudeAgentOptions
    coord = build_coordinator(stamp=stamp, prd_path="prd/x.md", proj="p", slug="s",
                              state_dir=state, env=os.environ)
    adapter, hooks = build_dev_hooks(coord)
    # 真实 SDK 接受 hooks（None 或 6-event dict）——wiring 真实性证据
    ClaudeAgentOptions(hooks=hooks)
    events = sorted(hooks.keys()) if hooks else []
    _result(flag=coord.flags.lifecycle_hooks, adapter_present=adapter is not None,
            hooks_present=hooks is not None, events=events)

elif mode == "parse-flags":
    from coordinator import build_coordinator
    coord = build_coordinator(stamp=stamp, prd_path="prd/x.md", proj="p", slug="s",
                              state_dir=state, env=os.environ)
    f = coord.flags
    _result(journal_shadow=f.journal_shadow, journal_driven_dispatch=f.journal_driven_dispatch,
            session_aware_retry=f.session_aware_retry, lifecycle_hooks=f.lifecycle_hooks,
            container_sandbox=f.container_sandbox, telemetry_export=f.telemetry_export)

elif mode == "preflight":
    from coordinator import build_coordinator, preflight
    coord = build_coordinator(stamp=stamp, prd_path="prd/x.md", proj="p", slug="s",
                              state_dir=state, env=os.environ)
    res = preflight(coord.flags)
    _result(ok=res.is_ok,
            reason=res.blocked.reason if res.blocked else None,
            violations=list(res.blocked.violations) if res.blocked else [])

else:
    raise SystemExit(f"unknown PA_TEST_MODE: {mode!r}")
'''


def _read_journal(path: str) -> list[dict]:
    """读 journal.jsonl 每行 JSON（落盘事件）；文件不存在/空 → []。"""
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run_child(mode: str, *, state_dir: Path, flags_env: dict[str, str]) -> dict:
    """跑生产入口子进程，返回解析后的 JSON 结果 dict。

    子进程跑真实生产函数链（build_coordinator / build_dev_hooks / preflight——dispatch_one 与 dev-agent
    main 共用的生产入口）；flag 经 ``PA_LOOP_*`` env 控制（同生产 env 解析路径）。filter 掉父进程
    ``PA_LOOP_*`` 残留保 flag 隔离；保留 PATH/PYTHONPATH（lifecycle mode 需 import claude_agent_sdk）。
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("PA_LOOP_")}
    env.update(flags_env)
    env["PA_TEST_MODE"] = mode
    env["PA_TEST_STATE"] = str(state_dir)
    result = subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT],
        capture_output=True, text=True, timeout=30, cwd=SCRIPTS_DIR, env=env,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"子进程 {mode!r} 退出码 {result.returncode}\nSTDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}")
    return json.loads(result.stdout.strip())


# ════════════════════════════════════════════════════════════════════════════
# journal_shadow：真实 dispatch journal 路径（落盘 vs no-op）
# ════════════════════════════════════════════════════════════════════════════
def test_journal_shadow_on_persists_journal_event(tmp_path):
    """journal_shadow=true → build_coordinator（dispatch_one 生产入口，run_daily:1236）+ emit 落盘。

    dispatch_one lifecycle 在 run_daily:1247 ``_sj.emit("planned", ...)``（``_sj=_coord.journal``）。本测试
    用独立子进程跑 build_coordinator + coord.emit("planned")，证明 flag 经生产 coordinator 边界改变真实
    dispatch journal 路径（journal.jsonl 创建 + 含 planned 事件），而非只在 ShadowJournal helper 单元测。
    """
    # Act
    out = _run_child("journal-shadow", state_dir=tmp_path,
                     flags_env={"PA_LOOP_JOURNAL_SHADOW": "true"})
    # Assert
    assert out["flag"] is True
    assert out["journal_exists"] is True
    assert out["emit_id"] is not None                     # emit 返回 event_id（落盘成功）
    events = _read_journal(out["journal_path"])
    assert any(e["event_type"] == "planned" for e in events)


def test_journal_shadow_off_does_not_create_journal(tmp_path):
    """journal_shadow 默认关 → ShadowJournal no-op（loop_runtime:65），emit 返回 None，journal 不创建。

    spec「Disabled runtime preserves baseline」：flags 全关 = 第一阶段行为，dispatch 不留痕。
    """
    # Act
    out = _run_child("journal-shadow", state_dir=tmp_path, flags_env={})
    # Assert
    assert out["flag"] is False
    assert out["journal_exists"] is False
    assert out["emit_id"] is None


# ════════════════════════════════════════════════════════════════════════════
# lifecycle_hooks：真实 SDK ClaudeAgentOptions.hooks 路径
# ════════════════════════════════════════════════════════════════════════════
def test_lifecycle_hooks_on_registers_real_sdk_six_events(tmp_path):
    """lifecycle_hooks=true → build_dev_hooks（dev-agent main wiring）返回 6-event hooks dict +
    真实 claude_agent_sdk.ClaudeAgentOptions(hooks=...) 构造成功。

    design.md「subprocess-level SDK hook wiring tests」：子进程跑 build_coordinator + build_dev_hooks，
    证明 flag 经生产 coordinator 边界改变真实 SDK hook 注册（6 lifecycle events 接入 ClaudeAgentOptions），
    而非只在 hook_bridge helper 单元测。
    """
    # Act
    out = _run_child("lifecycle-hooks", state_dir=tmp_path,
                     flags_env={"PA_LOOP_JOURNAL_SHADOW": "true", "PA_LOOP_LIFECYCLE_HOOKS": "true"})
    # Assert
    assert out["flag"] is True
    assert out["adapter_present"] is True
    assert out["hooks_present"] is True
    assert out["events"] == ["PostToolUse", "PreCompact", "PreToolUse",
                             "Stop", "SubagentStart", "SubagentStop"]
    # options_built 隐含 True（子进程 ClaudeAgentOptions(hooks=hooks) 未抛）——真实 SDK 接受 hooks


def test_lifecycle_hooks_off_returns_none_and_sdk_accepts_none(tmp_path):
    """lifecycle_hooks 关 → build_dev_hooks 返回 (None, None)（baseline，dev-agent 不注册 SDK hooks，
    design 决策#8）；真实 ClaudeAgentOptions(hooks=None) 构造成功（SDK 接受无 hooks baseline）。"""
    # Act
    out = _run_child("lifecycle-hooks", state_dir=tmp_path, flags_env={})
    # Assert
    assert out["flag"] is False
    assert out["adapter_present"] is False
    assert out["hooks_present"] is False
    assert out["events"] == []
    # options_built 隐含 True（ClaudeAgentOptions(hooks=None) 未抛）


# ════════════════════════════════════════════════════════════════════════════
# driven/retry/sandbox/telemetry：经 env 解析到生产入口（真实路径待后续 Section）
# ════════════════════════════════════════════════════════════════════════════
def test_each_flag_parsed_at_production_entry_from_env(tmp_path):
    """全部 6 个 flag 经 PA_LOOP_* env 解析到 build_coordinator.flags（生产入口一次解析）。

    「each feature flag」要求：journal_shadow/lifecycle_hooks 的真实路径由上面的 dispatch/SDK 测试覆盖；
    其余 4 个（driven/retry/sandbox/telemetry）的真实 dispatch/SDK 路径在后续 Section 接入，本测试钉死
    它们已正确经生产 coordinator 边界解析（env > 默认），后续 Section 接入时此解析契约不变。
    """
    # Act
    out = _run_child("parse-flags", state_dir=tmp_path, flags_env={
        "PA_LOOP_JOURNAL_SHADOW": "true",
        "PA_LOOP_JOURNAL_DRIVEN_DISPATCH": "true",
        "PA_LOOP_SESSION_AWARE_RETRY": "true",
        "PA_LOOP_LIFECYCLE_HOOKS": "true",
        "PA_LOOP_CONTAINER_SANDBOX": "true",
        "PA_LOOP_TELEMETRY_EXPORT": "true",
    })
    # Assert
    assert out == {
        "journal_shadow": True, "journal_driven_dispatch": True,
        "session_aware_retry": True, "lifecycle_hooks": True,
        "container_sandbox": True, "telemetry_export": True,
    }
    # TODO(Section 7.5/3/5/6)：driven/retry/sandbox/telemetry 真实 dispatch/SDK 路径接入后，补对应
    # subprocess 集成测试（journal_driven_dispatch→真实 reducer 驱动 dispatch、session_aware_retry→
    # 真实 resume、container_sandbox→真实容器执行、telemetry_export→真实 OTLP export）。


def test_flags_default_all_off_at_production_entry(tmp_path):
    """无 env / 无 profile → build_coordinator.flags 全 False（baseline 保留，spec「Disabled runtime」）。"""
    # Act
    out = _run_child("parse-flags", state_dir=tmp_path, flags_env={})
    # Assert
    assert out == {
        "journal_shadow": False, "journal_driven_dispatch": False,
        "session_aware_retry": False, "lifecycle_hooks": False,
        "container_sandbox": False, "telemetry_export": False,
    }


# ════════════════════════════════════════════════════════════════════════════
# preflight：生产入口拦截 invalid flag 组合（task 2.5 在生产 build_coordinator 后生效）
# ════════════════════════════════════════════════════════════════════════════
def test_preflight_blocks_lifecycle_hooks_without_journal_shadow(tmp_path):
    """lifecycle_hooks=true 但 journal_shadow 关 → preflight(coord.flags) blocked（dispatch 跳过，
    run_daily:1242-1245）。

    子进程跑 build_coordinator（解析 flag）+ preflight，证明 preflight 经生产入口解析的 flag 生效
    （而非只 preflight helper 单元测）；design 决策#1 防 impossible partial 组合（hooks 无 journal）。
    """
    # Act
    out = _run_child("preflight", state_dir=tmp_path,
                     flags_env={"PA_LOOP_LIFECYCLE_HOOKS": "true"})   # 故意无 journal_shadow
    # Assert
    assert out["ok"] is False
    assert out["violations"]                               # 含 lifecycle_hooks→journal_shadow 依赖违规
    assert any("lifecycle_hooks requires journal_shadow" in v for v in out["violations"])
