"""test_coordinator.py — task 2.1 production runtime coordinator 回归测试。

design 决策#1：把 journal/artifacts/IDs/retry/hooks/sandbox/telemetry/reconciliation 收敛到一个
coordinator 边界，``dispatch_one`` 与 ``dev-agent`` 共用它——**一次解析所有 loop flag**，集中 own
运行时设施，不再散建（spec durable-runtime-integration「Production runtime coordinator」两个 scenario）。

本文件覆盖 task 2.1 的 coordinator **骨架**契约（flags 一次解析 + 稳定 IDs + own journal/artifact
+ lifecycle emit + iteration 衍生 + baseline 保留）；hooks/sandbox/telemetry adapter 的生产 wiring
由 task 2.3 / Section 5 / Section 6 在 coordinator 上挂载（从 ``coord.flags`` 读 flag，不再各自 resolve）。

纯逻辑层（DI ``tmp_path`` + 固定 ``stamp_fn``），零 SDK/零系统时间；AAA；跑：
``python3 -m pytest scripts/test_coordinator.py -q``
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import coordinator as CO  # noqa: E402
import ids as loop_ids  # noqa: E402
import journal as J  # noqa: E402
from feature_flags import LoopFlags  # noqa: E402
from loop_runtime import ShadowJournal  # noqa: E402


_STAMP = "20260722"


def _build(tmp_path, *, profile=None, env=None):
    """造一个 coordinator（固定 stamp_fn，确定性）。"""
    return CO.build_coordinator(
        stamp=_STAMP, prd_path="prd/proj/x.md", proj="proj", slug="x",
        state_dir=tmp_path, profile=profile, env=env,
        stamp_fn=lambda: "2026-07-22T00:00:00Z",
    )


# ─── 一次解析 flag：env > profile > 默认；产出冻结快照 ─────────────────────────
def test_build_resolves_flags_once_as_immutable_snapshot(tmp_path):
    """design 决策#1「resolves flags once」：coordinator 是唯一 resolve 点。env 压 profile，
    产出 ``LoopFlags`` 冻结快照——后续 adapter 从 ``coord.flags`` 读，不再各自 ``resolve_flags``。"""
    # Arrange — profile 开 journal_shadow + lifecycle_hooks；env kill lifecycle_hooks（env 优先）
    prof = {"loop": {"journal_shadow": True, "lifecycle_hooks": True}}
    env = {"PA_LOOP_LIFECYCLE_HOOKS": "false"}

    # Act
    coord = _build(tmp_path, profile=prof, env=env)

    # Assert — env 压 profile；快照是 LoopFlags 冻结实例
    assert coord.flags.journal_shadow is True
    assert coord.flags.lifecycle_hooks is False     # env kill 压过 profile
    assert isinstance(coord.flags, LoopFlags)


# ─── 稳定确定性 ID（同输入→同 ID，来自 loop_ids 单一源头，前缀可辨）─────────────
def test_build_creates_stable_deterministic_ids(tmp_path):
    """spec「Enabled runtime uses coordinator ... creates stable run/PRD/iteration IDs」。
    ID 必须确定性（崩溃重放产同 id，reducer dedup 依据）且来自 ``loop_ids`` 单一源头。"""
    # Act
    c1 = _build(tmp_path)
    c2 = _build(tmp_path)

    # Assert — 确定性（同输入同 id）+ 等于 loop_ids + 前缀可辨
    assert c1.run_id == c2.run_id == loop_ids.run_id(_STAMP)
    assert c1.prd_id == c2.prd_id == loop_ids.prd_id("prd/proj/x.md")
    assert c1.iteration_id == loop_ids.iteration_id(c1.run_id, c1.prd_id, 0)
    assert c1.run_id.startswith("run_")
    assert c1.prd_id.startswith("prd_")
    assert c1.iteration_id.startswith("iter_")


# ─── own journal：ShadowJournal 实例，enabled 跟 journal_shadow flag，run_id 绑定 ─
def test_build_owns_journal_enabled_matches_shadow_flag(tmp_path):
    """coordinator own journal（``ShadowJournal``）；``enabled`` 跟 ``journal_shadow`` flag，
    ``run_id`` 绑定——替代 dispatch_one 散建的 ``_sj``。"""
    # Arrange — shadow 开 / 关
    c_on = _build(tmp_path, profile={"loop": {"journal_shadow": True}})
    c_off = _build(tmp_path)

    # Assert
    assert isinstance(c_on.journal, ShadowJournal)
    assert c_on.journal.enabled is True
    assert c_on.journal.run_id == c_on.run_id
    assert c_off.journal.enabled is False


# ─── enabled coordinator emit lifecycle 事件（在首个副作用前可观测）──────────────
def test_enabled_coordinator_emit_lifecycle_to_journal(tmp_path):
    """spec「emits lifecycle events before the first external side effect」——enabled 时
    ``coord.emit`` 委托 journal 落盘 lifecycle 事件（planned/running/...），返回 event_id。"""
    # Arrange
    coord = _build(tmp_path, profile={"loop": {"journal_shadow": True}})

    # Act
    eid = coord.emit("planned", payload={"base": "main"})
    coord.emit("running", payload={"round": 1})

    # Assert — 两条 lifecycle 事件落盘，首条 event_id 非空
    events = J.read_events(coord.journal.path)
    assert [e.event_type for e in events] == ["planned", "running"]
    assert eid is not None


# ─── disabled（baseline）：is_baseline、emit no-op、journal 文件不建（无 partial durable）
def test_disabled_coordinator_preserves_baseline(tmp_path):
    """spec「Disabled runtime preserves baseline ... does not silently invoke partial durable
    features」：flags 全关 → ``is_baseline`` True，emit no-op（journal 文件不建），dispatch
    first-phase 决策零变化。"""
    # Arrange — flags 全关（默认）
    coord = _build(tmp_path)

    # Act
    eid = coord.emit("running", payload={"round": 1})

    # Assert — baseline：no-op，不悄悄写 partial durable
    assert coord.is_baseline is True
    assert eid is None
    assert not Path(coord.journal.path).exists()


# ─── next_iteration：distinct + 引用 parent run/prd（确定性）────────────────────
def test_next_iteration_distinct_and_references_parent(tmp_path):
    """spec「Iteration identity ... distinct deterministic iteration ID while preserving a parent
    run/PRD identity」。``next_iteration(seq)`` 衍生 distinct id（task 3.3 revise/resume/fork 用），
    parent run/prd 一致，确定性。"""
    # Arrange
    coord = _build(tmp_path)

    # Act
    iter1 = coord.next_iteration(1)
    iter2 = coord.next_iteration(2)

    # Assert — distinct + 等于 loop_ids 单一源头（parent run/prd + seq）
    assert iter1 != iter2 != coord.iteration_id
    assert iter1 == loop_ids.iteration_id(coord.run_id, coord.prd_id, 1)
    assert iter2 == loop_ids.iteration_id(coord.run_id, coord.prd_id, 2)


# ─── artifact_root 在 state_dir/artifacts/<run_id>（内容寻址工件存储根）──────────
def test_artifact_root_keyed_by_run_under_state_dir(tmp_path):
    """coordinator own artifact store 根：``state_dir/artifacts/<run_id>``——per-run 隔离的
    内容寻址工件存储（feedback/snapshot/transcript 落盘处，task 3.2/4.3 用）。"""
    # Act
    coord = _build(tmp_path)

    # Assert
    assert coord.artifact_root == str(tmp_path / "artifacts" / coord.run_id)


# ════════════════════════════════════════════════════════════════════════════
# task 2.5：preflight 校验 loop flag 组合一致性（design 决策#1 防 impossible partial 组合）
# ════════════════════════════════════════════════════════════════════════════
def test_preflight_accepts_baseline_and_self_consistent_combos():
    """baseline（全关）+ 自洽组合（shadow 单开 / shadow+hooks / shadow+driven+hooks）→ ok。

    design 决策#1：散建 flag 允许 impossible 组合；preflight 一次校验所有依赖链。自洽组合放行。"""
    # Arrange — 自洽组合（依赖满足）
    ok_cases = [
        LoopFlags(),                                                          # baseline 全关
        LoopFlags(journal_shadow=True),                                       # shadow 单开
        LoopFlags(journal_shadow=True, lifecycle_hooks=True),                # shadow+hooks（hooks 依赖满足）
        LoopFlags(journal_shadow=True, journal_driven_dispatch=True,
                  lifecycle_hooks=True, session_aware_retry=True),           # shadow 驱动其余（全依赖满足）
    ]
    # Act + Assert
    for flags in ok_cases:
        r = CO.preflight(flags)
        assert r.is_ok, f"应放行自洽组合 {flags}"
        assert r.blocked is None


@pytest.mark.parametrize("bad_flags,violated_substring", [
    (LoopFlags(journal_driven_dispatch=True), "journal_driven_dispatch requires journal_shadow"),
    (LoopFlags(session_aware_retry=True), "session_aware_retry requires journal_shadow"),
    (LoopFlags(lifecycle_hooks=True), "lifecycle_hooks requires journal_shadow"),
])
def test_preflight_rejects_flag_without_journal_dependency(bad_flags, violated_substring):
    """design 决策#1：driven/retry/hooks 开但 journal_shadow 关 = impossible partial 组合 → blocked。

    依赖链（design 决策#2 cutover + #8 渐进）：
        journal_driven_dispatch ⇒ journal_shadow（driven 必须先 shadow）
        session_aware_retry     ⇒ journal_shadow（retry 需 journal 持久化 session）
        lifecycle_hooks         ⇒ journal_shadow（hooks 需 journal 落盘事件）
    """
    # Act
    r = CO.preflight(bad_flags)
    # Assert — 结构化 blocked reason，含具体违规描述
    assert not r.is_ok
    assert r.blocked is not None
    assert any(violated_substring in v for v in r.blocked.violations)


def test_preflight_records_all_violations_structured():
    """task 2.5「record a structured blocked reason」：多条依赖同时违 → violations 全列出（不漏报）。"""
    # Arrange — driven + retry + hooks 全开但 shadow 关（3 条依赖链全违）
    flags = LoopFlags(journal_driven_dispatch=True, session_aware_retry=True,
                      lifecycle_hooks=True, journal_shadow=False)
    # Act
    r = CO.preflight(flags)
    # Assert — 3 条违规全记录，reason 含 journal_shadow
    assert not r.is_ok
    assert len(r.blocked.violations) == 3
    assert "journal_shadow" in r.blocked.reason

