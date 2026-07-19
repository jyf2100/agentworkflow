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
