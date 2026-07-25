import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import run_daily


class A:
    dry_run = False


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
    # r8-5：本测试测 cmd 构造（--allowedTools 附加），不依赖真 claude 二进制；monkeypatch resolve_claude_bin
    # 绕过 CLI 存在性检查（CI 无 claude → 旧 sys.exit SystemExit），CI 也能跑 + 覆盖 cmd 构造逻辑。
    monkeypatch.setattr(run_daily, "resolve_claude_bin", lambda: "/fake/claude")
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
    # r8-5：同上——monkeypatch resolve_claude_bin 绕过 CLI 检查，CI 无 claude 也能跑。
    monkeypatch.setattr(run_daily, "resolve_claude_bin", lambda: "/fake/claude")
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


def test_stage_fetch_dispatches_by_kind_via_fetch_config(tmp_path, monkeypatch):
    """FETCH_CONFIG[kind] 分发：kind 在配置里 → 用对应 agent/tools；不在 → 跳过。"""
    run_daily.VAULT_ROOT = tmp_path; run_daily.STATE_DIR = tmp_path / "state"; run_daily.STATE_DIR.mkdir()
    called = {}
    def fake_persona(name, prompt, stage, label, allowed_tools=None):
        called[name] = allowed_tools
        return ({"items": [{"title": "T1", "markdown": "# T1"}, {"title": "T2", "markdown": "# T2"}]},
                {"cost": 0.1, "turns": 3, "session_id": "s", "duration_ms": 1, "model": {}})
    monkeypatch.setattr(run_daily, "run_persona", fake_persona)
    monkeypatch.setitem(run_daily.FETCH_CONFIG, "wechat-url", {
        "agent": "pa-fetch-wechat-url", "tools": ["mcp__web_reader__webReader"],
        "prompt": lambda s: "p", "mode": "items"})
    src = {"name": "wx", "kind": "wechat-url", "root": "wx", "marker": "m",
           "params": {"urls": ["http://x"]}}
    out = run_daily.stage_fetch(A(), [src], "20260719")
    assert called["pa-fetch-wechat-url"] == ["mcp__web_reader__webReader"]
    paths = [p["path"] for p in out["produced"]]
    assert any("20260719_t1" in p for p in paths)
    assert any("20260719_t2" in p for p in paths)
    assert (tmp_path / "wx/20260719_t1.md").read_text(encoding="utf-8") == "# T1"


def test_stage_fetch_items_mode_skips_empty_md(tmp_path, monkeypatch):
    """items 模式：某 item markdown 空 → 跳该 item，其余照落（per-item fault isolation）。"""
    run_daily.VAULT_ROOT = tmp_path; run_daily.STATE_DIR = tmp_path / "state"; run_daily.STATE_DIR.mkdir()
    monkeypatch.setattr(run_daily, "run_persona", lambda *a, **k:
        ({"items": [{"title": "ok", "markdown": "# OK"}, {"title": "blank", "markdown": "   "}]},
         {"cost": 0.1, "turns": 2, "session_id": "s", "duration_ms": 1, "model": {}}))
    monkeypatch.setitem(run_daily.FETCH_CONFIG, "wechat-url", {
        "agent": "pa-fetch-wechat-url", "tools": [], "prompt": lambda s: "p", "mode": "items"})
    out = run_daily.stage_fetch(A(), [{"name": "wx", "kind": "wechat-url", "root": "wx", "marker": "m"}], "20260719")
    titles = [p["title"] for p in out["produced"]]
    assert titles == ["ok"]
    assert not (tmp_path / "wx/20260719_blank.md").exists()


def test_stage_fetch_skips_kind_not_in_fetch_config(tmp_path, monkeypatch):
    """directory / local-file / 未知 kind 不在 FETCH_CONFIG → 跳过，不调 agent。"""
    run_daily.VAULT_ROOT = tmp_path; run_daily.STATE_DIR = tmp_path / "state"; run_daily.STATE_DIR.mkdir()
    called = []
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a, **k: called.append(a) or ({"markdown": "x"}, {"cost": 0, "turns": 1}))
    run_daily.stage_fetch(A(), [
        {"name": "d", "kind": "directory", "root": "d", "marker": "m1"},
        {"name": "lf", "kind": "local-file", "root": "lf", "marker": "m2"},
        {"name": "?", "kind": "no-such-kind", "root": "x", "marker": "m3"},
    ], "20260719")
    assert called == []


def test_wechat_url_prompt_embeds_urls():
    src = {"name": "wx", "kind": "wechat-url", "params": {"urls": ["https://mp.weixin.qq.com/s/AAA", "https://mp.weixin.qq.com/s/BBB"]}}
    p = run_daily.wechat_url_prompt(src)
    assert "https://mp.weixin.qq.com/s/AAA" in p and "https://mp.weixin.qq.com/s/BBB" in p
    assert "pa-fetch-wechat-url" in p and "items" in p          # 契约点名


def test_stage_fetch_wechat_url_writes_one_file_per_url(tmp_path, monkeypatch):
    """wechat-url 端到端：mock run_persona 吐 items → 每篇一文件，slug 来自 title。"""
    run_daily.VAULT_ROOT = tmp_path; run_daily.STATE_DIR = tmp_path / "state"; run_daily.STATE_DIR.mkdir()
    monkeypatch.setattr(run_daily, "run_persona", lambda *a, **k:
        ({"items": [{"url": "u1", "title": "Wechat Article One", "markdown": "# One\n正文", "fetched_via": "web_reader", "ok": True},
                    {"url": "u2", "title": "Article Two", "markdown": "# Two", "fetched_via": "exa", "ok": True}]},
         {"cost": 0.2, "turns": 5, "session_id": "s", "duration_ms": 1, "model": {}}))
    src = {"name": "wx-picked", "kind": "wechat-url", "root": "微信精选",
           "params": {"urls": ["u1", "u2"]}, "marker": "m"}
    out = run_daily.stage_fetch(A(), [src], "20260719")
    assert len(out["produced"]) == 2
    assert (tmp_path / "微信精选/20260719_wechat-article-one.md").read_text(encoding="utf-8").startswith("# One")
    assert (tmp_path / "微信精选/20260719_article-two.md").is_file()


def test_github_repo_prompt_embeds_repos_and_window():
    src = {"name": "gh", "kind": "github-repo",
           "params": {"repos": ["akfamily/akshare", "pallets/flask"], "window": "3d"}}
    p = run_daily.github_repo_prompt(src)
    assert "akfamily/akshare" in p and "pallets/flask" in p
    assert "3d" in p and "gh api" in p and "pa-fetch-github-repo" in p


def test_stage_fetch_github_repo_writes_one_file_per_repo(tmp_path, monkeypatch):
    run_daily.VAULT_ROOT = tmp_path; run_daily.STATE_DIR = tmp_path / "state"; run_daily.STATE_DIR.mkdir()
    monkeypatch.setattr(run_daily, "run_persona", lambda *a, **k:
        ({"items": [{"repo": "pallets/flask", "title": "pallets-flask 7d digest",
                     "markdown": "# flask 近 7 天\n- commit A", "commits_count": 5, "prs_count": 1}]},
         {"cost": 0.15, "turns": 6, "session_id": "s", "duration_ms": 1, "model": {}}))
    src = {"name": "gh-watch", "kind": "github-repo", "root": "gh-watch",
           "params": {"repos": ["pallets/flask"], "window": "7d"}, "marker": "m"}
    out = run_daily.stage_fetch(A(), [src], "20260719")
    assert len(out["produced"]) == 1
    f = tmp_path / "gh-watch/20260719_pallets-flask-7d-digest.md"
    assert f.is_file() and "commit A" in f.read_text(encoding="utf-8")


def test_fetch_config_github_repo_uses_bash_not_github_mcp():
    """② 走 gh CLI 经 Bash（非 mcp__plugin_ecc_github__*——headless 不可用）。
    冒烟：Bash(gh api:*) 限定语法仍触发 denial → plain Bash 保 cron 鲁棒（persona 硬约束只跑 gh api）。"""
    cfg = run_daily.FETCH_CONFIG["github-repo"]
    assert cfg["agent"] == "pa-fetch-github-repo"
    assert cfg["tools"] == ["Bash"]            # 非 mcp__plugin_ecc_github__*；plain Bash（scoped 冒烟有 denial）
    assert cfg["mode"] == "items"
