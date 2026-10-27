#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_verify_stream_anchors.py — agent-verify-injection 2.2/2.3：三机械锚 + verify-report（TDD 先红）。

注意本模块（``verify_stream``：stream 解析 + 三机械锚 + report）与既有 ``verify_anchors``
（harden-pa-verify-determinism 的**行级** failing anchor 解析）同词不同物——机械锚 = transcript
退出码 / git 工作区净 / 注入件 digest 三锚（design 锚①②③）。

stream 解析契约锚定 **真 fixture**（fixtures/verify_stream_{green,red}.slim.jsonl——2.0 冒烟
产物）：tool_use(Bash)↔tool_result 按 id 配对；exit code 无结构化字段——``is_error=false`` ⇔ 0，
``is_error=true`` ⇔ content 首行 ``Exit code N``（抽不到 → 保守 1）。
锚①同一性谓词（D5.1）：末端段 ≡ declared（链式变体 ``cd wt && npm ci && npm test`` 匹配
``npm test`` 不错杀）；管道/重定向段不匹配；非全等变体 → unresolved。
锚②workspace：真 tmp git repo——tracked 变更拒、非 ignored untracked ⊆ 注入集过。
锚③：注入件逐文件 sha256（改/缺 → violation）。
verdict 三态 green|red|invalid（unresolved / 锚 violation → invalid；超时映射 invalid 由
编排层做——executor raise VerifySessionTimeout 已锁）+ verify-report 完备性（含环境奇偶节 T3）。
跑：python3 -m pytest scripts/test_verify_stream_anchors.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import verify_stream as VS   # noqa: E402

FIX = Path(__file__).parent / "fixtures"


# ─── stream 解析（真 fixture 锚定） ────────────────────────────────────
def test_parse_stream_green_fixture():
    execs, meta = VS.parse_stream((FIX / "verify_stream_green.slim.jsonl").read_text(encoding="utf-8"))
    assert len(execs) == 1
    ex = execs[0]
    assert ex.command == "echo pa-verify-smoke"
    assert ex.exit_code == 0 and ex.is_error is False
    assert "pa-verify-smoke" in ex.output
    assert meta.get("session_id") and "num_turns" in meta   # result 会话元抽取


def test_parse_stream_red_fixture_exit_code():
    execs, _ = VS.parse_stream((FIX / "verify_stream_red.slim.jsonl").read_text(encoding="utf-8"))
    failing = [e for e in execs if e.is_error]
    assert failing, "fixture 必含非零退出样本"
    f = failing[0]
    assert f.exit_code == 7                       # 「Exit code 7」首行抽取
    assert "out-line" in f.output                 # 首行剥离后输出保留（行锚解析输入不断供）


def test_exit_code_fallback_conservative():
    """is_error=true 但首行无 Exit code N → 保守 1（非零）。"""
    assert VS._exit_code("weird failure text", True) == 1
    assert VS._exit_code("Exit code 42\nrest", True) == 42
    assert VS._exit_code("plain stdout", False) == 0


# ─── 锚①：同一性谓词（D5.1） ───────────────────────────────────────────
def _ex(cmd, code=0):
    return VS.BashExecution(command=cmd, tool_use_id="t1", is_error=code != 0,
                            exit_code=code, output="")


def test_match_chained_variant_not_killed():
    """链式变体末段 ≡ declared → 采信（D5.1：信任谓词上限不泄漏进匹配面）。"""
    execs = [_ex("cd /wt && npm ci && npm test")]
    m = VS.match_declared(execs, ["npm test"])
    assert m["npm test"] is not None and m["npm test"].exit_code == 0


def test_match_pipe_segment_rejected():
    """末段含管道 → 退出码不可信 → 不匹配（unresolved）。"""
    m = VS.match_declared([_ex("npm test | tail")], ["npm test"])
    assert m["npm test"] is None


def test_match_variant_flag_unresolved():
    """非全等变体（--silent）→ unresolved（保守：变体不可判读）。"""
    m = VS.match_declared([_ex("npm test --silent")], ["npm test"])
    assert m["npm test"] is None


def test_match_none_when_not_run():
    """install 失败没跑到 test cmd → unresolved（invalid 信号）。"""
    m = VS.match_declared([_ex("npm ci")], ["npm test"])
    assert m["npm test"] is None


# ─── 锚②：workspace 干净（真 tmp git repo） ───────────────────────────
def _git_repo(tmp_path):
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    for c in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
              ["git", "config", "user.name", "t"], ["git", "add", "-A"],
              ["git", "commit", "-qm", "init"]):
        subprocess.run(c, cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def test_workspace_clean_ok_with_injected_untracked(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "test").mkdir()
    (repo / "test" / "a.test.js").write_text("// criterion: x:ac1", encoding="utf-8")
    ok, detail = VS.check_workspace_clean(repo, {"test/a.test.js"})
    assert ok, detail


def test_workspace_dirty_tracked_change_rejected(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "README.md").write_text("changed", encoding="utf-8")
    ok, _ = VS.check_workspace_clean(repo, set())
    assert not ok


def test_workspace_stray_untracked_rejected(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "hack.py").write_text("x", encoding="utf-8")
    ok, _ = VS.check_workspace_clean(repo, {"test/a.test.js"})
    assert not ok


# ─── 锚③：注入件 digest 复核 ─────────────────────────────────────────
def test_digest_ok(tmp_path):
    import acceptance_tests as AT
    content = "test('a', () => {});"
    dg = AT.store_suite(tmp_path, {"test/a.test.js": content},
                        prd_criteria_ids=["x:ac1"], suite_criteria_ids=["x:ac1"],
                        harness="jest")["files"][0]["digest"]
    (tmp_path / "test").mkdir()
    (tmp_path / "test" / "a.test.js").write_text(content, encoding="utf-8")   # 注入件已拷入 worktree
    assert VS.check_injected_digests(tmp_path, {"test/a.test.js": dg}) == []
    (tmp_path / "test" / "a.test.js").write_text("tampered", encoding="utf-8")
    assert any("digest-mismatch" in v for v in VS.check_injected_digests(tmp_path, {"test/a.test.js": dg}))
    (tmp_path / "test" / "a.test.js").unlink()
    assert any("missing" in v for v in VS.check_injected_digests(tmp_path, {"test/a.test.js": dg}))


# ─── 环境奇偶（T3：机械提取，记录不阻断） ─────────────────────────────
def test_observed_env_exports():
    execs = [_ex("NODE_ENV=test npm test"), _ex("export CI=1 && echo ok"), _ex("npm run build")]
    obs = VS.observed_env_exports(execs)
    assert obs.get("NODE_ENV") == ["test"]
    assert obs.get("CI") == ["1"]


# ─── verdict 三态 ─────────────────────────────────────────────────────
def test_verdict_green_red_invalid():
    good = {"npm test": _ex("npm test", 0)}
    bad = {"npm test": _ex("npm test", 1)}
    unresolved = {"npm test": None}
    assert VS.derive_verdict(good, True, []) == "green"
    assert VS.derive_verdict(bad, True, []) == "red"
    assert VS.derive_verdict(unresolved, True, []) == "invalid"
    assert VS.derive_verdict(good, False, []) == "invalid"          # 锚② violation
    assert VS.derive_verdict(good, True, ["digest-mismatch:test/a.test.js"]) == "invalid"  # 锚③


# ─── verify-report 完备性（⑤） ────────────────────────────────────────
def _report(matched=None, ws_ok=True, dg_viol=None):
    return VS.build_verify_report(
        run_id="run-1", project="demo", prd_id="prd-1", iteration_id="it-1", harness="jest",
        gate_cmd="npm test", gate_source="profile",
        matched=matched if matched is not None else {"npm test": _ex("npm test", 0)},
        anchor_workspace_ok=ws_ok, anchor_digest_violations=dg_viol or [],
        stream_path="state/runs/x.verify-stream.jsonl",
        session_meta={"session_id": "s1", "num_turns": 3, "duration_api_ms": 100, "total_cost_usd": 0.1},
        env_spec="NODE_ENV=test", env_observed={"NODE_ENV": ["test"]})


def test_report_fields_complete():
    r = _report()
    for section in ("run", "commands", "anchors", "verdict", "evidence", "session", "env_parity"):
        assert section in r, section
    assert r["run"]["run_id"] == "run-1" and r["run"]["harness"] == "jest"
    assert r["commands"]["source"] == "profile" and r["commands"]["gate_cmd"] == "npm test"
    assert r["commands"]["results"][0]["exit_code"] == 0
    assert r["anchors"]["workspace_clean"]["ok"] is True
    assert r["verdict"] == "green"
    assert r["evidence"]["stream_path"].endswith(".jsonl")
    assert r["env_parity"]["declared"] == "NODE_ENV=test"     # T3 环境奇偶节
    assert r["env_parity"]["observed_exports"] == {"NODE_ENV": ["test"]}


def test_report_three_states_shape():
    red = _report(matched={"npm test": _ex("npm test", 2)})
    assert red["verdict"] == "red"
    inv = _report(matched={"npm test": None})
    assert inv["verdict"] == "invalid"
    assert inv["commands"]["results"][0]["unresolved"] is True
    assert json.dumps(inv, ensure_ascii=False)   # 可序列化


def test_report_records_anchor_detail():
    r = _report(ws_ok=False, dg_viol=["digest-mismatch:test/a.test.js"])
    assert r["verdict"] == "invalid"
    assert r["anchors"]["injected_digests"]["violations"] == ["digest-mismatch:test/a.test.js"]
