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


def test_all_six_env_names_mapped():
    """FLAGS_ENV_MAP 覆盖 6 个 flag，且 env 名稳定（运维/CI 文档化的开关名）。"""
    assert FLAGS_ENV_MAP == {
        "journal_shadow": "PA_LOOP_JOURNAL_SHADOW",
        "journal_driven_dispatch": "PA_LOOP_JOURNAL_DRIVEN_DISPATCH",
        "session_aware_retry": "PA_LOOP_SESSION_AWARE_RETRY",
        "lifecycle_hooks": "PA_LOOP_LIFECYCLE_HOOKS",
        "container_sandbox": "PA_LOOP_CONTAINER_SANDBOX",
        "telemetry_export": "PA_LOOP_TELEMETRY_EXPORT",
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
