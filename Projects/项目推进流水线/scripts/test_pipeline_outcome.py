"""add-pipeline-outcome-observability（2026-10-29 立案）：业务终态结构化出口四件套。

pipeline_outcome.json 契约（机器可读单文件，report 段终笔）/ 编排器 stdout tee
落 state / dispatch record pr_number+pr_url 对称 / verify-report 逐命令
output_ref（红因诊断不翻 10k 行原始流）。四轮真机标本（run 92/#83/#84/#85：
容器 exit 0 但业务各异，MM 只收到 succeeded exit 0）钉死根因：业务终态无
结构化出口，通知面退回容器退出码。
"""
import json
from pathlib import Path

import run_daily
import verify_stream as VS


# ── 1.3 PR 身份对称：url 单源析出 number ─────────────────────────────────────
def test_pr_number_from_url():
    assert run_daily._pr_number_from_url("https://github.com/o/r/pull/60") == 60
    assert run_daily._pr_number_from_url("https://github.com/o/r/pull/60/") == 60
    assert run_daily._pr_number_from_url("https://gitlab.com/g/p/-/merge_requests/9") is None
    assert run_daily._pr_number_from_url(None) is None


# ── 1.1 outcome 契约：红导出红（容器退出码降附录）+ 兜底对称 ──────────────────
def test_write_pipeline_outcome_red_not_exit_green(tmp_path, monkeypatch):
    disp = [{"slug": "s1", "project": "p", "status": "pr_open",
             "pr_url": "https://github.com/o/r/pull/60", "pr_number": None,   # 旧 record 形态
             "verify": {"verdict": "red", "pass": False, "note": "红→标 failing"},
             "skip_reason": None, "triage_reason": None}]
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    p = run_daily._write_pipeline_outcome(disp, "20261029m1", exit_code=0)
    o = json.loads(p.read_text())
    assert p.name == "pipeline_outcome_20261029m1.json"
    assert o["schema"] == "pa.pipeline_outcome.v1"
    assert o["summary"]["red"] == 1 and o["summary"]["green"] == 0
    pr0 = o["prs"][0]
    assert pr0["verify_verdict"] == "red" and pr0["status"] == "pr_open"
    assert pr0["pr_number"] == 60 and pr0["pr_url"].endswith("/pull/60")   # 兜底从 url 析出
    assert pr0["reason_headline"].startswith("红→标 failing")              # note 首个非空
    assert o["container"]["exit_code"] == 0                                # 附录非判据


def test_write_pipeline_outcome_counts_all_buckets(tmp_path, monkeypatch):
    mk = lambda s, v=None: {"slug": s, "project": "p", "status": s, "pr_url": None,
                            "pr_number": None, "verify": ({"verdict": v, "pass": v == "green"} if v else None),
                            "skip_reason": "异常: x" if s in ("skip", "fail") else None,
                            "triage_reason": None}
    disp = [mk("pr_open", "green"), mk("pr_open", "red"), mk("verify_invalid", "invalid"),
            mk("blocked_external_state", None), mk("fail", None)]
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    o = json.loads(run_daily._write_pipeline_outcome(disp, "s", exit_code=0).read_text())
    sm = o["summary"]
    assert (sm["green"], sm["red"], sm["invalid"], sm["blocked"], sm["abnormal"]) == (1, 1, 1, 1, 1)
    assert o["prs"][4]["reason_headline"].startswith("异常: x")           # skip_reason 兜底首行


def test_write_pipeline_outcome_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    def boom(*a, **k): raise OSError("disk full")
    monkeypatch.setattr(Path, "write_text", boom)
    assert run_daily._write_pipeline_outcome([], "s", exit_code=0) is None  # warn 不拦 report


def test_stage_report_writes_outcome(tmp_path, monkeypatch):
    """report 段终笔出口（--from-stage report 重跑路径同命中：自包含从盘读 state）。"""
    monkeypatch.setattr(run_daily, "STATE_DIR", tmp_path)
    monkeypatch.setattr(run_daily, "REPORT_DIR", tmp_path / "rep")
    monkeypatch.setattr(run_daily, "DAILY_DIR", tmp_path / "daily")
    for n in ("candidates_s.json", "prd_gate_s.json", "dispatch_s.json", "fetch_s.json"):
        (tmp_path / n).write_text("{}" if n.startswith(("cand", "fetch")) else "[]")
    args = type("A", (), {"limit": 0, "no_notify": True, "dry_run": True})()
    run_daily.stage_report(args, {}, "s")
    assert (tmp_path / "pipeline_outcome_s.json").exists()


# ── 1.2 stdout tee：观测面不拦编排 ────────────────────────────────────────────
def test_stdout_tee(tmp_path):
    p = tmp_path / "run.stdout.log"
    run_daily._stdout_tee_open(p)
    try:
        run_daily.log("hello-tee")
    finally:
        run_daily._stdout_tee_close()
    assert "hello-tee" in p.read_text()


def test_stdout_tee_unwritable_degrades(tmp_path):
    blk = tmp_path / "blk"                     # 占位为文件：其下 mkdir 抛 NotADirectoryError
    blk.write_text("x")
    run_daily._stdout_tee_open(blk / "f.log")  # open 失败 → warn 降级仅终端
    run_daily.log("still-alive")               # 不抛：终端面不受影响
    run_daily._stdout_tee_close()


# ── 1.4 verify-report 逐命令 output_ref ─────────────────────────────────────
def test_build_verify_report_output_ref():
    ex = VS.BashExecution(command="npm test", tool_use_id="t1", is_error=False, exit_code=0, output="ok")
    r = VS.build_verify_report(run_id="r", project="p", prd_id="d", iteration_id="i", harness="pytest",
                               gate_cmd="npm test", gate_source="dev-reported",
                               matched={"npm test": ex, "pytest -q": None},
                               anchor_workspace_ok=True, anchor_digest_violations=[],
                               stream_path="s", session_meta={}, env_spec="", env_observed={},
                               cmd_outputs={"npm test": "runs/x.cmd0.out"})
    refs = [x.get("output_ref") for x in r["commands"]["results"]]
    assert refs == ["runs/x.cmd0.out", None]        # 匹配命令持指针；unresolved 无


def test_write_per_cmd_outputs(tmp_path):
    """agent 轨逐命令输出：落 <stem>.cmd<k>.out（declared 序号），unresolved 无键。"""
    lf = tmp_path / "v.log"
    lf.write_text("")
    ex0 = VS.BashExecution(command="npx playwright test x.spec.ts", tool_use_id="t1",
                           is_error=True, exit_code=1, output="Error: webServer port")
    ex1 = VS.BashExecution(command="npm test", tool_use_id="t2", is_error=False, exit_code=0, output="ok 1-13")
    m = run_daily._write_per_cmd_outputs(
        lf, {"npx playwright test x.spec.ts": ex0, "npm test": ex1, "pytest -q": None})
    p0 = m["npx playwright test x.spec.ts"]
    assert p0.endswith(".cmd0.out") and "webServer port" in Path(p0).read_text()   # 红因就地可诊
    assert m["npm test"].endswith(".cmd1.out")
    assert m.get("pytest -q") is None                                             # unresolved 无输出无键
