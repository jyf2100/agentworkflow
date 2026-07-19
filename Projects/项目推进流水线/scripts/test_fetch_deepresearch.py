import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import run_daily


def test_run_persona_allowed_tools_appended(monkeypatch):
    captured = {}
    class _P:
        returncode = 0
        stdout = json.dumps({"is_error": False, "result": '{"ok": true}', "total_cost_usd": 0.01, "num_turns": 1})
        stderr = ""
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _P()
    monkeypatch.setattr(run_daily.subprocess, "run", fake_run)
    run_daily.run_persona("pa-x", "hi", "radar", "t1", allowed_tools=["mcp__a__b", "mcp__c__d"])
    cmd = captured["cmd"]
    assert "--allowedTools" in cmd
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__a__b,mcp__c__d"


def test_run_persona_no_allowed_tools_omits_flag(monkeypatch):
    captured = {}
    class _P:
        returncode = 0
        stdout = json.dumps({"is_error": False, "result": '{"ok": true}', "total_cost_usd": 0.01, "num_turns": 1})
        stderr = ""
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _P()
    monkeypatch.setattr(run_daily.subprocess, "run", fake_run)
    run_daily.run_persona("pa-x", "hi", "radar", "t1")
    assert "--allowedTools" not in captured["cmd"]


def test_stage_fetch_writes_md_with_stamp_slug(tmp_path, monkeypatch):
    run_daily.VAULT_ROOT = tmp_path
    run_daily.STATE_DIR = tmp_path / "state"; run_daily.STATE_DIR.mkdir()
    src = {"name": "quant-research", "kind": "agent-deepresearch",
           "root": "深研/quant", "params": {"prompts": ["A股量化"]},
           "marker": "state/consumed_quant_research", "target_projects": ["ashare-llm-analyst"]}
    def fake_persona(name, prompt, stage, label, allowed_tools=None):
        return ({"title": "Ashare LLM Quant", "markdown": "# Ashare LLM Quant\n正文带[1]引用。",
                 "sources_count": 6, "confidence": "Medium"},
                {"cost": 0.12, "turns": 10, "session_id": "s", "duration_ms": 1, "model": {}})
    monkeypatch.setattr(run_daily, "run_persona", fake_persona)
    class A: dry_run = False
    out = run_daily.stage_fetch(A(), [src], "20260719")
    f = tmp_path / "深研/quant/20260719_ashare-llm-quant.md"
    assert f.is_file()
    assert "正文带[1]引用" in f.read_text(encoding="utf-8")
    assert out["produced"][0]["source"] == "quant-research"
    assert out["produced"][0]["sources_count"] == 6


def test_stage_fetch_skips_non_deepresearch_kinds(tmp_path, monkeypatch):
    run_daily.VAULT_ROOT = tmp_path; run_daily.STATE_DIR = tmp_path / "state"; run_daily.STATE_DIR.mkdir()
    called = []
    srcs = [{"name": "wechat", "kind": "directory", "root": "w", "marker": "m1"},
            {"name": "drop", "kind": "local-file", "root": "d", "marker": "m2"}]
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a, **k: called.append(a) or ({"markdown": "x"}, {"cost": 0, "turns": 1}))
    class A: dry_run = False
    run_daily.stage_fetch(A(), srcs, "20260719")
    assert called == []   # directory / local-file 无 fetcher，不调 agent


def test_stage_fetch_no_marker_mutation(tmp_path, monkeypatch):
    run_daily.VAULT_ROOT = tmp_path; run_daily.STATE_DIR = tmp_path / "state"; run_daily.STATE_DIR.mkdir()
    (tmp_path / ".project-auto/state/consumed_quant_research").parent.mkdir(parents=True)
    mp = tmp_path / ".project-auto/state/consumed_quant_research"; mp.write_text("20260701")
    src = {"name": "q", "kind": "agent-deepresearch", "root": "q", "params": {"prompts": ["x"]},
           "marker": "state/consumed_quant_research"}
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a, **k: ({"title": "T", "markdown": "# T", "sources_count": 1},
                                         {"cost": 0, "turns": 1, "session_id": "s", "duration_ms": 1, "model": {}}))
    class A: dry_run = False
    run_daily.stage_fetch(A(), [src], "20260719")
    assert mp.read_text() == "20260701"   # fetch 不碰 marker（radar 消费后才 bump）


def test_stage_fetch_empty_markdown_skipped(tmp_path, monkeypatch):
    run_daily.VAULT_ROOT = tmp_path; run_daily.STATE_DIR = tmp_path / "state"; run_daily.STATE_DIR.mkdir()
    src = {"name": "q", "kind": "agent-deepresearch", "root": "q", "params": {"prompts": ["x"]}, "marker": "m"}
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a, **k: ({"title": "T", "markdown": "   ", "sources_count": 0},
                                         {"cost": 0, "turns": 1, "session_id": "s", "duration_ms": 1, "model": {}}))
    class A: dry_run = False
    out = run_daily.stage_fetch(A(), [src], "20260719")
    assert out["produced"] == []
    assert not (tmp_path / "q").glob("20260719_*.md") or not list((tmp_path / "q").glob("*.md"))


def test_stages_has_fetch_at_zero():
    import importlib
    importlib.reload(run_daily)
    assert run_daily.STAGES[0] == "fetch"
    assert run_daily.STAGES[1] == "radar"   # 原 radar 顺位后移


def test_fetch_timeout_and_maxturns_defined():
    assert "fetch" in run_daily.TIMEOUT and run_daily.TIMEOUT["fetch"] > run_daily.TIMEOUT["radar"]
    assert "fetch" in run_daily.MAX_TURNS and run_daily.MAX_TURNS["fetch"] >= 20


def test_stage_fetch_reuse_gate_short_circuits(tmp_path, monkeypatch):
    """fetch_{stamp}.json 已存在且非 --force → 复用，不调 agent（成本护栏，镜像 radar:497-500）。"""
    run_daily.VAULT_ROOT = tmp_path; run_daily.STATE_DIR = tmp_path / "state"; run_daily.STATE_DIR.mkdir()
    pre = {"produced": [{"source": "quant-research", "path": "深研/quant/20260719_x.md",
                         "sources_count": 9, "cost": 0.5, "turns": 7}], "stamp": "20260719"}
    (run_daily.STATE_DIR / "fetch_20260719.json").write_text(
        json.dumps(pre, ensure_ascii=False), encoding="utf-8")
    called = []
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a, **k: called.append(a) or ({"markdown": "x"}, {"cost": 0, "turns": 1}))
    class A:
        dry_run = False
    out = run_daily.stage_fetch(A(), [{"name": "q", "kind": "agent-deepresearch",
                                       "root": "q", "marker": "m"}], "20260719")
    assert called == []          # 没调 agent（不重花 ~$0.9/次）
    assert out == pre            # 返回已有产物


def test_stage_fetch_force_bypasses_reuse_gate(tmp_path, monkeypatch):
    """--force → 即使 fetch_{stamp}.json 存在也重跑（镜像 radar，锁 force-bypass 契约）。"""
    run_daily.VAULT_ROOT = tmp_path; run_daily.STATE_DIR = tmp_path / "state"; run_daily.STATE_DIR.mkdir()
    (run_daily.STATE_DIR / "fetch_20260719.json").write_text(
        '{"produced": [], "stamp": "20260719"}', encoding="utf-8")
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a, **k: ({"title": "T", "markdown": "# T", "sources_count": 1},
                                         {"cost": 0, "turns": 1, "session_id": "s",
                                          "duration_ms": 1, "model": {}}))
    class A:
        dry_run = False; force = True
    out = run_daily.stage_fetch(A(), [{"name": "q", "kind": "agent-deepresearch",
                                       "root": "q", "params": {"prompts": ["x"]}, "marker": "m"}], "20260719")
    assert out["produced"] and out["produced"][0]["source"] == "q"   # 重跑了


def test_stage_fetch_per_source_isolation(tmp_path, monkeypatch):
    """一个 agent-deepresearch 源 run_persona 抛错 → 跳过它，不拖垮其他源，fetch_{stamp}.json 照写。"""
    run_daily.VAULT_ROOT = tmp_path; run_daily.STATE_DIR = tmp_path / "state"; run_daily.STATE_DIR.mkdir()
    srcs = [{"name": "bad", "kind": "agent-deepresearch", "root": "bad", "marker": "m1"},
            {"name": "good", "kind": "agent-deepresearch", "root": "good",
             "params": {"prompts": ["x"]}, "marker": "m2"}]

    def fake_persona(name, prompt, stage, label, allowed_tools=None):
        if label == "fetch-bad":
            raise RuntimeError("exa outage")
        return ({"title": "Good", "markdown": "# Good", "sources_count": 2},
                {"cost": 0.1, "turns": 3, "session_id": "s", "duration_ms": 1, "model": {}})
    monkeypatch.setattr(run_daily, "run_persona", fake_persona)
    class A:
        dry_run = False
    out = run_daily.stage_fetch(A(), srcs, "20260719")
    assert [p["source"] for p in out["produced"]] == ["good"]   # bad 被隔离，good 正常
    assert (tmp_path / "good/20260719_good.md").is_file()
