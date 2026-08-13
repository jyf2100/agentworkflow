#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_feature_flags.py — Loop runtime 渐进启用开关单测（OpenSpec add-durable-loop-runtime task 1.3）。

design 决策#8：第二阶段所有新能力（journal / hooks / retry / sandbox / telemetry）默认 **全关**，
以 shadow mode 旁路运行、不改 dispatch 决策；逐项 canary 一致后再翻 flag 切到「驱动」。本测试锁定：

    - 默认全关（无 env、无 profile）→ 行为与第一阶段完全一致（baseline 不变）；
    - 6 个 flag 各自存在且默认 False；
    - 环境变量 truthy 开启；falsy 关闭；
    - profile.loop 可开启；
    - 环境变量优先于 profile（运维 kill switch 一键关回旧逻辑）。

跑：python3 -m pytest scripts/test_feature_flags.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from feature_flags import LoopFlags, resolve_flags, FLAGS_ENV_MAP  # noqa: E402


# ─── 默认全关（baseline 不变的最强断言）─────────────────────────────────
def test_defaults_all_off():
    """无 env、无 profile → 全 False。第一阶段 dispatch 行为零变化（design 决策#8）。"""
    # Arrange / Act
    flags = resolve_flags(env={})   # 显式空 env，免 os.environ 耦合（CI 若预置 PA_LOOP_* 不 flaky）
    # Assert
    assert flags == LoopFlags()


def test_six_flags_exist_and_default_false():
    """6 个 flag（task 1.3 列举）都在，默认 False。"""
    flags = LoopFlags()
    for name in ["journal_shadow", "journal_driven_dispatch", "session_aware_retry",
                 "lifecycle_hooks", "container_sandbox", "telemetry_export"]:
        assert hasattr(flags, name), f"LoopFlags 缺字段: {name}"
        assert getattr(flags, name) is False, f"{name} 默认非 False（baseline 会变）"


# ─── 环境变量 truthy 开启 ───────────────────────────────────────────────
def test_env_enables_single_flag():
    """PA_LOOP_JOURNAL_SHADOW=1 → 只开 journal_shadow，其余仍关。"""
    # Arrange / Act
    flags = resolve_flags(env={"PA_LOOP_JOURNAL_SHADOW": "1"})
    # Assert
    assert flags.journal_shadow is True
    assert flags.journal_driven_dispatch is False
    assert flags.lifecycle_hooks is False


def test_env_truthy_falsy_variants():
    """truthy: 1/true/yes/on（大小写不敏感）；falsy: 0/false/no/off/空/乱串。"""
    truthy = ["1", "true", "TRUE", "True", "yes", "on", "YES", "On"]
    for v in truthy:
        assert resolve_flags(env={"PA_LOOP_LIFECYCLE_HOOKS": v}).lifecycle_hooks is True, f"{v!r} 应为 truthy"
    falsy = ["0", "false", "no", "off", "", "random", "2", "false "]
    for v in falsy:
        assert resolve_flags(env={"PA_LOOP_LIFECYCLE_HOOKS": v}).lifecycle_hooks is False, f"{v!r} 应为 falsy"


def test_all_env_names_mapped():
    """FLAGS_ENV_MAP 覆盖所有 flag，且 env 名稳定（运维/CI 文档化的开关名）。

    add-cross-prd-learning-memory task 1.3a/1.3b 追加 cross_prd_learning_shadow / cross_prd_learning_injection，
    **不带 PA_LOOP_ prefix，有意域切割**（learning memory 是独立能力域）。harden-pa-verify-determinism
    task 4.0 追加 verify_anchor_evidence（pa-verify 质量闸域，同样不带 PA_LOOP_ prefix）。"""
    assert FLAGS_ENV_MAP == {
        "journal_shadow": "PA_LOOP_JOURNAL_SHADOW",
        "journal_driven_dispatch": "PA_LOOP_JOURNAL_DRIVEN_DISPATCH",
        "session_aware_retry": "PA_LOOP_SESSION_AWARE_RETRY",
        "lifecycle_hooks": "PA_LOOP_LIFECYCLE_HOOKS",
        "container_sandbox": "PA_LOOP_CONTAINER_SANDBOX",
        "telemetry_export": "PA_LOOP_TELEMETRY_EXPORT",
        # task 1.3a/1.3b：learning memory 双 flag（域切割，不带 PA_LOOP_ prefix）
        "cross_prd_learning_shadow": "PA_LEARNING_SHADOW",
        "cross_prd_learning_injection": "PA_LEARNING_INJECTION",
        # single-flight-auto-merge task 1.1：dispatch 串行单飞 + auto-merge 双 flag（域切割，PA_SINGLE_FLIGHT_ 前缀）
        "single_flight_serial_shadow": "PA_SINGLE_FLIGHT_SERIAL_SHADOW",
        "single_flight_auto_merge": "PA_SINGLE_FLIGHT_AUTO_MERGE",
        # harden-pa-verify-determinism task 4.0：verify 锚点域切割（不带 PA_LOOP_ prefix）
        "verify_anchor_evidence": "PA_VERIFY_ANCHOR_EVIDENCE",
        # langgraph-workflow-upgrade task 5.1：graph 编排器双 flag（域切割，PA_GRAPH_ 前缀——
        # 编排器主图切换是独立能力域，不属于 loop runtime 6 大渐进启用面）
        "pa_graph_shadow": "PA_GRAPH_SHADOW",
        "pa_graph_orchestrator": "PA_GRAPH_ORCHESTRATOR",
    }


# ─── profile.loop 可开启（per-project canary）──────────────────────────
def test_profile_loop_enables_flag():
    """profile.loop.telemetry_export=True → 开 telemetry（单项目 canary）。"""
    flags = resolve_flags(env={}, profile={"loop": {"telemetry_export": True}})
    assert flags.telemetry_export is True
    assert flags.container_sandbox is False


def test_profile_loop_falsy_keeps_off():
    """profile.loop 里显式 False / 缺省 → 关。"""
    assert resolve_flags(env={}, profile={"loop": {"container_sandbox": False}}).container_sandbox is False
    assert resolve_flags(env={}, profile={"loop": {}}).container_sandbox is False
    assert resolve_flags(env={}, profile={}).container_sandbox is False


# ─── 环境变量优先于 profile（kill switch）──────────────────────────────
def test_env_overrides_profile_when_env_set():
    """env 显式设置时压过 profile（运维可用 env 一键关回旧逻辑，忽略 profile 的 canary 开启）。"""
    # env=false 压过 profile=True
    flags = resolve_flags(env={"PA_LOOP_CONTAINER_SANDBOX": "false"},
                          profile={"loop": {"container_sandbox": True}})
    assert flags.container_sandbox is False
    # env=true 压过 profile=False
    flags = resolve_flags(env={"PA_LOOP_CONTAINER_SANDBOX": "true"},
                          profile={"loop": {"container_sandbox": False}})
    assert flags.container_sandbox is True


def test_profile_used_when_env_absent():
    """env 未设（None / 缺键）→ 用 profile。"""
    flags = resolve_flags(env={"OTHER_VAR": "1"},  # 无 PA_LOOP_* 键
                          profile={"loop": {"session_aware_retry": True}})
    assert flags.session_aware_retry is True


# ─── 不可变（feature flag 解析结果不应被调用方意外改写）─────────────────
def test_loop_flags_is_frozen():
    """LoopFlags 是 frozen dataclass（同 TestEvidence/ExtResult 既定模式）。"""
    import dataclasses
    assert dataclasses.is_dataclass(LoopFlags)
    flags = resolve_flags(env={"PA_LOOP_TELEMETRY_EXPORT": "1"})
    try:
        flags.telemetry_export = False  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("LoopFlags 应为 frozen——flag 解析结果不可被调用方改写")


# ════════════════════════════════════════════════════════════════════════
# add-cross-prd-learning-memory task 1.3a / 1.3b：learning memory 双 flag（默认关）
# design 决策#8：两 flag 镜像 journal_shadow / journal_driven_dispatch 模式——shadow 先旁路生成 candidate，
# injection 经 parity + quality + allowlist 三门控才注入 prompt。两 flag 默认 False = baseline 不变。
# 不带 PA_LOOP_ prefix（PA_LEARNING_SHADOW / PA_LEARNING_INJECTION）——有意域切割，learning memory 是独立能力域。
# ════════════════════════════════════════════════════════════════════════
def test_learning_flags_default_false():
    """task 1.3a/1.3b：两 flag 默认 False——第一阶段行为零变化（design 决策#8）。"""
    flags = resolve_flags(env={})
    assert flags.cross_prd_learning_shadow is False
    assert flags.cross_prd_learning_injection is False


def test_learning_flags_env_names_mapped_without_loop_prefix():
    """task 1.3a/1.3b：FLAGS_ENV_MAP 含两 flag，env 名是 PA_LEARNING_SHADOW / PA_LEARNING_INJECTION
    （**不带 PA_LOOP_ prefix，有意域切割**）。"""
    assert FLAGS_ENV_MAP["cross_prd_learning_shadow"] == "PA_LEARNING_SHADOW"
    assert FLAGS_ENV_MAP["cross_prd_learning_injection"] == "PA_LEARNING_INJECTION"
    # 域切割：确认不是 PA_LOOP_* 前缀（防误归类为 loop runtime flag）
    assert "PA_LOOP_LEARNING" not in " ".join(FLAGS_ENV_MAP.values())


def test_learning_shadow_env_truthy_enables():
    """task 1.3a：PA_LEARNING_SHADOW=1/on/true → cross_prd_learning_shadow=True。"""
    for v in ["1", "true", "TRUE", "yes", "on"]:
        assert resolve_flags(env={"PA_LEARNING_SHADOW": v}).cross_prd_learning_shadow is True, f"{v} 应 truthy"


def test_learning_shadow_env_falsy_disables():
    """task 1.3a：PA_LEARNING_SHADOW=0/false/no/off/空/乱串 → False。"""
    for v in ["0", "false", "no", "off", "", "random"]:
        assert resolve_flags(env={"PA_LEARNING_SHADOW": v}).cross_prd_learning_shadow is False, f"{v} 应 falsy"


def test_learning_injection_env_truthy_enables():
    """task 1.3b：PA_LEARNING_INJECTION=1 → cross_prd_learning_injection=True。"""
    assert resolve_flags(env={"PA_LEARNING_INJECTION": "1"}).cross_prd_learning_injection is True


def test_learning_shadow_via_profile_loop():
    """task 1.3a：profile.loop.cross_prd_learning_shadow=True → 开（per-project canary）。"""
    flags = resolve_flags(env={}, profile={"loop": {"cross_prd_learning_shadow": True}})
    assert flags.cross_prd_learning_shadow is True
    assert flags.cross_prd_learning_injection is False


def test_learning_env_overrides_profile():
    """task 1.3a：env 显式设置压过 profile（运维 kill switch 一键关回旧逻辑）。"""
    flags = resolve_flags(env={"PA_LEARNING_SHADOW": "false"},
                          profile={"loop": {"cross_prd_learning_shadow": True}})
    assert flags.cross_prd_learning_shadow is False


# ════════════════════════════════════════════════════════════════════════
# single-flight-auto-merge task 1.1：dispatch 串行单飞 + auto-merge 双 flag（默认关）
# design 决策#8 + D9/Migration：两 flag 镜像 journal_shadow/driven、learning_shadow/injection 模式——
# serial_shadow 先走串行消费但 merge/revert 只 log（shadow，不改 main）；auto_merge 经 shadow + parity +
# canary 门控后才真 merge/push/revert（破坏性副作用，改目标仓 main）。两 flag 默认 False = baseline 不变
# （仍并发投递 + 兜底开 PR 待 review）。域切割：PA_SINGLE_FLIGHT_* 前缀（dispatch 串行+auto-merge 是独立能力域）。
# ════════════════════════════════════════════════════════════════════════
def test_single_flight_flags_default_false():
    """task 1.1：两 flag 默认 False——并发投递 + 兜底开 PR 旧行为零变化（design 决策#8）。"""
    flags = resolve_flags(env={})
    assert flags.single_flight_serial_shadow is False
    assert flags.single_flight_auto_merge is False


def test_single_flight_flags_env_names_mapped():
    """task 1.1：FLAGS_ENV_MAP 含两 flag，env 名 PA_SINGLE_FLIGHT_SERIAL_SHADOW / PA_SINGLE_FLIGHT_AUTO_MERGE
    （域切割，非 PA_LOOP_ 前缀）。"""
    assert FLAGS_ENV_MAP["single_flight_serial_shadow"] == "PA_SINGLE_FLIGHT_SERIAL_SHADOW"
    assert FLAGS_ENV_MAP["single_flight_auto_merge"] == "PA_SINGLE_FLIGHT_AUTO_MERGE"


def test_single_flight_serial_shadow_env_truthy():
    """task 1.1：PA_SINGLE_FLIGHT_SERIAL_SHADOW=1/on/true → serial_shadow=True（shadow 串行消费）。"""
    for v in ["1", "true", "TRUE", "yes", "on"]:
        assert resolve_flags(env={"PA_SINGLE_FLIGHT_SERIAL_SHADOW": v}).single_flight_serial_shadow is True, f"{v} 应 truthy"


def test_single_flight_auto_merge_env_truthy():
    """task 1.1：PA_SINGLE_FLIGHT_AUTO_MERGE=1 → auto_merge=True（真实 merge/revert 闭环）。"""
    assert resolve_flags(env={"PA_SINGLE_FLIGHT_AUTO_MERGE": "1"}).single_flight_auto_merge is True


def test_single_flight_serial_shadow_via_profile():
    """task 1.1：profile.loop.single_flight_serial_shadow=True → 开（per-project canary）。"""
    flags = resolve_flags(env={}, profile={"loop": {"single_flight_serial_shadow": True}})
    assert flags.single_flight_serial_shadow is True
    assert flags.single_flight_auto_merge is False


def test_single_flight_env_overrides_profile():
    """task 1.1：env 显式设置压过 profile（运维 kill switch 一键关回旧逻辑）。"""
    flags = resolve_flags(env={"PA_SINGLE_FLIGHT_AUTO_MERGE": "false"},
                          profile={"loop": {"single_flight_auto_merge": True}})
    assert flags.single_flight_auto_merge is False


# ════════════════════════════════════════════════════════════════════════
# langgraph-workflow-upgrade task 5.1：graph 编排器双 flag（默认关）
# design 决策#8 + D7：两 flag 镜像 single_flight / learning 模式——pa_graph_shadow 先旁路跑 graph 主图
# 双源 shadow parity（不改 cron 真路径，仍走 run_daily）；pa_graph_orchestrator 经 shadow + parity +
# canary 门控后才真把 cron 分流到 graph_pa.py（run_cron.sh 分流点）。两 flag 默认 False = baseline 不变
# （cron 仍走 run_daily.py，D7 flag off = run_daily 完整保留）。域切割：PA_GRAPH_* 前缀（编排器主图切换
# 是独立能力域，非 loop runtime 6 大渐进启用面）。
# ════════════════════════════════════════════════════════════════════════
def test_graph_flags_default_false():
    """task 5.1：两 flag 默认 False——cron 仍走 run_daily.py，行为零变化（design 决策#8 / D7）。"""
    flags = resolve_flags(env={})
    assert flags.pa_graph_shadow is False
    assert flags.pa_graph_orchestrator is False


def test_graph_flags_env_names_mapped():
    """task 5.1：FLAGS_ENV_MAP 含两 flag，env 名 PA_GRAPH_SHADOW / PA_GRAPH_ORCHESTRATOR
    （域切割，非 PA_LOOP_ 前缀——编排器主图切换是独立能力域）。"""
    assert FLAGS_ENV_MAP["pa_graph_shadow"] == "PA_GRAPH_SHADOW"
    assert FLAGS_ENV_MAP["pa_graph_orchestrator"] == "PA_GRAPH_ORCHESTRATOR"
    # 域切割：确认是 PA_GRAPH_* 前缀（防误归类为 loop runtime flag）
    assert "PA_GRAPH_SHADOW" in FLAGS_ENV_MAP.values()
    assert "PA_LOOP_GRAPH" not in " ".join(FLAGS_ENV_MAP.values())


def test_graph_shadow_env_truthy():
    """task 5.1：PA_GRAPH_SHADOW=1/on/true → pa_graph_shadow=True（旁路 shadow parity）。"""
    for v in ["1", "true", "TRUE", "yes", "on"]:
        assert resolve_flags(env={"PA_GRAPH_SHADOW": v}).pa_graph_shadow is True, f"{v} 应 truthy"


def test_graph_orchestrator_env_truthy():
    """task 5.1：PA_GRAPH_ORCHESTRATOR=1 → pa_graph_orchestrator=True（cron 分流 graph_pa.py）。"""
    assert resolve_flags(env={"PA_GRAPH_ORCHESTRATOR": "1"}).pa_graph_orchestrator is True


def test_graph_shadow_via_profile():
    """task 5.1：profile.loop.pa_graph_shadow=True → 开（per-project canary）。"""
    flags = resolve_flags(env={}, profile={"loop": {"pa_graph_shadow": True}})
    assert flags.pa_graph_shadow is True
    assert flags.pa_graph_orchestrator is False


def test_graph_env_overrides_profile():
    """task 5.1：env 显式设置压过 profile（运维 kill switch 一键关回 legacy run_daily）。"""
    flags = resolve_flags(env={"PA_GRAPH_ORCHESTRATOR": "false"},
                          profile={"loop": {"pa_graph_orchestrator": True}})
    assert flags.pa_graph_orchestrator is False
