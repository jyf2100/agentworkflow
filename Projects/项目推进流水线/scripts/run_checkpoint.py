"""run_checkpoint.py — run 级 checkpoint 快照与当日断点续跑推导（pa-hardening P0-2 / P2-1）。

**为什么需要它**（两起真实故障的 G2/G3 缺口）：各段「产物存在即复用」闸只构成**隐式**断点——
没有 run 级元数据（哪些段完成 / 中断在哪 / 成本累计），`--from-stage` 推导靠人脑；中途段崩溃 →
当日零报告零邮件，次日 cron 新 stamp 跳过昨日文件，半成品成孤儿现场（SPEC §6「断点续跑」设计
承诺只兑现到人工续跑级别）。本模块把每个段完成即落 run 级快照（只记索引不复制产物），中断后
`maybe_auto_resume` 自动推导 `--from-stage`（复用既有产物复用闸兜底一致性）。

**flag 门控（默认 off = baseline 零行为变化）**：
    * ``PA_CHECKPOINT`` —— 写点总门（mark_done / mark_terminal / add_cost 全部 no-op 直到 on）；
    * ``PA_AUTO_RESUME`` —— 读点门（maybe_auto_resume 生效；单独开，灰度顺序在 checkpoint 之后）。
    两 flag 独立（resume 只依赖盘上是否已有 interrupted checkpoint 文件，不强求本 run 也开着写点）。

**数据**：``<state_dir>/checkpoint_<stamp>.json``——新文件，不写不删任何既有 ``state/*.json``（回滚
无状态清理负担）。文件名跟 **run 起始 stamp**（``args.stamp``）；inject 段自增 stamp 的边界用内部
``actual_stamp`` 字段跟踪（产物互证按 actual_stamp 对齐文件名），单文件跟踪整 run。

**产物互证（防状态与盘不一致）**：resume 推导时 ``stages_done`` 里的段须其 state 产物文件真实存在
（crash 落在 mark_done 与产物 write 之间的窗口 / 人工删产物都会造成漂移）——缺产物 → 视为未完成。

**SPEC 不变式边界（重要）**：SPEC §6「临时跳过的过闸 PRD 保留待投递，日跑不自动重试」是产品决策
——auto-resume 仅限**当日同 stamp** interrupted run 的段起点推导（checkpoint 文件按 stamp 命名，
次日新 stamp 自然找不到），绝不自动重投历史 pending PRD；「待投递 PRD」术语语义不变。

**graph 侧不穿透**：``PA_GRAPH_ORCHESTRATOR=1`` 时 cron 走 graph_pa.py（run_cron.sh 分流），不走
run_daily → 本模块不加载；graph 轨继续其 journal-driven recovery（D2/D3「无 Checkpointer」是有意
设计）。两个恢复体系按 flag 物理隔离，cutover 后本模块随 legacy 退役或平移评估。

**时间注入**：``now_fn`` 由调用方注入（ISO 字符串），本模块不触系统时间（同 ``circuit_breaker``
约定，保测试确定性）。纯 IO 模块（json/os/tempfile），零 git/SDK/journal 依赖——checkpoint 是
快照不是事件流，故不复用 journal.append_event。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 真值集合（strip + lower 后判定），与 feature_flags._TRUTHY 同集合——本地复制以避免反向耦合 loop flag 体系
_TRUTHY = frozenset({"1", "true", "yes", "on"})

CHECKPOINT_FLAG = "PA_CHECKPOINT"
AUTO_RESUME_FLAG = "PA_AUTO_RESUME"

#: 7 段链的**自动执行序**（resume 推导用）。inject 是手动旁路（替 radar→prd，须 --inject-prd，
#: 无法在无人补参时续跑）——走 inject 的 run 由 ``stages_done`` 含 "inject" 表达，推导时跳过。
AUTO_CHAIN: tuple[str, ...] = ("fetch", "radar", "prd", "critic", "dispatch", "report")

#: 各段 state 产物文件名模板（``stages_done`` 互证：说 done 但产物缺 → 视为未完成）。
#: report 产物在 vault 报告目录而非 state_dir，且 report 完成即 run 完成（无 resume 意义）→ None 不互证。
_STAGE_ARTIFACTS: dict[str, str | None] = {
    "fetch": "fetch_{stamp}.json",
    "radar": "candidates_{stamp}.json",
    "prd": "prd_manifest_{stamp}.json",
    "inject": "prd_manifest_{stamp}.json",
    "critic": "prd_gate_{stamp}.json",
    "dispatch": "dispatch_{stamp}.json",
    "report": None,
}

_STATUS_VALUES = frozenset({"running", "completed", "interrupted", "halted"})


def _warn(msg: str) -> None:
    """fail-open 告警走 stderr（不依赖调用方 logger；cron 重定向下仍可见）。"""
    print(f"[run_checkpoint] ⚠ {msg}", file=sys.stderr)


def _env_truthy(env: dict[str, str] | None, key: str) -> bool:
    """flag 真值判定（env=None 读 os.environ；单测传 ``{}`` 隔离）。"""
    env_map = os.environ if env is None else env
    v = env_map.get(key)
    return isinstance(v, str) and v.strip().lower() in _TRUTHY


def enabled(env: dict[str, str] | None = None) -> bool:
    """PA_CHECKPOINT 写点总门（off = 本模块全部写操作 no-op）。"""
    return _env_truthy(env, CHECKPOINT_FLAG)


def checkpoint_path(state_dir, stamp: str) -> Path:
    """checkpoint 文件路径：``<state_dir>/checkpoint_<stamp>.json``（按 run 起始 stamp 命名，次日自然分文件）。"""
    return Path(state_dir) / f"checkpoint_{stamp}.json"


def _atomic_write(path: Path, data: dict) -> None:
    """原子写（tmp + os.replace）：crash 不留半写文件（checkpoint 被 resume 消费，半写会误导推导）。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def new_checkpoint(stamp: str, now_fn) -> dict:
    """全新 checkpoint 骨架（首段 mark_done 时隐式创建）。"""
    return {"stamp": stamp, "actual_stamp": stamp, "status": "running",
            "stages_done": [], "last_stage": None,
            "started": now_fn(), "updated": now_fn(),
            "cost_usd": {}, "tripped_reason": None, "interrupted_reason": None}


def load_checkpoint(state_dir, stamp: str) -> dict | None:
    """读 checkpoint（resume 推导 / report 渲染用）。缺文件 → None；损坏 → None + 告警（fail-open：
    checkpoint 是恢复辅助而非安全准入门，坏文件不应阻断流水线——对齐「额外保护层 fail-open」惯例）。"""
    p = checkpoint_path(state_dir, stamp)
    if not p.is_file():
        return None
    try:
        ck = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        _warn(f"checkpoint 损坏（{p.name}: {e}）→ 视为无 checkpoint（fail-open，不阻断流水线）")
        return None
    if not isinstance(ck, dict) or not isinstance(ck.get("stages_done", []), list):
        _warn(f"checkpoint 结构非法（{p.name}）→ 视为无 checkpoint")
        return None
    return ck


def _mutate(state_dir, stamp: str, now_fn, fn) -> dict | None:
    """读-改-写公共骨架：flag off → no-op 返 None；文件损坏 → 以全新 checkpoint 自愈续写（非致命，
    快照丢了可从当前段重新累积——resume 推导有产物互证兜底，不信任过期 stages_done）。"""
    if not enabled():
        return None
    ck = load_checkpoint(state_dir, stamp)
    if ck is None:
        ck = new_checkpoint(stamp, now_fn)
    fn(ck)
    ck["updated"] = now_fn()
    try:
        _atomic_write(checkpoint_path(state_dir, stamp), ck)
    except OSError as e:
        _warn(f"checkpoint 写失败（{e}）→ 本次快照丢失（fail-open，不影响流水线主路径）")
        return None
    return ck


def mark_done(state_dir, stamp: str, stage: str, *, now_fn, actual_stamp: str | None = None) -> dict | None:
    """段完成记账（run_daily 各 stage 调用正常返回后调）。幂等：重复 mark 同段不重复 append。

    ``actual_stamp``：inject 段自增 stamp 后，调用方传自增后的 stamp 对齐产物文件名（缺省沿用旧值）。
    """
    def _apply(ck: dict) -> None:
        if actual_stamp:
            ck["actual_stamp"] = actual_stamp
        if stage not in ck["stages_done"]:
            ck["stages_done"].append(stage)
        ck["last_stage"] = stage
    return _mutate(state_dir, stamp, now_fn, _apply)


def mark_terminal(state_dir, stamp: str, *, status: str, reason: str | None = None, now_fn) -> dict | None:
    """终态落盘：completed（全链走完）/ interrupted（RuntimeError→exit 1 前）/ halted（熔断→exit 3 前）。

    中断/熔断原因一并记录（``tripped_reason`` 源自熔断、``interrupted_reason`` 源自普通失败——
    次日人工/告警可直接读出「为什么停」，不用翻 cron.log）。
    """
    if status not in _STATUS_VALUES:
        raise ValueError(f"非法 checkpoint 终态：{status}（合法值 {sorted(_STATUS_VALUES)}）")

    def _apply(ck: dict) -> None:
        ck["status"] = status
        if status == "halted":
            ck["tripped_reason"] = reason
        elif status == "interrupted":
            ck["interrupted_reason"] = reason
    return _mutate(state_dir, stamp, now_fn, _apply)


def add_cost(state_dir, stamp: str, stage: str, amount, *, now_fn) -> dict | None:
    """P2-1 成本累加：persona 调用成功后按段累加 ``meta['total_cost_usd']`` 实数（dispatch 段累加 dev_cost）。

    None / 非数 / 负数跳过（信封缺 cost 字段时不记账也不报错——成本聚合是观测面不是准入门）。
    """
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount < 0:
        return None

    def _apply(ck: dict) -> None:
        costs = ck.setdefault("cost_usd", {})
        costs[stage] = round(costs.get(stage, 0.0) + float(amount), 6)
    return _mutate(state_dir, stamp, now_fn, _apply)


def total_cost(ck: dict | None) -> float:
    """checkpoint 成本合计（report 概览行渲染用；无记录 → 0.0）。"""
    if not ck:
        return 0.0
    return round(sum(v for v in (ck.get("cost_usd") or {}).values() if isinstance(v, (int, float))), 6)


def stage_artifact_exists(state_dir, ck: dict, stage: str) -> bool:
    """产物互证：该段 state 产物文件是否真实存在（report 段无 state 产物 → 恒 True）。

    防「checkpoint 说 done 但盘上产物缺失」（crash 落在 mark_done 与产物 write 之间的窗口、
    人工删产物）：缺产物 → 视为未完成，resume 推导不信任该段。
    """
    tpl = _STAGE_ARTIFACTS.get(stage)
    if tpl is None:
        return True
    return (Path(state_dir) / tpl.format(stamp=ck.get("actual_stamp") or ck.get("stamp", ""))).is_file()


def verified_stages_done(ck: dict, state_dir) -> list[str]:
    """过产物互证的 stages_done 子集（resume 推导的可信输入）。"""
    return [s for s in (ck.get("stages_done") or []) if stage_artifact_exists(state_dir, ck, s)]


def derive_resume_from_stage(ck: dict, state_dir) -> str | None:
    """断点推导：自动链（AUTO_CHAIN）上第一个未可信完成的段。

    * inject 跳过（手动旁路无法自动补 --inject-prd；其完成度由 stages_done 含 "inject" 表达，
      之后从 critic 续）；
    * stages_done 为空 → None（无从推断原意图，不 resume）；
    * 全部完成 → None。
    """
    done = set(verified_stages_done(ck, state_dir))
    if not done:
        return None
    for stage in AUTO_CHAIN:
        if stage not in done:
            return stage
    return None


def maybe_auto_resume(state_dir, args, *, log_fn=None, env: dict[str, str] | None = None) -> bool:
    """P0-2 读点：``PA_AUTO_RESUME`` on 且当日 checkpoint ``status=interrupted`` → 自动推导 ``--from-stage``。

    守卫（全部命中才改 args.from_stage，否则原样返回 False）：
      * flag off / 无 checkpoint / 非 interrupted（completed/halted/running 不续——halted 须人工，
        running 是疑似 crash 未落终态，抢跑有双写风险）；
      * ``args.from_stage != "radar"``（argparse 默认值）→ 用户显式指定了起点，尊重手动控制；
      * ``args.inject_prd`` 已设 → 手动注入 run，不自动续；
      * 推导结果为 None（无从推断 / 全部完成）。
    返回 True 表示已改写 ``args.from_stage``（调用方 log 一行供 cron.log 追溯）。
    """
    def _log(msg: str) -> None:
        (log_fn or _warn)(msg)

    if not _env_truthy(env, AUTO_RESUME_FLAG):
        return False
    if getattr(args, "inject_prd", None):
        return False
    if args.from_stage != "radar":   # argparse 默认起点（cron 路径恒为此）；非默认 = 手动指定，不抢
        return False
    ck = load_checkpoint(state_dir, args.stamp)
    if ck is None or ck.get("status") != "interrupted":
        return False
    derived = derive_resume_from_stage(ck, state_dir)
    if derived is None:
        return False
    args.from_stage = derived
    _log(f"[checkpoint] ↻ auto-resume：{args.stamp} 中断于 {ck.get('last_stage')}（{ck.get('interrupted_reason')}）"
         f"→ 续跑 --from-stage {derived}")
    return True
