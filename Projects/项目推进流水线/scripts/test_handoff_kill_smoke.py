#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_handoff_kill_smoke.py — add-dev-handoff-artifact 组6·6.1：断点③端到端冒烟。

链路（hub 容器中途崩 → 编排器收养 → 下轮 prompt 含 handoff 块）：

    ① dev 写侧（**真子进程**）：子进程以 dev-agent 同一调用面（``handoff.absorb_assistant_text``
       → 原子写 ``run_dir/handoff-latest.json``）落首个快照后被 **SIGKILL**——无 stdout JSON
       （= dispatch 侧 ``script_json=None`` / dev_killed），快照因 tmp+rename 原子性幸存。
    ② 编排器收养（dispatch_one 内联块同一调用序列）：``handoff.load_snapshot(wt/state/runs)``
       → ``run_daily._store_iteration_handoff(adopted=True)`` → journal 事件
       ``adopted_from_snapshot=true`` + content-addressed artifact + digest。
    ③ 下轮注入闭环：artifact path/digest 按 ``_dev_cmd`` 透传面组成参数 → **真 dev-agent**
       ``parse_args``+``build_prompt`` 读文件 + digest 校验 → prompt 含独立标记块
       「上一轮 dev 交接（仅上下文，非判据）」，且内容 = 快照原文（digest 闭环）。

边界声明：不跑 SDK dev loop（需目标仓 + roc 凭据，超出单测面）；SDK 内循环的逐 turn absorb
由组2 单测锁、dispatch_one 巨函数接线由组4 单测锁（repo 惯例：单测只锁纯函数面）。
本测试锁的是三段之间的**接缝**（子进程边界 × 文件系统 × digest）。跑：
python3 -m pytest scripts/test_handoff_kill_smoke.py -q
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import handoff  # noqa: E402
import run_daily  # noqa: E402

RD = Path(__file__).parent


def _import_dev_agent():
    spec = importlib.util.spec_from_file_location("dev_agent_kill_smoke", RD / "dev-agent.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeSJ:
    def __init__(self):
        self.enabled = True
        self.events: list[tuple] = []

    def emit(self, event_type, iter_id, prd_id, payload=None):
        self.events.append((event_type, iter_id, prd_id, payload or {}))


# 子进程剧本：dev-agent 写侧同一调用面落快照 → 停在「dev loop 中途」（被外部 SIGKILL）。
_CHILD = textwrap.dedent("""
    import sys, time
    from pathlib import Path
    sys.path.insert(0, {scripts!r})
    import handoff
    state = {{"handoff_latest": None}}
    handoff.absorb_assistant_text(
        state,
        "阶段性进展\\n```handoff\\n"
        + '{{\\"done\\": [\\"完成解析骨架\\"], \\"in_progress\\": [\\"注释边界\\"],'
        + ' \\"blockers\\": [], \\"tried_and_failed\\": [\\"regex 路不通\\"],'
        + ' \\"next\\": [\\"换 AST 遍历\\"], \\"rationale\\": \\"嵌套注释\\"}}'
        + "\\n```",
        run_dir=Path({runs!r}))
    assert state["handoff_latest"] is not None, "写侧未捕获 handoff"
    print("SNAPSHOT_WRITTEN", flush=True)
    time.sleep(600)   # dev loop 中途：等外部 SIGKILL（模拟容器非零退出/hub 崩）
""")


def test_sigkill_adoption_end_to_end(tmp_path):
    DA = _import_dev_agent()
    wt = tmp_path / "wt"
    runs = wt / "state" / "runs"
    runs.mkdir(parents=True)

    # ① 真子进程写侧 + SIGKILL（无 stdout JSON = dev_killed）
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD.format(scripts=str(RD), runs=str(runs))],
        cwd=str(wt), stdout=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 30
    while not (runs / handoff.SNAPSHOT_NAME).exists():
        if time.monotonic() > deadline or child.poll() is not None:
            child.kill()
            pytest.fail("子进程未在期限内写出 handoff 快照（或提前退出）")
        time.sleep(0.05)
    first_line = child.stdout.readline().strip() if child.stdout else ""
    child.kill()                       # SIGKILL：dispatch 侧只见无 JSON 尾行（script_json=None）
    child.wait(timeout=10)
    assert first_line == "SNAPSHOT_WRITTEN"

    # ② 编排器收养（dispatch_one 内联块同一序列：dev_killed → load_snapshot → store adopted）
    snap = handoff.load_snapshot(runs)
    assert snap is not None and snap["next"] == ["换 AST 遍历"]   # 快照幸存（tmp+rename 原子性）
    sj = _FakeSJ()
    art_root = tmp_path / "artifacts" / "run-1"
    ref = run_daily._store_iteration_handoff(sj, "it-1", "prd-1", art_root, snap,
                                             round_n=1, adopted=True)
    assert ref is not None and ref["digest"].startswith("sha256:")
    et, it, pid, pl = sj.events[-1]
    assert (et, it, pid) == ("iteration_handoff", "it-1", "prd-1")
    assert pl["adopted_from_snapshot"] is True and pl["digest"] == ref["digest"]
    art_abs = art_root / ref["path"]
    raw = art_abs.read_text(encoding="utf-8")
    assert handoff.verify_digest(raw, ref["digest"]) is True       # artifact ⇄ digest 闭环

    # ③ 下轮注入：_dev_cmd 透传面参数 → 真 dev-agent build_prompt 读文件 + 校验 + 渲染标记块
    cmd = run_daily._dev_cmd({"conda_env": ""}, str(tmp_path / "p.prd"), "main", "",
                             expect_handoff=True,
                             handoff_artifact=str(art_abs), handoff_digest=ref["digest"])
    assert "--expect-handoff" in cmd
    assert cmd[cmd.index("--handoff-artifact") + 1] == str(art_abs)
    assert cmd[cmd.index("--handoff-digest") + 1] == ref["digest"]
    args = DA.parse_args(["--prd", "x.md", "--expect-handoff",
                          "--handoff-artifact", str(art_abs),
                          "--handoff-digest", ref["digest"]])
    prompt = DA.build_prompt(args, "PRD 内容", "pa-dev/x")
    assert "## 上一轮 dev 交接（仅上下文，非判据）" in prompt   # 独立标记块（delta scenario）
    assert "换 AST 遍历" in prompt                              # 内容 = 快照原文（dev 亲笔进下轮）
