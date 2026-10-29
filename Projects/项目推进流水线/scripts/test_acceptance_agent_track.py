#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_acceptance_agent_track.py — agent-verify-injection 2.4/2.5：independent_verify flag 分支 + agent 轨（TDD 先红）。

覆盖面：
- flag 注册三态：``PA_VERIFY_AGENT`` env > profile.loop > 默认 False（off = 机械轨 byte-identical）；
- ``_resolve_gate_cmd`` 优先序三场景（profile 声明胜出 / 无声明用 dev hint / 皆无 → none+note 记来源）；
- ``_acceptance_ref_for``：stored → suite_ref；absent / 无登记 / 损坏 → None（fail-open）；
- flag 分支路由：on → ``_agent_track_verify``；off → 机械轨（不触 agent 面）；
- agent 轨全链（fake executor + 真 tmp git repo + 真 store 套件 + 手工 stream-json）：
  green（三锚净）/ red（declared 非零）/ invalid（unresolved）/ 超时映射 invalid（T4）/
  无考卷无门跳过（verdict=None）/ 套件 load 失败 fail-closed；注入件机械拷入 worktree、
  原始 stream + verify-report + test_log 落 ``state/runs``（④/B2）。

跑：python3 -m pytest scripts/test_acceptance_agent_track.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import acceptance_tests as AT   # noqa: E402
import feature_flags as FF      # noqa: E402
import run_daily                # noqa: E402
import verify_agent as VA       # noqa: E402


# ─── flag 注册（三态） ─────────────────────────────────────────────────
def test_flag_default_off_and_env_on():
    assert FF.resolve_flags(env={}).verify_agent_track is False
    assert FF.resolve_flags(env={"PA_VERIFY_AGENT": "1"}).verify_agent_track is True
    assert FF.resolve_flags(env={"PA_VERIFY_AGENT": "0"}).verify_agent_track is False
    assert FF.resolve_flags(env={}, profile={"loop": {"verify_agent_track": True}}).verify_agent_track is True
    # env kill switch 压过 profile
    assert FF.resolve_flags(env={"PA_VERIFY_AGENT": "false"},
                            profile={"loop": {"verify_agent_track": True}}).verify_agent_track is False


# ─── 门命令优先序（2.4：profile > dev-reported > none） ────────────────
def test_gate_cmd_priority_three_scenarios():
    assert run_daily._resolve_gate_cmd({"verify_test_cmd": "npm test"}, "pytest -q") == ("npm test", "profile")
    assert run_daily._resolve_gate_cmd({}, "pytest -q") == ("pytest -q", "dev-reported")
    assert run_daily._resolve_gate_cmd(None, None) == (None, "none")
    assert run_daily._resolve_gate_cmd({"verify_test_cmd": "  "}, "x") == ("x", "dev-reported")   # 空串=未声明


# ─── 登记文件读取（verify 注入面） ─────────────────────────────────────
def test_acceptance_ref_for_stored_absent_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    reg = tmp_path / "acceptance_st_demo_x.json"
    reg.write_text(json.dumps({"status": "stored", "digest": "d", "path": "p", "size": 1,
                               "files": [{"path": "test/a.test.js", "digest": "fd"}]}), encoding="utf-8")
    got = run_daily._acceptance_ref_for("demo", "x", "st")
    assert got and got["digest"] == "d" and got["files"][0]["path"] == "test/a.test.js"
    reg.write_text(json.dumps({"status": "absent", "reason": "no_harness"}), encoding="utf-8")
    assert run_daily._acceptance_ref_for("demo", "x", "st") is None
    reg.unlink()
    assert run_daily._acceptance_ref_for("demo", "x", "st") is None
    reg.write_text("{broken", encoding="utf-8")
    assert run_daily._acceptance_ref_for("demo", "x", "st") is None   # 损坏 → None（fail-open）


# ─── flag 分支路由（off = 机械轨回归锚） ──────────────────────────────
def _repo_with_branch(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text('{"scripts": {"test": "node --test"}}', encoding="utf-8")
    for c in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
              ["git", "config", "user.name", "t"], ["git", "add", "-A"],
              ["git", "commit", "-qm", "init"], ["git", "checkout", "-qb", "feat/x"]):
        subprocess.run(c, cwd=repo, check=True, capture_output=True)
    return repo


def test_flag_routes_to_agent_track(monkeypatch, tmp_path):
    repo = _repo_with_branch(tmp_path)
    called = {}

    def fake_agent_track(*a, **k):
        called["args"] = a
        return {"verdict": "green", "pass": True}

    monkeypatch.setattr(run_daily, "_agent_track_verify", fake_agent_track)
    monkeypatch.setattr(run_daily, "_run_capture",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("机械轨被触")))
    monkeypatch.setenv("PA_VERIFY_AGENT", "1")
    out = run_daily.independent_verify(str(repo), "feat/x", "st", "slug", tmp_path / "l.json",
                                       prof=None, test_cmd_hint="node --test", suite_ref={"digest": "d"})
    assert out["verdict"] == "green" and called["args"][1] == "feat/x"


def test_flag_off_stays_mechanical(monkeypatch, tmp_path):
    """flag off 回归锚：走机械轨（_agent_track_verify 零接触），行为不变。"""
    repo = _repo_with_branch(tmp_path)
    monkeypatch.delenv("PA_VERIFY_AGENT", raising=False)
    monkeypatch.setattr(run_daily, "_agent_track_verify",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("agent 轨被触")))
    caps = {}
    def fake_run_capture(cmd, cwd, timeout, tag, log_file, env=None):
        caps[tag] = (cmd, cwd, timeout)
        return (0, "ok", "")
    monkeypatch.setattr(run_daily, "_run_capture", fake_run_capture)
    out = run_daily.independent_verify(str(repo), "feat/x", "st", "slug", tmp_path / "l.json",
                                       prof=None, test_cmd_hint=None)
    assert out["pass"] is True and out["test_cmd"] == "node --test"   # Node 轨 scripts.test 重放
    assert "[slug:npm-ci]" in caps and "[slug:npm-test]" in caps


# ─── agent 轨全链（fake executor + 手工 stream-json） ─────────────────
def _ev_use(tid, cmd):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd}}]}}


def _ev_res(tid, content, is_error=False):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": content, "is_error": is_error}]}}


def _ev_meta():
    return {"type": "result", "session_id": "s-1", "num_turns": 2,
            "duration_api_ms": 10, "total_cost_usd": 0.01}


def _raw(events):
    return "\n".join(json.dumps(e) for e in events)


class FakeExecutor:
    """捕获 prompt/cwd、断言注入件已机械拷入（injected_rel 非空时），返回预构造 stream。"""
    stream = ""
    injected_rel: str = ""

    def __init__(self, *a, **k):
        pass

    def run(self, cwd, prompt, *, timeout, **k):
        if self.injected_rel:
            assert (Path(cwd) / self.injected_rel).is_file(), "注入件未机械拷入 worktree"
        FakeExecutor.seen_prompt, FakeExecutor.seen_timeout = prompt, timeout
        return self.stream


@pytest.fixture()
def agent_env(tmp_path, monkeypatch):
    """公共底座：STATE_DIR 指 tmp（store + 登记面）+ fake executor + runs 目录。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    runs = tmp_path / "runs"
    runs.mkdir()
    FakeExecutor.injected_rel = "test/a.test.js"
    monkeypatch.setattr(VA, "ClaudeHeadlessExecutor", FakeExecutor)
    monkeypatch.setattr(run_daily, "VA_MOD", VA)
    monkeypatch.setenv("PA_VERIFY_AGENT", "1")
    return tmp_path, runs


def _store_jest_suite(store_root: Path) -> dict:
    return AT.store_suite(store_root, {"test/a.test.js": "test('ac1', () => {});"},
                          prd_criteria_ids=["x:ac1"], suite_criteria_ids=["x:ac1"], harness="jest")


DEC_JEST = "npx jest test/a.test.js"


def test_agent_track_green_full_chain(tmp_path, agent_env):
    _t, runs = agent_env
    suite = _store_jest_suite(tmp_path / "acceptance_store")
    FakeExecutor.stream = _raw([_ev_use("t1", "npm ci"), _ev_res("t1", "installed"),
                                _ev_use("t2", f"cd wt && {DEC_JEST}"), _ev_res("t2", "1 passing"),
                                _ev_use("t3", "npm test"), _ev_res("t3", "all green"),
                                _ev_meta()])
    repo = _repo_with_branch(tmp_path)
    log_file = runs / "st_slug.log"
    out = run_daily.independent_verify(str(repo), "feat/x", "st", "slug", log_file,
                                       prof={"verify_test_cmd": "npm test"}, test_cmd_hint=None,
                                       suite_ref=suite)
    assert out["verdict"] == "green" and out["pass"] is True
    assert out["gate_source"] == "profile" and out["test_rc"] == 0
    assert DEC_JEST in FakeExecutor.seen_prompt and "npm test" in FakeExecutor.seen_prompt   # declared 同源进指令
    rp = runs / "st_slug.verify-report.json"
    assert rp.is_file() and json.loads(rp.read_text(encoding="utf-8"))["verdict"] == "green"
    assert (runs / "st_slug.verify-stream.jsonl").is_file()                                   # ④ 原始流 durable
    assert out["verify_report"] == str(rp)
    assert out["test_log"] and "1 passing" in Path(out["test_log"]).read_text(encoding="utf-8")


def test_agent_track_red_on_declared_nonzero(tmp_path, agent_env):
    _t, runs = agent_env
    suite = _store_jest_suite(tmp_path / "acceptance_store")
    FakeExecutor.stream = _raw([_ev_use("t1", DEC_JEST), _ev_res("t1", "Exit code 1\nFAIL a.test.js", True),
                                _ev_meta()])
    repo = _repo_with_branch(tmp_path)
    out = run_daily.independent_verify(str(repo), "feat/x", "st", "slug", runs / "st_slug.log",
                                       prof=None, test_cmd_hint=None, suite_ref=suite)
    assert out["verdict"] == "red" and out["pass"] is False and out["test_rc"] == 1


def test_agent_track_unresolved_is_invalid(tmp_path, agent_env):
    """install 失败跑不到 declared → unresolved → invalid（不进 revise 语义的判定基础）。"""
    _t, runs = agent_env
    suite = _store_jest_suite(tmp_path / "acceptance_store")
    FakeExecutor.stream = _raw([_ev_use("t1", "npm ci"), _ev_res("t1", "Exit code 1\nEACCES", True),
                                _ev_meta()])
    repo = _repo_with_branch(tmp_path)
    out = run_daily.independent_verify(str(repo), "feat/x", "st", "slug", runs / "st_slug.log",
                                       prof=None, test_cmd_hint=None, suite_ref=suite)
    assert out["verdict"] == "invalid" and out["pass"] is False and out["test_rc"] is None
    # run 20261029m4 标本：invalid 的 note（→ MM reason_headline）须透出人读根因
    # （report.reason，如「unresolved（declared 未被执行/变体不可判读）」）——
    # 否则收到通知的人只看到分流去向，看不出「为什么 invalid」。
    assert out.get("invalid_reason") == "unresolved"
    assert "unresolved" in out.get("note", "") and "不进 revise 回环" in out["note"]


def test_agent_track_timeout_maps_invalid(tmp_path, agent_env, monkeypatch):
    """会话墙钟超时 → invalid 非红（T4：不可判读不是失败）。"""
    _t, runs = agent_env
    suite = _store_jest_suite(tmp_path / "acceptance_store")

    class TimeoutExecutor(FakeExecutor):
        def run(self, cwd, prompt, *, timeout, **k):
            raise VA.VerifySessionTimeout("墙钟超时")

    monkeypatch.setattr(VA, "ClaudeHeadlessExecutor", TimeoutExecutor)
    repo = _repo_with_branch(tmp_path)
    out = run_daily.independent_verify(str(repo), "feat/x", "st", "slug", runs / "st_slug.log",
                                       prof=None, test_cmd_hint=None, suite_ref=suite)
    assert out["verdict"] == "invalid" and "超时" in (out["note"] or "")


def test_agent_track_no_suite_no_gate_skips(tmp_path, agent_env):
    """无考卷且无全量门 → verdict=None 未判定（与机械轨「项目自治跳过」同构），不起会话。"""
    _t, runs = agent_env
    FakeExecutor.stream = ""   # 若误起会话会返回空 → parse 后全 None → 断言失败
    repo = _repo_with_branch(tmp_path)
    out = run_daily.independent_verify(str(repo), "feat/x", "st", "slug", runs / "st_slug.log",
                                       prof=None, test_cmd_hint=None, suite_ref=None)
    assert out["verdict"] is None and out["pass"] is False and "无可执行面" in out["note"]


def test_agent_track_suite_load_failure_fail_closed(tmp_path, agent_env):
    """套件 load 失败（digest/路径坏）→ fail-closed invalid，绝不无考卷裸跑。"""
    _t, runs = agent_env
    bad_ref = {"digest": "deadbeef", "path": "nope/x", "size": 1, "files": []}
    repo = _repo_with_branch(tmp_path)
    out = run_daily.independent_verify(str(repo), "feat/x", "st", "slug", runs / "st_slug.log",
                                       prof=None, test_cmd_hint=None, suite_ref=bad_ref)
    assert out["verdict"] == "invalid" and "load 失败" in out["note"]


def test_agent_track_session_failure_maps_invalid(tmp_path, agent_env, monkeypatch):
    """会话级失败（claude 非零退出：max-turns 撞墙/API 断）→ invalid 非 dispatch 异常
    （run 92 真机：退出 1 冒泡成「dispatch 异常 status=fail」+ report 静默全绿——T1 活标本）。"""
    _t, runs = agent_env
    suite = _store_jest_suite(tmp_path / "acceptance_store")

    class SessionFailExecutor(FakeExecutor):
        def run(self, cwd, prompt, *, timeout, **k):
            raise VA.VerifySessionFailed("[pa-verify-agent] claude 退出 1: max_turns_reached")

    monkeypatch.setattr(VA, "ClaudeHeadlessExecutor", SessionFailExecutor)
    repo = _repo_with_branch(tmp_path)
    out = run_daily.independent_verify(str(repo), "feat/x", "st", "slug", runs / "st_slug.log",
                                       prof=None, test_cmd_hint=None, suite_ref=suite)
    assert out["verdict"] == "invalid" and out["pass"] is False
    assert out.get("invalid_reason") == "session_failed"
    assert "claude 退出 1" in (out["note"] or "")


def test_agent_track_config_swap_dev_version_wins(tmp_path, agent_env):
    """F2（config 覆盖）：harness config 换版——dev 分支已有同名 config 时 store 原件
    不覆盖（dev 环境对接修正是 run 真实形态），out 记两版 sha 对照、锚③豁免换版件。"""
    _t, runs = agent_env
    store_cfg = "// store original config\nexport default {};\n"
    dev_cfg = "// DEV-adapted config (PORT→CC_WEB_PORT)\nexport default {port: 4194};\n"
    suite = AT.store_suite(tmp_path / "acceptance_store",
                           {"test/a.spec.ts": "test('ac1', () => {});",
                            "playwright.config.ts": store_cfg},
                           prd_criteria_ids=["x:ac1"], suite_criteria_ids=["x:ac1"],
                           harness="playwright")
    repo = _repo_with_branch(tmp_path)
    (repo / "playwright.config.ts").write_text(dev_cfg, encoding="utf-8")   # dev 分支已有修正版
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "dev config adapt"],
                   check=True, capture_output=True)
    FakeExecutor.injected_rel = "test/a.spec.ts"
    FakeExecutor.stream = _raw([
        _ev_use("t1", "cd wt && playwright test test/a.spec.ts"),
        _ev_res("t1", "3 passed"), _ev_meta()])
    out = run_daily.independent_verify(str(repo), "feat/x", "st", "slug", runs / "st_slug.log",
                                       prof=None, test_cmd_hint=None, suite_ref=suite)
    swaps = out.get("config_swaps") or []
    assert swaps and swaps[0]["rel"] == "playwright.config.ts"
    assert swaps[0]["dev_sha"] != swaps[0]["store_sha"]          # 两版对照可观测
    # worktree 已被 finally 移除——由 verdict 间接证不覆盖：若 store 件仍注入，锚③对 dev 版
    # config 必 digest-mismatch → invalid；绿 + 换版 note = dev 版生效且豁免路径命中。
    assert out["verdict"] == "green", out["note"]
    assert "config 换版" in (out["note"] or "")


def test_agent_track_gate_only_when_no_suite(tmp_path, agent_env):
    """无验收套件但有全量门（dev-reported hint）→ 只 declared gate_cmd 一条。"""
    _t, runs = agent_env
    FakeExecutor.stream = _raw([_ev_use("t1", "npm test"), _ev_res("t1", "ok"), _ev_meta()])
    FakeExecutor.injected_rel = ""   # 无注入面
    repo = _repo_with_branch(tmp_path)
    out = run_daily.independent_verify(str(repo), "feat/x", "st", "slug", runs / "st_slug.log",
                                       prof=None, test_cmd_hint="npm test", suite_ref=None)
    assert out["verdict"] == "green" and out["gate_source"] == "dev-reported"
    assert "只跑注入验收测试" not in FakeExecutor.seen_prompt   # 有门 → 门进 prompt


# ─── 2.7：机械轨 verdict 契约化（flag off 派生 + 超时 invalid） ─────────
def test_mechanical_track_verdict_derivation(tmp_path, monkeypatch):
    """flag off 机械轨：绿→green / 红→red / 跳过→None（加键不改行为，byte-identical 语义保留）。"""
    repo = _repo_with_branch(tmp_path)
    monkeypatch.delenv("PA_VERIFY_AGENT", raising=False)

    def _mk(rc):
        def fake(cmd, cwd, timeout, tag, log_file, env=None):
            if "npm-test" in tag:                      # 只有测试步吃 rc
                return (rc, f"rc={rc}", "")
            return (0, "", "")                          # 基建步（worktree/npm-ci）恒绿
        return fake

    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(run_daily, "_run_capture", _mk(0))
    out = run_daily.independent_verify(str(repo), "feat/x", "st", "slug", runs / "l.log", prof=None)
    assert out["verdict"] == "green" and out["pass"] is True
    monkeypatch.setattr(run_daily, "_run_capture", _mk(3))
    out = run_daily.independent_verify(str(repo), "feat/x", "st", "slug", runs / "l2.log", prof=None)
    assert out["verdict"] == "red" and out["pass"] is False and out["test_rc"] == 3


def test_mechanical_track_timeout_maps_invalid(tmp_path, monkeypatch):
    """机械轨墙钟超时（_run_capture RuntimeError）→ invalid 不再上抛中断 dispatch（T4 契约化）。"""
    repo = _repo_with_branch(tmp_path)
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.delenv("PA_VERIFY_AGENT", raising=False)

    def fake(cmd, cwd, timeout, tag, log_file, env=None):
        if "npm-test" in tag:
            raise RuntimeError("Command timed out after 600s")
        return (0, "", "")
    monkeypatch.setattr(run_daily, "_run_capture", fake)
    out = run_daily.independent_verify(str(repo), "feat/x", "st", "slug", runs / "l.log", prof=None)
    assert out["verdict"] == "invalid" and out["pass"] is False
    assert "invalid" in out["note"] and "timed out" in out["note"]


# ─── 2.6：套件缺陷签名 + dispatch invalid 分流 ────────────────────────
def test_suite_defective_signature_patterns(tmp_path):
    log_ok = tmp_path / "ok.out"
    log_ok.write_text("3 passing, 0 failed", encoding="utf-8")
    log_bad = tmp_path / "bad.out"
    log_bad.write_text("ERROR collecting test/a.py\nImportError", encoding="utf-8")
    assert run_daily._suite_defective_signature({"test_log": str(log_bad)}) is True
    assert run_daily._suite_defective_signature({"test_log": str(log_ok)}) is False
    assert run_daily._suite_defective_signature({"test_log": None}) is False
    assert run_daily._suite_defective_signature({}) is False
    assert run_daily._suite_defective_signature({"test_log": str(tmp_path / "nope.out")}) is False


def test_dispatch_invalid_routes_to_triage_not_revise(tmp_path, monkeypatch):
    """2.6/2.7 核心：invalid → rec.status=verify_invalid（triage 槽，不落 pass=False 红/revise 槽）、
    不烧 pa-verify 会话（run_persona 触=失败）、reconcile interrupted 对账收尾。"""
    import test_verify_loop as VL
    VL._setup(tmp_path, monkeypatch)
    VL._admit(monkeypatch)
    repo = VL._repo(tmp_path)
    monkeypatch.setattr(run_daily, "_run_dev_agent",
                        lambda *a, **k: {"branch": "auto/r1", "cost": 0.1, "turns": 5, "test_cmd": "npm test"})
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: VL.found(True))
    _bad_log = tmp_path / "bad.out"   # 收集失败签名 → triage_reason 候选
    _bad_log.write_text("ERROR collecting test/a.test.js", encoding="utf-8")
    monkeypatch.setattr(run_daily, "independent_verify",
                        lambda *a, **k: {"verdict": "invalid", "pass": False, "test_rc": None,
                                         "note": "unresolved", "test_log": str(_bad_log)})
    monkeypatch.setattr(run_daily, "_dump_branch_diff", lambda *a, **k: None)
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("invalid 不应烧 pa-verify 会话")))
    recon_calls, fake_rec = VL._recon_recorder()
    monkeypatch.setattr(run_daily, "reconcile_pr", fake_rec)
    rec = run_daily.dispatch_one(VL._entry(), VL._prof(repo), "20260718", VL._args())
    assert rec["status"] == "verify_invalid"          # triage 槽（非红/revise）
    assert rec.get("triage_reason") == "acceptance_suite_defective_candidate"
    assert recon_calls == [True]                       # interrupted 对账收尾（分支保留）
    assert rec.get("verify_verdict") is None           # 未烧 pa-verify
    # terminal 映射面：invalid → external_blocked（reflection 不落 revise 词汇）
    assert run_daily._DISPATCH_STATUS_TO_ENVELOPE_TERMINAL["verify_invalid"] == "external_blocked"


# ─── 1.9：iteration 考卷快照 + 同 digest 不变式（F4） ─────────────────────
def _snap_ref(digest: str) -> dict:
    return {"digest": digest, "path": f"sha256/{digest[:6]}/x", "size": 9, "status": "stored",
            "criteria_ids": ["ac-1"], "uncovered": [], "harness": "jest",
            "files": [{"path": "test/a.test.js", "digest": digest}], "supersedes": None}


def test_dev_cmd_acceptance_injection_params():
    """_dev_cmd：传 bundle/digest → cmd 含 --acceptance-tests/--acceptance-digest；缺省零参数 baseline。"""
    cmd = run_daily._dev_cmd({"conda_env": ""}, "/p.md", "main", "/s.md",
                             acceptance_bundle="/abs/bundle.json", acceptance_digest="sha256:aa")
    assert "--acceptance-tests" in cmd and cmd[cmd.index("--acceptance-tests") + 1] == "/abs/bundle.json"
    assert "--acceptance-digest" in cmd and cmd[cmd.index("--acceptance-digest") + 1] == "sha256:aa"
    cmd2 = run_daily._dev_cmd({"conda_env": ""}, "/p.md", "main", "/s.md")
    assert "--acceptance-tests" not in cmd2 and "--acceptance-digest" not in cmd2


def test_dispatch_suite_snapshot_fixed_mid_iteration(tmp_path, monkeypatch):
    """F4 快照不变式：iteration 开始读一次 digest A；r1 判 revise 后登记文件被重写为 B（triage 重编写）
    ——r2 verify 仍用 A（mid-iteration 不换考卷，supersedes 只对下一次 dispatch 生效）。"""
    import test_verify_loop as VL
    VL._setup(tmp_path, monkeypatch)
    VL._admit(monkeypatch)
    repo = VL._repo(tmp_path)
    refs = {"cur": _snap_ref("sha256:" + "a" * 64)}
    monkeypatch.setattr(run_daily, "_acceptance_ref_for", lambda *a: refs["cur"])
    branches = iter(["auto/r1", "auto/r1"])
    _dev_dg = refs["cur"]["digest"]   # 钉住首轮值：真实链路 dev digest 来自每轮 CLI 参数 = 快照恒 A
    monkeypatch.setattr(run_daily, "_run_dev_agent",
                        lambda *a, **k: {"branch": next(branches), "cost": 0.1, "turns": 5,
                                         "test_cmd": "npm test", "acceptance_digest": _dev_dg})
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: VL.found(True))
    seen: list[str] = []

    def _iv(*a, **k):
        seen.append((k.get("suite_ref") or {}).get("digest"))
        return {"pass": False, "test_rc": 1, "verdict": "red", "test_log": None, "test_cmd": "npm test"}
    monkeypatch.setattr(run_daily, "independent_verify", _iv)
    monkeypatch.setattr(run_daily, "_dump_branch_diff", lambda *a, **k: None)
    verdicts = iter([{"verdict": "revise", "feedback_section": "fix X"},
                     {"verdict": "pass"}])

    def _rp(*a, **k):
        v = next(verdicts)
        if v["verdict"] == "revise":   # mid-iteration 重编写：r1 判 revise 时登记文件换新 digest
            refs["cur"] = _snap_ref("sha256:" + "b" * 64)
        return v, {"cost": 0.0, "turns": 1}
    monkeypatch.setattr(run_daily, "run_persona", _rp)
    _c, fake_rec = VL._recon_recorder()
    monkeypatch.setattr(run_daily, "reconcile_pr", fake_rec)
    rec = run_daily.dispatch_one(VL._entry(), VL._prof(repo), "20260718", VL._args())
    assert rec["verify_verdict"] == "pass"                       # r2 判绿走完（revise 回环真跑了两轮）
    assert seen == ["sha256:" + "a" * 64] * 2                     # 两轮 verify 同考卷 A（B 没换进来）


def test_dispatch_invariant_violation_invalid(tmp_path, monkeypatch):
    """dev 上报注入 digest ≠ 考卷快照 → invalid 分流：verify 会话不烧、不进 revise、对账收尾。"""
    import test_verify_loop as VL
    VL._setup(tmp_path, monkeypatch)
    VL._admit(monkeypatch)
    repo = VL._repo(tmp_path)
    monkeypatch.setattr(run_daily, "_acceptance_ref_for",
                        lambda *a: _snap_ref("sha256:" + "a" * 64))
    monkeypatch.setattr(run_daily, "_run_dev_agent",
                        lambda *a, **k: {"branch": "auto/r1", "cost": 0.1, "turns": 5,
                                         "test_cmd": "npm test", "acceptance_digest": "sha256:" + "e" * 64})
    monkeypatch.setattr(run_daily, "_has_commits", lambda *a, **k: VL.found(True))
    monkeypatch.setattr(run_daily, "independent_verify",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不变式破坏应在 verify 前分流")))
    monkeypatch.setattr(run_daily, "_dump_branch_diff", lambda *a, **k: None)
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("invalid 不进 revise")))
    _c, fake_rec = VL._recon_recorder()
    monkeypatch.setattr(run_daily, "reconcile_pr", fake_rec)
    rec = run_daily.dispatch_one(VL._entry(), VL._prof(repo), "20260718", VL._args())
    assert rec["status"] == "verify_invalid"
    assert "不变式破坏" in (rec.get("skip_reason") or "")
    assert _c == [True] and rec.get("verify_verdict") is None


def test_agent_track_report_run_identity(tmp_path, agent_env):
    """本体论审核⑤F7（agent-verify-injection 收尾修）：verify-report run 身份非空壳——
    independent_verify 透传 project/iteration 进 build_verify_report（spec「报告覆盖 run
    identity」；triage 按 report 定位 iteration 时身份面不可为空串）。"""
    _t, runs = agent_env
    suite = _store_jest_suite(tmp_path / "acceptance_store")
    FakeExecutor.stream = _raw([_ev_use("t1", DEC_JEST), _ev_res("t1", "1 passing"), _ev_meta()])
    repo = _repo_with_branch(tmp_path)
    out = run_daily.independent_verify(str(repo), "feat/x", "st", "slug", runs / "st_slug.log",
                                       prof=None, test_cmd_hint=None, suite_ref=suite,
                                       project="cc-web-control", iteration="st:prd-x:2")
    assert out["verdict"] == "green"
    rep = json.loads((runs / "st_slug.verify-report.json").read_text(encoding="utf-8"))
    assert rep["run"]["project"] == "cc-web-control"
    assert rep["run"]["iteration_id"] == "st:prd-x:2"
    # 缺省形态（旧调用面）零崩溃——空串回退保持
    out2 = run_daily.independent_verify(str(repo), "feat/x", "st", "slug", runs / "st_slug.log",
                                        prof=None, test_cmd_hint=None, suite_ref=suite)
    assert out2["verdict"] == "green"
