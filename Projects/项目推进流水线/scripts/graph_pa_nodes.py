#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""graph_pa_nodes.py — 4 类通用 node 工厂 + node_radar 配置实例（langgraph-workflow-upgrade 2.3/2.5）。

design D3 / spec「四类通用 node 抽象」+「verdict 仅 PersonaNode 可写」。7 阶段 = 这 4 类的配置实例
（新加 stage = 配置实例 + 加 graph 边，零新 node 代码）。verdict 类型层只在 PersonaNode 暴露写入；
MechanicalNode/GatewayNode/DevLoopNode 的工厂**不提供** verdict 写入接口，commit_node 运行时守 +
check_boundary.py 静态 lint 双保险（D3/R6）。

4 类（D3）：
    PersonaNode    控制面语义：subprocess 调 persona + 两层解析 + 契约校验 + 1 次 repair-hint 重试
                   （复用 run_daily.run_persona；run_persona 内已含 repair-hint 1 次重试）
    DevLoopNode    目标面语义：SDK dev loop + exit 14/15/12 + worktree + session（Phase 1 骨架，Phase 2 完整）
    MechanicalNode 零 LLM 机械活：文件发现/去重/落盘/聚合/SMTP/install-test
    GatewayNode    fail-safe 门：三态/测试门/reconcile/single_flight/circuit_breaker，UNKNOWN→blocked

node 函数签名：(state: GraphState) -> dict（langgraph state 更新片段；返回的 key 由 graph reducer 合并）。
node 内不 mutate state，只返回 update。绝对路径不入 state（node 内 resolve_handle 即时解析，R8）。

journal 单写（D2）：commit_node 内校验；append_event(fsync) 接线留 Phase 2 任务 3.7（journal 单写真源）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import graph_pa_contracts as C


# ── node 种类（verdict 写入权限的类型层锚点，D3/R6）──────────────────────
@dataclass(frozen=True)
class NodeKind:
    name: str
    write_verdict: bool            # 仅 PersonaNode True


KIND_PERSONA = NodeKind("persona", True)
KIND_DEVLOOP = NodeKind("devloop", False)
KIND_MECHANICAL = NodeKind("mechanical", False)
KIND_GATEWAY = NodeKind("gateway", False)


def _ik(state_or_ni: dict, stage: str) -> str:
    """idempotency_key：run_id:stage[:project]（exactly-once reconcile，喂 reconcile.py）。"""
    proj = state_or_ni.get("_project", "")
    return f"{state_or_ni.get('run_id', '?')}:{stage}:{proj}"


def _ni(state: dict, *, stage: str, **extra) -> C.NodeInput:
    """从 graph state 构造 NodeInput（node 入参）。绝对路径由调用方经 vault_root/state_dir 注入。"""
    ni: dict = {"run_id": state.get("run_id", ""), "thread_id": state.get("thread_id", ""),
                "stamp": state.get("stamp", ""), "stage": stage, "config": state.get("config", {})}
    # 转发 state 的 `_` 前缀 str 标识字段（per-stage 标识：_project/_src_name/_prd_path/...），
    # 供 _ik 幂等键 + per-stage label 解析；大对象（_profiles/_candidates/...）不进 ni
    # （build_prompt/to_state 直接读 state，避免污染 NodeInput 契约）。
    for k, v in state.items():
        if k.startswith("_") and isinstance(v, str):
            ni[k] = v
    ni.update(extra)
    return ni  # type: ignore[return-value]


def commit_node(kind: NodeKind, ni: C.NodeInput, out: C.NodeOutput) -> C.NodeOutput:
    """通用 node 提交：校验 NodeInput/NodeOutput + verdict 边界运行时守（D3/R6）。

    verdict 边界：非 PersonaNode 产 verdict → raise（边界滑落）。这是运行时守；静态守由
    check_boundary.py lint（扫「非 persona node 产 verdict」）。journal append_event(fsync) 接线
    留 Phase 2 任务 3.7（node 内先 journal fsync 再 return state，D2 单写真源）。
    """
    C.validate_node_input(ni)
    C.validate_node_output(out)
    if not kind.write_verdict and out.get("verdict") is not None:
        raise C.ContractError(
            f"{kind.name} node 产 verdict（边界滑落，D3/R6——仅 PersonaNode 可写语义判决）")
    # TODO(phase2/3.7): journal.append_event(kind.name, ni, out, fsync=True)
    return out


# ── PersonaNode（控制面语义；唯一可写 verdict）──────────────────────────
def _default_verdict_mapper(payload: dict) -> dict:
    """默认 verdict 映射：payload.{verdict,reason,feedback} → NodeOutput.verdict。"""
    return {"value": payload.get("verdict"), "reason": payload.get("reason", ""),
            "feedback": payload.get("feedback", "")}


def make_persona_node(*, agent_name: str, stage: str, label,
                     allowed_tools: list[str] | None = None,
                     build_prompt: Callable[[dict], str],
                     extract_artifacts: Callable[[dict, dict], list] | None = None,
                     expose_verdict: bool = False,
                     to_state: Callable[[dict, dict], dict] | None = None,
                     verdict_mapper: Callable[[dict], dict] | None = None):
    """PersonaNode 工厂（唯一可写 verdict，D3/R6）。

    label: str | Callable[[NodeInput], str]   固定 label 或运行期按 ni（含 _project）算 label
                   （对齐 stage_radar 的 f"radar-{proj}" 等 per-project label）。
    build_prompt(state)->str      编译期注入 prompt 构造（复用 run_daily.radar_prompt / prd_prompt 等）。
    extract_artifacts(payload, ni)->list   产物 ArtifactHandle 提取（None→空）。
    expose_verdict               critic/progress 等对抗 persona 暴露 verdict；radar/prd/fetch 不产 verdict。
    to_state(payload, state)->dict         把 persona payload 映射成 state 更新片段（Phase 2 各 stage 定制）。

    返回 node 函数 (state)->update；node.invoke(ni, prompt)->(NodeOutput, payload) 供测试/复用。
    """
    def invoke(ni: C.NodeInput, prompt: str):
        import run_daily       # 延迟 import（降加载成本；同目录扁平，monkeypatch 友好）
        import stage_contracts
        lbl = label(ni) if callable(label) else label        # per-project label（对齐 stage_radar f"radar-{proj}"）
        payload, meta = run_daily.run_persona(agent_name, prompt, stage, lbl, allowed_tools)
        stage_contracts.validate_stage(stage, payload)    # fail-open（外部 persona 宽容，不改终态）
        out: dict = {"status": C.STATUS_OK, "obs": C.obs_from_meta(meta),
                     "idempotency_key": _ik(ni, stage)}
        out["artifacts"] = list(extract_artifacts(payload, ni) or []) if extract_artifacts else []
        if expose_verdict and isinstance(payload, dict):
            vm = verdict_mapper or _default_verdict_mapper
            v = vm(payload)
            if v.get("value"):
                out["verdict"] = v
        return commit_node(KIND_PERSONA, ni, out), payload   # type: ignore[return-value]

    def node(state: dict) -> dict:
        ni = _ni(state, stage=stage)
        out, payload = invoke(ni, build_prompt(state))
        update: dict = {"obs_log": [out["obs"]]}
        if to_state:
            update.update(to_state(payload, state) or {})
        return update

    node._kind = KIND_PERSONA              # type: ignore[attr-defined]
    node._cfg = {"agent_name": agent_name, "stage": stage, "label": label}   # type: ignore[attr-defined]
    node.invoke = invoke                    # type: ignore[attr-defined]
    return node


# ── MechanicalNode（零 LLM 机械活；不产 verdict）────────────────────────
def make_mechanical_node(*, stage: str, op: Callable[[dict, dict], tuple]):
    """op(ni, state)->(artifacts: list, state_extra: dict, obs: dict)。零 LLM；不产 verdict（D3）。

    op 收 (ni, state)：ni 是 NodeInput（run_id/stage/config/_project/_src_name 等标识），
    state 是完整 graph state——机械活常需读 _profiles/_inject_prd/_candidates 等运行期输入
    （Phase 1 骨架占位 op(ni)，Phase 2 接线 task 3.2 inject 需 state 访问，演进为 op(ni, state)）。
    """
    def invoke(ni: C.NodeInput, state: dict):
        artifacts, extra, obs = op(ni, state)
        out: dict = {"status": C.STATUS_OK, "obs": obs or {}, "artifacts": list(artifacts or []),
                     "idempotency_key": _ik(ni, stage)}
        return commit_node(KIND_MECHANICAL, ni, out), (extra or {})

    def node(state: dict) -> dict:
        ni = _ni(state, stage=stage)
        out, extra = invoke(ni, state)
        update: dict = {"obs_log": [out["obs"]]}
        update.update(extra)
        return update

    node._kind = KIND_MECHANICAL; node._cfg = {"stage": stage}; node.invoke = invoke   # type: ignore[attr-defined]
    return node


# ── GatewayNode（fail-safe 门；UNKNOWN→blocked；不产 verdict）────────────
def make_gateway_node(*, stage: str, check: Callable[[dict], tuple]):
    """check(ni)->(passed: bool, error: dict|None)。passed=False/三态 UNKNOWN → status=blocked（机械硬门，D6）。"""
    def invoke(ni: C.NodeInput):
        passed, error = check(ni)
        out: dict = {"obs": {}, "artifacts": [], "idempotency_key": _ik(ni, stage)}
        out["status"] = C.STATUS_OK if passed else C.STATUS_BLOCKED
        if not passed:
            out["error"] = error or {"code": C.ERR_UNKNOWN_REMOTE, "message": "gateway 阻断（fail-safe）"}
        return commit_node(KIND_GATEWAY, ni, out)

    def node(state: dict) -> dict:
        ni = _ni(state, stage=stage)
        out = invoke(ni)
        update: dict = {"obs_log": [out["obs"]]}
        if out["status"] != C.STATUS_OK:
            update["terminal"] = out["status"]    # 条件边机械路由 enum 终态（不替判）
        return update

    node._kind = KIND_GATEWAY; node._cfg = {"stage": stage}; node.invoke = invoke   # type: ignore[attr-defined]
    return node


# ── DevLoopNode（目标面语义；Phase 1 骨架，Phase 2 任务 3.5 完整）─────────
DEV_EXIT_TEST_GATE = 14      # 测试发布门 GATE_FAILED/NOT_RUN/STALE → blocked（机械硬门）
DEV_EXIT_OFF_TRACK = 15      # pa-progress off_track 连续 → triaged（升人工）
DEV_EXIT_BRAKE = 12          # 刹车 → triaged


def parse_dev_exit(code: int) -> tuple[str | None, str | None]:
    """dev-agent.py exit code → (terminal_status|None, error_code|None)。机械映射，不替判（D6）。

    0→(None,None) 正常；14→blocked/test_gate；15/12→triaged；未知非 0→triaged（升人工，不替判死）。
    """
    if code == DEV_EXIT_TEST_GATE:
        return C.STATUS_BLOCKED, C.ERR_TEST_GATE
    if code in (DEV_EXIT_OFF_TRACK, DEV_EXIT_BRAKE):
        return C.STATUS_TRIAGED, C.ERR_CONTRACT_VIOLATION
    if code == 0:
        return None, None
    return C.STATUS_TRIAGED, C.ERR_PERSONA_CRASH


def make_devloop_node(*, stage: str = "dispatch"):
    """DevLoopNode 工厂（Phase 1 骨架）。

    Phase 2 任务 3.5 完整：调 dev-agent.py subprocess + claude-agent-sdk dev loop + worktree + session
    续接 + single_flight/circuit_breaker/merge_loop/reconcile 协同（留 2 周，R4）。store=worktree 只此 node 写。
    """
    def node(state: dict) -> dict:
        raise NotImplementedError(
            "DevLoopNode 完整迁移在 Phase 2 任务 3.5：dev-agent.py subprocess + 容错协同（留 2 周，R4）")
    node._kind = KIND_DEVLOOP; node._cfg = {"stage": stage}   # type: ignore[attr-defined]
    return node


# ── node_radar 配置实例（任务 2.5）──────────────────────────────────────
# radar 调 pa-radar persona（抽技术信号 = 控制面语义）→ PersonaNode（spec D3；radar 不产 verdict）。
# stage_radar 的机械活（discover_today_new/fetch_dedup_list/stats/marker bump）拆为前置/后置
# MechanicalNode + GatewayNode 留 Phase 2；Phase 1 node_radar 是 PersonaNode 配置实例，复用 run_persona
# 逻辑主体零重写（spec「新加 stage 不写新 node 代码」+「node 复用 run_daily 纯函数」）。
def _radar_build_prompt(state: dict) -> str:
    import run_daily
    proj = state["_project"]
    return run_daily.radar_prompt(proj, state["_today_new"], state["_profiles"][proj],
                                  state.get("_dedup", []))


def _radar_extract(payload: dict, ni: dict) -> list:
    stamp = ni.get("stamp", "")
    return [{"kind": "candidates", "store": C.STORE_TMP,
             "rel_path": f"candidates_{stamp}.json"}]


def _radar_to_state(payload: dict, state: dict) -> dict:
    # Phase 2 把 radar payload 写入 state['candidates']；Phase 1 先暂存 _radar_payload 供 byte-identical spike
    return {"_radar_payload": payload}


# label 运行期对齐 stage_radar 的 f"radar-{proj}"（调用形态一致，任务 2.6）
node_radar = make_persona_node(
    agent_name="pa-radar", stage="radar", label=lambda ni: f"radar-{ni.get('_project', '')}",
    build_prompt=_radar_build_prompt, extract_artifacts=_radar_extract,
    expose_verdict=False, to_state=_radar_to_state)


# ── fetch node 配置实例（任务 3.1：3 个 persona 配置实例）──────────────
# fetch stage 按 source kind 分发 3 个 persona（stage_fetch L758-766 + FETCH_CONFIG L720-733）：
#   agent-deepresearch（pa-fetch-deepresearch，exa tools，mode=single 一份合成 md）
#   wechat-url（pa-fetch-wechat-url，web_reader+exa，mode=items N 篇）
#   github-repo（pa-fetch-github-repo，Bash gh CLI，mode=items）
# radar=1 个 persona、fetch=3 个 persona，均 PersonaNode（语义采集 = 控制面语义，spec D3）。
# label per-source f"fetch-{src['name']}"（对齐 stage_fetch L766）；prompt 各自（延迟 import run_daily.*_prompt）。
# 落盘（write_text）+ items 拆分（_payload_to_items）是机械活，留 MechanicalNode（Phase 2 后续 task）；
# 此处 PersonaNode 只暂存 payload（同 node_radar 模式）。fetch 产物 store=vault → digest 强制（OQ3），
# Phase 1 未落盘无文件可算 digest → extract_artifacts 不设（落盘 MechanicalNode 后产 ArtifactHandle）。
_FETCH_TOOLS = {                                        # 工具白名单镜像 run_daily.FETCH_CONFIG（test_graph_fetch 校验一致性防漂移）
    "agent-deepresearch": ["mcp__plugin_ecc_exa__web_search_exa", "mcp__plugin_ecc_exa__web_fetch_exa"],
    "wechat-url": ["mcp__web_reader__webReader", "mcp__plugin_ecc_exa__web_fetch_exa"],
    "github-repo": ["Bash"],
}


def _fetch_label(ni: dict) -> str:
    return f"fetch-{ni.get('_src_name', '')}"           # 对齐 stage_fetch L766 f"fetch-{src['name']}"


def _make_fetch_build(prompt_attr: str):
    """build_prompt(state) → run_daily.<prompt_attr>(state['_src'])。延迟 import（monkeypatch 友好）。"""
    def build(state: dict) -> str:
        import run_daily
        return getattr(run_daily, prompt_attr)(state["_src"])
    return build


def _fetch_to_state(payload: dict, state: dict) -> dict:
    return {"_fetch_payload": payload}                  # Phase 1 暂存 payload（落盘留 MechanicalNode）


node_fetch_deepresearch = make_persona_node(
    agent_name="pa-fetch-deepresearch", stage="fetch", label=_fetch_label,
    allowed_tools=_FETCH_TOOLS["agent-deepresearch"],
    build_prompt=_make_fetch_build("fetch_prompt"),
    expose_verdict=False, to_state=_fetch_to_state)

node_fetch_wechat = make_persona_node(
    agent_name="pa-fetch-wechat-url", stage="fetch", label=_fetch_label,
    allowed_tools=_FETCH_TOOLS["wechat-url"],
    build_prompt=_make_fetch_build("wechat_url_prompt"),
    expose_verdict=False, to_state=_fetch_to_state)

node_fetch_github = make_persona_node(
    agent_name="pa-fetch-github-repo", stage="fetch", label=_fetch_label,
    allowed_tools=_FETCH_TOOLS["github-repo"],
    build_prompt=_make_fetch_build("github_repo_prompt"),
    expose_verdict=False, to_state=_fetch_to_state)


# ── prd node 配置实例（任务 3.2）──────────────────────────────────────
# prd 调 pa-prd persona（candidates × profile → 项目专属 PRD = 控制面语义）→ PersonaNode（spec D3）。
# stage_prd L907 调用形态：run_persona("pa-prd", prd_prompt(candidates, profiles, stamp), "prd", "prd")
# label 固定 "prd"（batch 翻译所有 candidates，非 per-project；对齐 stage_prd L907 第 4 参）。
# prd 不产 verdict（pa-prd 产 prds/skipped；critic 才产 verdict，spec D3 verdict 仅对抗 persona）。
# pa-prd persona 内部 Write 落盘 PRD md（.project-auto/state/prd/<proj>/<stamp>_<slug>.md，控制面 vault）；
# manifest 落盘（prd_manifest_<stamp>.json）+ candidates 空跳过是机械活，留 MechanicalNode（Phase 2 后续）。
# revise 入口（prd_prompt revise 参数）占位——critic revise 回环接 critic 子图（task 3.3）。
def _prd_build_prompt(state: dict) -> str:
    import run_daily
    return run_daily.prd_prompt(state["_candidates"], state["_profiles"], state["stamp"])


def _prd_to_state(payload: dict, state: dict) -> dict:
    return {"_prd_manifest": payload}                  # Phase 1 暂存 manifest（落盘留 MechanicalNode）


node_prd = make_persona_node(
    agent_name="pa-prd", stage="prd", label="prd",      # label 固定（batch，对齐 stage_prd L907）
    build_prompt=_prd_build_prompt,
    expose_verdict=False, to_state=_prd_to_state)


# ── inject node 配置实例（任务 3.2）───────────────────────────────────
# inject 是手动注入入口（--inject-prd md → manifest），替 radar→prd 自动路径（stage_inject L1102）。
# 纯机械活（零 LLM：frontmatter 解析 + slug + copy + manifest）→ MechanicalNode（spec D3）。
# op 复用 run_daily.stage_inject（零重写逻辑主体）；stage_inject 内 sys.exit（输入错）穿透 node
# （Phase 2 拓扑接线时包装为 graph 终态，task 3.9）。actual_stamp 可能自增（避碰），透传供下游对齐。
def _inject_op(ni: dict, state: dict):
    import run_daily
    from argparse import Namespace
    args = Namespace(inject_prd=state.get("_inject_prd"))   # stage_inject 只读 args.inject_prd（L1111）
    manifest, actual = run_daily.stage_inject(args, state.get("_profiles", {}), ni.get("stamp", ""))
    return ([], {"_prd_manifest": manifest, "_inject_stamp": actual},
            {"inject_stamp": actual, "n_prds": len(manifest.get("prds", []))})


node_inject = make_mechanical_node(stage="inject", op=_inject_op)


# ── critic node 配置实例（任务 3.3，critic 子图的 PersonaNode 组件）─────────
# critic 调 pa-prd-critic persona（PRD 对抗质量闸 = 控制面语义）→ PersonaNode（spec D3）。
# critic 是首个 expose_verdict=True 的 PersonaNode（产 pass/revise/drop 语义判决，D3 唯一 verdict 写入）。
# _critic_one L970 调用形态：run_persona("pa-prd-critic", critic_prompt(path,source_path,prof), "critic", f"critic:{stem}")
# label per-prd f"critic:{stem}"（对齐 _critic_one L969）；allowed_tools=None。
# verdict 提取供条件边路由（critic 子图 route_critic）；to_state 暂存 _critic_payload（含 revisions_needed 供 revise）。
def _critic_build_prompt(state: dict) -> str:
    import run_daily
    return run_daily.critic_prompt(state["_prd_path"], state.get("_source_path", ""), state["_prof"])


def _critic_label(ni: dict) -> str:
    from pathlib import Path
    return f"critic:{Path(ni.get('_prd_path', 'x')).stem}"   # 对齐 _critic_one L969


def _critic_to_state(payload: dict, state: dict) -> dict:
    return {"_critic_payload": payload, "_critic_verdict": payload.get("verdict")}


def _critic_verdict_mapper(payload: dict) -> dict:
    """critic payload.{verdict,summary,issues} → NodeOutput.verdict。
    pa-prd-critic 吐 summary/issues（非 reason/feedback）；summary 作 reason（validate 要求非空），
    issues join 作 feedback（revise 时 revisions_needed 已在 _critic_payload 供 revise node）。"""
    return {"value": payload.get("verdict"),
            "reason": payload.get("summary") or payload.get("verdict", ""),
            "feedback": "; ".join(payload.get("issues") or []) or payload.get("summary", "")}


node_critic = make_persona_node(
    agent_name="pa-prd-critic", stage="critic", label=_critic_label,
    build_prompt=_critic_build_prompt,
    expose_verdict=True, to_state=_critic_to_state,
    verdict_mapper=_critic_verdict_mapper)


# ── prd revise node 配置实例（任务 3.3，critic 子图的 revise 回环节点）─────
# revise 回环：critic verdict=revise → 调 pa-prd round2（按 revisions_needed 修订）→ 回 critic round2。
# stage_critic L951-952 调用形态：run_persona("pa-prd", prd_prompt([], profiles, stamp, revise=rev), "prd", f"prd-revise:{proj}")
# rev = {prd_path, revisions_needed}（来自 critic payload）；label per-project f"prd-revise:{proj}"。
# 复用 node_prd 的 agent（pa-prd）；不产 verdict（prd 不产 verdict，critic 才产）。
def _prd_revise_build_prompt(state: dict) -> str:
    import run_daily
    rev = {"prd_path": state["_prd_path"],
           "revisions_needed": (state.get("_critic_payload") or {}).get("revisions_needed", [])}
    return run_daily.prd_prompt([], state["_profiles"], state["stamp"], revise=rev)


node_prd_revise = make_persona_node(
    agent_name="pa-prd", stage="prd", label=lambda ni: f"prd-revise:{ni.get('_project', '')}",
    build_prompt=_prd_revise_build_prompt,
    expose_verdict=False, to_state=lambda p, s: {"_revised_prd": p})
