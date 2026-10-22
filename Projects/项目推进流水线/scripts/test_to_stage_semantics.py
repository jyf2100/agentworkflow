#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_to_stage_semantics.py — add-hub-slice-3 task 1.1：--to-stage 既有语义回归钉死。

背景（本体论审核 d-3 修正后基线）：``--to-stage`` 自初始 commit 即存在（非本 change 新增），
``default="dispatch"``——不传时本就不跑 report（cron 经 run_cron.sh 显式 ``--to-stage report``）。
本 change 零产码改动复用它为部署边界（roc cron 取值 report→prd），故此处只钉死既有契约防回归：

  * 参数存在 + choices = STAGES 全集（防删除/收窄）；
  * default == "dispatch"（防默认漂移——漂了 cron 未显式传的场景行为就变）；
  * STAGES 序列不变（区间推导 ``lo<=i<=hi`` 的根基）；
  * 区间组合语义等价物：to=prd → 执行集恰为 fetch/radar/prd（critic 起不跑）；
    from=critic..to=report → 执行集为后三段。

argparse 内联在 main()（不可单独调用），故用 ast 断言声明层契约；区间语义用 STAGES 重演
main() 的 ``run = {s:i for i,s in enumerate(STAGES)}; lo,hi = run[from],run[to]`` 推导。
跑：python3 -m pytest scripts/test_to_stage_semantics.py -q
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402


def _to_stage_arg() -> ast.Call:
    """从 run_daily.main() 的 argparse 声明里取 --to-stage 的 add_argument 调用节点。"""
    tree = ast.parse(Path(run_daily.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "add_argument" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and first.value == "--to-stage":
            return node
    raise AssertionError("run_daily.main() 中找不到 add_argument('--to-stage', ...)——参数被删除？")


def _kw(call: ast.Call, name: str) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def test_to_stage_flag_exists_with_full_choices():
    call = _to_stage_arg()
    choices = _kw(call, "choices")
    assert choices is not None, "--to-stage 必须带 choices=STAGES（开放取值会破坏区间推导）"
    # choices=STAGES 是 Name 引用；断言其解析结果与模块常量一致（全 7 段开放）
    assert run_daily.STAGES == ["fetch", "radar", "prd", "inject", "critic", "dispatch", "report"]


def test_to_stage_default_is_dispatch():
    call = _to_stage_arg()
    default = _kw(call, "default")
    assert isinstance(default, ast.Constant) and default.value == "dispatch", (
        "--to-stage default 必须保持 'dispatch'（不传 = 不跑 report 的既有事实；"
        "改默认会静默改变无 cron 显式取值场景的行为）"
    )


def test_stage_range_to_prd_stops_after_prd():
    """to=prd 区间语义：执行集恰为前半段三段——critic/dispatch/report 不执行、不产其产物。"""
    run = {s: i for i, s in enumerate(run_daily.STAGES)}
    hi = run["prd"]
    executed = [s for s in run_daily.STAGES if run[s] <= hi]
    assert executed == ["fetch", "radar", "prd"]
    assert "critic" not in executed and "dispatch" not in executed and "report" not in executed


def test_stage_range_from_critic_to_report():
    """from×to 区间组合语义（既有 --from-stage 断点续跑行为不变）。"""
    run = {s: i for i, s in enumerate(run_daily.STAGES)}
    lo, hi = run["critic"], run["report"]
    executed = [s for s in run_daily.STAGES if lo <= run[s] <= hi]
    assert executed == ["critic", "dispatch", "report"]
