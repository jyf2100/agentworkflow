#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_journal.py — journal 单写持久化测试（任务 3.7，D2 单写真源）。

commit_node（4 类 node 工厂的单收口点）内 append_event(fsync) 先于 return state。event_type=
node_committed 非 status 事件（不在 _EVENT_STATUS_MAP），reducer 视作纯观测：收录 event_id 进
dedup 集但不推进 status → 与 _sj_terminal 写的 status 终态事件语义正交（节点级观测 vs 状态机级迁移）。

覆盖：
① commit_node 单元：写 journal / 无 _journal_path→no-op / 写入异常吞 / event_id 确定性+round 区分
② 4 类 node kind（persona/mechanical/gateway/devloop）经 commit_node 都写
③ reducer 正交：node_committed 收录 event_id 但不改 status
④ dispatch 子图端到端：baseline pass → journal 含多节点 node_committed 事件
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_pa_nodes as GN
import graph_pa_contracts as C


# ── 合法 NodeInput/NodeOutput 构造（过 validate_node_input/output）────────
def _ni(tmp_path=None, **extra):
    ni = {"run_id": "run_x", "stage": "radar", "config": {}, "_project": "proj"}
    if tmp_path is not None:
        ni["_journal_path"] = str(tmp_path / "j.jsonl")
    ni.update(extra)
    return ni


def _out(node="radar", **extra):
    o = {"status": C.STATUS_OK, "obs": {"node": node, "cost": 0.1},
         "idempotency_key": "run_x:radar:proj"}
    o.update(extra)
    return o


def _read_journal(path):
    """读 journal jsonl → list[dict]（每行一事件）。"""
    return [json.loads(l) for l in path.read_text(encoding="utf-8").strip().splitlines()]


# ── A. commit_node 单元 ─────────────────────────────────────────────────
def test_commit_node_writes_journal_event(tmp_path):
    """ni 含 _journal_path + 合法 out → commit_node 写 1 条 node_committed 事件，字段全。"""
    jpath = tmp_path / "j.jsonl"
    ni = _ni(_journal_path=str(jpath))
    out = _out(node="radar")
    GN.commit_node(GN.KIND_MECHANICAL, ni, out)
    events = _read_journal(jpath)
    assert len(events) == 1
    e = events[0]
    assert e["event_type"] == "node_committed"
    assert e["run_id"] == "run_x"
    assert e["event_id"].startswith("nc-radar-")          # nc-<node>-<_ik>-r<round>
    assert e["payload"]["kind"] == "mechanical"
    assert e["payload"]["node"] == "radar"
    assert e["schema_version"] == 1


def test_commit_node_no_path_is_noop(tmp_path):
    """ni 不含 _journal_path → commit_node no-op（无文件、无异常）。"""
    ni = _ni()                                            # 无 _journal_path
    out = _out()
    result = GN.commit_node(GN.KIND_MECHANICAL, ni, out)  # 不 raise
    assert result is out                                  # 正常返回 out
    assert not (tmp_path / "j.jsonl").exists()           # 无文件创建


def test_commit_node_journal_error_swallowed(monkeypatch, capsys, tmp_path):
    """journal.append_event 抛 OSError → commit_node 仍正常 return out（观测层容错，不拖垮 node），
    且经 stderr 暴露写失败（守 CLAUDE.md「无静默失败」，r2 review HIGH——D2 journal 是真相源非可弃影子）。"""
    import journal
    jpath = tmp_path / "j.jsonl"
    ni = _ni(_journal_path=str(jpath))
    out = _out()

    def _raise_disk_full(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(journal, "append_event", _raise_disk_full)
    result = GN.commit_node(GN.KIND_MECHANICAL, ni, out)  # 不 raise
    assert result is out                                  # 异常吞，正常返回
    err = capsys.readouterr().err
    assert "journal append failed" in err                 # stderr 可观测信号（不静默）
    assert "disk full" in err


def test_commit_node_event_id_deterministic_with_round(tmp_path):
    """同 ni 同 round 两次 commit → 同 event_id（重放 dedup）；round2 ≠ round1。"""
    jpath = tmp_path / "j.jsonl"
    ni_r1 = _ni(_journal_path=str(jpath), verify_round=1)
    ni_r2 = _ni(_journal_path=str(jpath), verify_round=2)
    out = _out(node="verify")
    GN.commit_node(GN.KIND_PERSONA, ni_r1, out)
    GN.commit_node(GN.KIND_PERSONA, ni_r2, out)
    events = _read_journal(jpath)
    assert len(events) == 2
    assert events[0]["event_id"].endswith("-r1")
    assert events[1]["event_id"].endswith("-r2")
    assert events[0]["event_id"] != events[1]["event_id"]  # round 区分
    # 确定性：同 round 再写一次 → 同 event_id（dedup 锚点）
    GN.commit_node(GN.KIND_PERSONA, ni_r1, out)
    events2 = _read_journal(jpath)
    ids = [e["event_id"] for e in events2]
    assert ids[0] == ids[-1]                              # 同 round 同 event_id


# ── B. 4 类 node kind 经 commit_node 都写 ────────────────────────────────
def test_all_node_kinds_write_journal(tmp_path):
    """persona/mechanical/gateway/devloop 各 commit → journal 4 条，payload.kind 覆盖 4 类。

    persona 可带 verdict（唯一可写）；其他三类无 verdict（commit_node verdict 边界守）。
    """
    jpath = tmp_path / "j.jsonl"
    ni = _ni(_journal_path=str(jpath))
    verdict_out = _out(node="critic", verdict={"value": "revise", "reason": "缺验收标准"})
    GN.commit_node(GN.KIND_PERSONA, ni, verdict_out)
    GN.commit_node(GN.KIND_MECHANICAL, ni, _out(node="inject"))
    GN.commit_node(GN.KIND_GATEWAY, ni, _out(node="admission"))
    GN.commit_node(GN.KIND_DEVLOOP, ni, _out(node="dev"))
    events = _read_journal(jpath)
    assert len(events) == 4
    kinds = {e["payload"]["kind"] for e in events}
    assert kinds == {"persona", "mechanical", "gateway", "devloop"}
    nodes = {e["payload"]["node"] for e in events}
    assert nodes == {"critic", "inject", "admission", "dev"}
    # verdict 只在 persona 事件出现
    assert events[0]["payload"]["verdict"] == {"value": "revise", "reason": "缺验收标准"}
    assert all(e["payload"]["verdict"] is None for e in events[1:])


# ── C. reducer 正交：node_committed 收录 event_id 但不改 status ──────────
def _as_journal_event(d: dict):
    """dict（json round-trip）→ JournalEvent（reduce 输入）。"""
    from loop_state import JournalEvent
    return JournalEvent(**d)


def test_node_committed_is_observation_only(tmp_path):
    """node_committed 非 status 事件：reducer 收录 event_id 进 dedup 集但不推进 status。

    经 _journal_append 产真实 payload 结构事件（r2 review MED——原手动合成绕过生产路径，若 reducer
    未来读 payload["kind"]/["node"] 测试仍会过）。与 status 事件对比 → 证明与 _sj_terminal 正交。
    """
    from loop_state import JournalEvent, reduce, initial_state, IterationStatus
    jpath = tmp_path / "j.jsonl"
    ni = _ni(_journal_path=str(jpath))                    # 真实 ni（含 _journal_path）
    out = _out(node="radar")
    GN._journal_append(GN.KIND_MECHANICAL, ni, out)       # 生产路径产真实 payload 事件
    nc_real = _read_journal(jpath)[0]
    nc = _as_journal_event(nc_real)

    init = initial_state("run_x", "p", "i", "b")
    state = reduce([nc], init)
    assert state.status == IterationStatus.PLANNED        # 不改 status（观测事件）
    assert nc_real["event_id"] in state.applied_event_ids  # 但 event_id 进 dedup 集
    # 对比：status 事件推进 status，nc 仍收录
    running = JournalEvent(schema_version=1, event_id="r1", timestamp="t",
                           iteration_id="i", run_id="run_x", prd_id="p",
                           event_type="running", payload={})
    state2 = reduce([nc, running], init)
    assert state2.status == IterationStatus.RUNNING       # running 推进 status
    assert nc_real["event_id"] in state2.applied_event_ids  # nc 仍收录（共存不冲突）


# ── D. dispatch 子图端到端 ───────────────────────────────────────────────
def test_dispatch_subgraph_writes_journal_per_node(monkeypatch, tmp_path):
    """dispatch 子图 baseline pass → journal 含多条 node_committed（commit_node 单点覆盖全 node）。

    复用 test_graph_dispatch_e2e 的 baseline pass mock 链；注入 _journal_path → 各节点 commit_node 写。
    _sj_terminal 被 mock（不写 status event）→ journal 全是 node_committed（节点级观测）。
    P1-7 修复后：工厂 name 参数注入 ni.node_id → payload.node = 节点名（admission/worktree/...），
    event_id 全唯一（不再同 kind 共享）。断言 event_id 唯一 + 关键节点覆盖（r2 review 加固）。
    """
    import types
    import test_graph_dispatch_e2e as E2E
    import graph_pa_dispatch as GD

    E2E._mock_admission_pass(monkeypatch)
    E2E._capture_subprocess(monkeypatch)
    E2E._mock_dev_post_pass(monkeypatch)
    E2E._mock_verify_seq(monkeypatch, ["pass"])
    E2E._mock_publish_pr_open(monkeypatch)
    captured = {}
    E2E._mock_sj(monkeypatch, captured)

    jpath = tmp_path / "j.jsonl"
    s = dict(E2E._BASE)
    s["_sj"] = types.SimpleNamespace(path=str(jpath))
    s["_journal_path"] = str(jpath)                       # 注入 → commit_node 写 journal
    GD.build_dispatch_subgraph().invoke(s)

    events = _read_journal(jpath)
    assert len(events) >= 5                               # append-only，多节点各写一条
    assert all(e["event_type"] == "node_committed" for e in events)   # _sj_terminal mock
    kinds = {e["payload"]["kind"] for e in events}
    assert "mechanical" in kinds                          # dispatch 多数节点 mechanical
    # P1-7 修复后：每节点 node_id 唯一 → event_id 唯一（崩溃恢复可定位执行点）
    ids = [e["event_id"] for e in events]
    assert len(set(ids)) == len(ids), f"event_id 撞: {ids}"
    # 关键节点必须出现（捕获「丢失节点」回归，r2 review MED——原断言过松）
    nodes_seen = {e["payload"]["node"] for e in events}
    assert {"admission", "worktree", "terminal_emit"} <= nodes_seen, f"缺节点: {nodes_seen}"
