"""handoff.py — dev 亲笔交接契约（OpenSpec add-dev-handoff-artifact，纯 stdlib 零 SDK）。

dev 会话三类断点（verify revise round2 / stalled·off_track retry / hub 崩溃续跑）后，新会话缺
「上一 iteration dev 的结构化自述」。本模块承载其全部机械面（同 ``evidence``/``slug_utils``
既定模式：无依赖模块，单测零 SDK 导入锁定红绿分支）：

    * 产出（写侧，D2）：dev 在 SDK loop 的 assistant 回复末尾携带 ```handoff``` 围栏块
      （内为 D1 JSON）；harness 经 ``parse_handoff_block``/``absorb_assistant_text`` 解析暂存
      ``state["handoff_latest"]``，并原子快照 ``run_dir/handoff-latest.json``（K-turn 检查点，
      断点③进程被杀时留最后一次快照——收养路径的输入）。
    * 契约（D1）：六字段结构化（done/in_progress/blockers/tried_and_failed/next/rationale），
      列表项 ≤200、rationale ≤500、总量 ≤4KB——防叙事倾泻污染 prompt。缺失字段补空值
      （诚实缺失，绝不伪造内容）；未知字段丢弃（契约封闭）。
    * 注入（读侧，D4）：``render_block`` 渲染为 prompt 独立标记块（「上一轮 dev 交接（仅上下文，
      非判据）」——untrusted 边界）；``digest_of``/``verify_digest`` 与
      ``artifact_store.compute_digest`` 同格式，落盘后被改 → 注入侧 fail-closed 拒绝。

untrusted 边界（spec durable-loop-state/session-aware-retry）：handoff 只作 prompt 增补与
recovery summary 的 decisions/next-action 自述源，**绝不进任何 verdict/gate/retry 决策判定**。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

# ─── D1 契约常量 ────────────────────────────────────────────────────────
HANDOFF_FIELDS: tuple[str, ...] = ("done", "in_progress", "blockers", "tried_and_failed", "next")
ITEM_MAX = 200        # 列表项长度上限（字符）
RATIONALE_MAX = 500   # rationale 长度上限（字符）
TOTAL_MAX = 4096      # 规整后总量上限（UTF-8 字节）——超限整块拒收

# 快照文件名（run_dir = run_log 所在目录；单文件「最新版」语义，同 PRD 跨轮覆盖为最新）
SNAPSHOT_NAME = "handoff-latest.json"

# assistant 回复中的 ```handoff``` 围栏块（非贪婪到闭合 ```；多块取最新）
_FENCE_RE = re.compile(r"```handoff[ \t]*\r?\n(.*?)```", re.S)


def validate_handoff(obj) -> dict | None:
    """按 D1 契约规整 handoff 对象；非法输入 → None。

    规则：非 dict → None；五列表字段（done/in_progress/blockers/tried_and_failed/next）
    非 list → 空列表（不猜形状），None 项剔除，str 化后截到 ``ITEM_MAX``；
    ``rationale`` str 化截到 ``RATIONALE_MAX``；未知字段丢弃（契约封闭）；
    规整后序列化 > ``TOTAL_MAX`` 字节 → None（拒收，防倾泻）。缺失字段补空值——
    补的是空容器/空串，**绝不伪造内容**（spec「Absent handoff is not fabricated」）。
    """
    if not isinstance(obj, dict):
        return None
    out: dict = {}
    for f in HANDOFF_FIELDS:
        v = obj.get(f)
        out[f] = [str(x)[:ITEM_MAX] for x in v if x is not None] if isinstance(v, list) else []
    r = obj.get("rationale")
    out["rationale"] = str(r)[:RATIONALE_MAX] if r is not None else ""
    if len(json.dumps(out, ensure_ascii=False).encode("utf-8")) > TOTAL_MAX:
        return None
    return out


def parse_handoff_block(text: str) -> dict | None:
    """从 assistant 回复文本提取 ```handoff``` 围栏 JSON，返回规整后的契约 dict。

    多块取最新（dev 阶段性更新，末版为准）；无块 / 坏 JSON / 超总量 → None
    （解析失败静默跳过，下个检查点补——D2）。
    """
    matches = _FENCE_RE.findall(text or "")
    if not matches:
        return None
    try:
        obj = json.loads(matches[-1])
    except json.JSONDecodeError:
        return None
    return validate_handoff(obj)


# ─── 快照（写侧 K-turn 落盘；断点③收养路径的输入）────────────────────────
def snapshot_path(run_dir) -> Path:
    return Path(run_dir) / SNAPSHOT_NAME


def write_snapshot(run_dir, handoff: dict) -> bool:
    """原子写 ``run_dir/handoff-latest.json``（tmp + rename；崩溃不留半写文件）。

    失败（目录不可写等）→ False 不炸：快照尽力而为，绝不掩 dev loop 主流程。
    """
    try:
        target = snapshot_path(run_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(handoff, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)   # os.rename 原子替换（同目录同文件系统）
        return True
    except Exception:
        return False


def load_snapshot(run_dir) -> dict | None:
    """读回最近快照；不存在/坏 JSON → None（正常缺失，非错误）。"""
    try:
        return json.loads(snapshot_path(run_dir).read_text(encoding="utf-8"))
    except Exception:
        return None


# ─── 写侧接线（dev-agent process_dev_loop 的 AssistantMessage 处理处调用）──
def absorb_assistant_text(state: dict, text: str, run_dir=None) -> dict | None:
    """解析 assistant 文本中的 handoff 块：新块 → 更新 ``state["handoff_latest"]`` + 落快照。

    每 AssistantMessage 调一次（自然每 turn 检查，与 pa-progress K=10 抽查独立共存，无共享状态）。
    无块/坏块 → 不动 state（保持上一最新块）、不写快照。返回本次吸收的新块（None=无新块）。
    """
    parsed = parse_handoff_block(text)
    if parsed is None:
        return None
    state["handoff_latest"] = parsed
    if run_dir is not None:
        write_snapshot(run_dir, parsed)
    return parsed


# ─── 读侧（注入）：digest 校验 + 固定框架渲染 ────────────────────────────
def digest_of(content: str | bytes) -> str:
    """算 SHA-256 digest，``sha256:<hex>``——与 ``artifact_store.compute_digest`` 同格式
    （控制面 ArtifactRef.digest ⇄ dev-agent 注入侧校验用同一值域）。"""
    b = content.encode("utf-8") if isinstance(content, str) else content
    return "sha256:" + hashlib.sha256(b).hexdigest()


def verify_digest(content: str | bytes, expected: str) -> bool:
    """注入前完整性校验：内容 digest 是否匹配 journal 记录的期望值（fail-closed 判据）。"""
    return isinstance(expected, str) and digest_of(content) == expected


def render_block(content: str) -> str:
    """渲染注入块：固定标记框架 + untrusted 边界声明（仅上下文，非判据——D4）。"""
    return (f"\n\n## 上一轮 dev 交接（仅上下文，非判据）\n"
            f"```\n{content}\n```")
