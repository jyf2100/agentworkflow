# ADR-0007 follow-up ①②：wechat-url + github-repo fetcher 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 pa 流水线加两种 persona-based 采集源 fetcher——`wechat-url`（web_reader 抓微信文章）+ `github-repo`（gh CLI 监控仓库活动），把 `stage_fetch` 从「只认 agent-deepresearch」泛化成「按 kind 分发」。

**Architecture:** 采集层统一 = 每 kind 一个薄 headless persona（与 `pa-fetch-deepresearch` 同形）+ `FETCH_CONFIG={kind:(persona,tools,prompt,mode)}` 分发字典。`mode="single"`（deepresearch 合成式，一份 md）已存在；新增 `mode="items"`（①② 枚举式，一次 agent 调用产 N 篇 → N 个 `YYYYMMDD_<slug>.md`，Contract A）。消费侧（radar/discover）**零改动**——仍按 `content_glob` 扫目录，kind 无关。

**Tech Stack:** Python 3（`run_daily.py`，cron 用 `/usr/bin/python3` 无 sdk——顶部 import 禁触 sdk）、headless `claude --agent <name> -p --output-format json`（`run_persona`）、MCP（web_reader + exa，headless 可用）、gh CLI（headless 经 Bash 可用）。

**冒烟前置结论（本计划的前提，已实测）：**
- `mcp__web_reader__webReader` 在 headless `claude -p` **可用** ✅（实测抓 example.com 成功）。
- `mcp__plugin_ecc_github__*` 在 headless **不可用** ❌（只在交互 session 注入；headless 工具集仅 `context7/exa/web_reader/4_5v_mcp`）→ ② 走 `gh` CLI（Bash），**不走 github MCP**。
- `gh` 已 auth 为 `jyf2100`（token 落 `~/.config/gh/hosts.yml`，cron 可继承）；headless `claude -p --allowedTools Bash` 跑 `gh api repos/pallets/flask/commits` 成功取回数据（1 次 `permission_denials` 后 3 turn 内自愈——用 `--allowedTools "Bash(gh api:*)"` 限定范围预授权可消掉）。

**关键约束（继承自 ADR-0007 / 既有架构）：**
- vault 是本地独立仓（never push）；提交时机由用户定，计划只列 `git add`/`commit` 步骤供执行期按需跑。
- `run_daily.py` 顶部 import 禁触 `claude_agent_sdk`（cron `/usr/bin/python3` 无 sdk）。
- fetcher **全被动 + 解耦**：persona 不碰盘、不自盖 `YYYYMMDD` 戳、不生成 PRD/candidates；编排器落盘 + radar 消费（ADR-0007 #3）。
- 采集层身份：source `name` 唯一 + `root` 排他（`load_sources` 硬错）。
- persona-based 源在 `sources.yaml` **不声明 `fetcher:` 键**（声明会触发 `load_sources` 的 missing-script warn——见 `quant-research` 注释 sources.yaml:31-33）。

---

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `Projects/项目推进流水线/scripts/run_daily.py` | 修改（~445-501） | `FETCH_CONFIG` 分发 + `mode` + `_payload_to_items` + 2 个 prompt builder；泛化 `stage_fetch` |
| `Projects/项目推进流水线/scripts/test_fetch_deepresearch.py` | 修改（加用例） | 补 items-mode / 分发 / 单 item 跳空 md 用例；保 ③ 既有 11 用例绿 |
| `.claude/agents/pa-fetch-wechat-url.md` | 新建 | ① 微信文章采集 headless persona（web_reader + exa 兜底，Contract A） |
| `.claude/agents/pa-fetch-github-repo.md` | 新建 | ② GitHub 仓库监控 headless persona（gh CLI / Bash，Contract A） |
| `.project-auto/sources.yaml` | 修改（gitignored） | 加 `wechat-url` + `github-repo` 两条源（无 `fetcher:` 键，persona-based） |
| `Projects/项目推进流水线/docs/adr/0007-multi-source-radar.md` | 修改 | schema 去 `fetcher:`、kind 表 ⏳→✅、follow-up ①②✅、补冒烟发现（github MCP headless 不可用→gh CLI） |

**不改动**：`discover_today_new`（kind 无关，零分支）、`stage_radar`、`load_sources`（persona 源无 `fetcher:` 不触发 warn）、`slug_utils.dev_slugify`、`pa-fetch-deepresearch.md`（③ 已 ship，contract 保持 single-mode 不动）。

---

## Task 1：`FETCH_CONFIG` 分发 + Contract A per-item 落盘（共享骨架）

**Files:**
- Modify: `Projects/项目推进流水线/scripts/run_daily.py:444-501`
- Test: `Projects/项目推进流水线/scripts/test_fetch_deepresearch.py`

把 `stage_fetch` 从「硬编码 agent-deepresearch、单 doc 落盘」泛化成「按 `FETCH_CONFIG[kind]` 分发、`mode` 决定单 doc / N items」。**保 ③ 既有 11 用例全绿**（produced 透传 `sources_count`，single-mode 形状不变）。

- [ ] **Step 1.1：先写失败测试（dispatch + items-mode + 单 item 跳空）**

追加到 `test_fetch_deepresearch.py` 末尾（复用既有 import 模式 `sys.path.insert; import run_daily` + `monkeypatch.setattr(run_daily,"run_persona",fake)` + `class A: dry_run=False`）：

```python
def test_stage_fetch_dispatches_by_kind_via_fetch_config(tmp_path, monkeypatch):
    """FETCH_CONFIG[kind] 分发：kind 在配置里 → 用对应 agent/tools；不在 → 跳过。"""
    run_daily.VAULT_ROOT = tmp_path; run_daily.STATE_DIR = tmp_path / "state"; run_daily.STATE_DIR.mkdir()
    called = {}
    def fake_persona(name, prompt, stage, label, allowed_tools=None):
        called[name] = allowed_tools
        return ({"items": [{"title": "T1", "markdown": "# T1"}, {"title": "T2", "markdown": "# T2"}]},
                {"cost": 0.1, "turns": 3, "session_id": "s", "duration_ms": 1, "model": {}})
    monkeypatch.setattr(run_daily, "run_persona", fake_persona)
    # 临时把 wechat-url 注入 FETCH_CONFIG（Task 2 才正式加，这里只验分发机制）
    run_daily.FETCH_CONFIG["wechat-url"] = {
        "agent": "pa-fetch-wechat-url", "tools": ["mcp__web_reader__webReader"],
        "prompt": lambda s: "p", "mode": "items"}
    try:
        src = {"name": "wx", "kind": "wechat-url", "root": "wx", "marker": "m",
               "params": {"urls": ["http://x"]}}
        out = run_daily.stage_fetch(A(), [src], "20260719")
        assert called["pa-fetch-wechat-url"] == ["mcp__web_reader__webReader"]   # 按 cfg.tools 传
        paths = [p["path"] for p in out["produced"]]
        assert any("20260719_t1" in p for p in paths)           # Contract A：N items → N 文件
        assert any("20260719_t2" in p for p in paths)
        assert (tmp_path / "wx/20260719_t1.md").read_text(encoding="utf-8") == "# T1"
    finally:
        run_daily.FETCH_CONFIG.pop("wechat-url", None)          # 清理，不污染其他用例


def test_stage_fetch_items_mode_skips_empty_md(tmp_path, monkeypatch):
    """items 模式：某 item markdown 空 → 跳该 item，其余照落（per-item fault isolation）。"""
    run_daily.VAULT_ROOT = tmp_path; run_daily.STATE_DIR = tmp_path / "state"; run_daily.STATE_DIR.mkdir()
    monkeypatch.setattr(run_daily, "run_persona", lambda *a, **k:
        ({"items": [{"title": "ok", "markdown": "# OK"}, {"title": "blank", "markdown": "   "}]},
         {"cost": 0.1, "turns": 2, "session_id": "s", "duration_ms": 1, "model": {}}))
    run_daily.FETCH_CONFIG["wechat-url"] = {
        "agent": "pa-fetch-wechat-url", "tools": [], "prompt": lambda s: "p", "mode": "items"}
    try:
        out = run_daily.stage_fetch(A(), [{"name": "wx", "kind": "wechat-url", "root": "wx", "marker": "m"}], "20260719")
        titles = [p["title"] for p in out["produced"]]
        assert titles == ["ok"]                                 # blank 被跳
        assert not (tmp_path / "wx/20260719_blank.md").exists()
    finally:
        run_daily.FETCH_CONFIG.pop("wechat-url", None)


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
    assert called == []                                         # 都没调 agent
```

- [ ] **Step 1.2：跑测试确认失败**

Run: `cd Projects/项目推进流水线 && python3 -m pytest scripts/test_fetch_deepresearch.py -q`
Expected: 3 个新用例 FAIL（`KeyError 'FETCH_CONFIG'`——当前 `stage_fetch` 还硬编码 kind、无 FETCH_CONFIG）。

- [ ] **Step 1.3：实现——替换 run_daily.py:444-501**

把 `FETCH_AGENT`/`FETCH_ALLOWED_TOOLS` 常量保留（deepresearch 条目引用），在其下方加 `wechat_url_prompt`/`github_repo_prompt` 占位（Task 2/3 填实，先放最小版不报错）、`FETCH_CONFIG`、`_payload_to_items`，重写 `stage_fetch`。**用 Edit 替换 444-501 整段**为：

```python
# ─── fetch 段（persona-based 源 fetcher：按 FETCH_CONFIG[kind] 分发 → 落 YYYYMMDD_*.md）────
FETCH_AGENT = "pa-fetch-deepresearch"
# exa MCP 工具白名单（ECC plugin 提供；firecrawl 缺席，单 exa 即可——冒烟已证可用）
FETCH_ALLOWED_TOOLS = ["mcp__plugin_ecc_exa__web_search_exa",
                       "mcp__plugin_ecc_exa__web_fetch_exa"]


def fetch_prompt(src: dict) -> str:
    prompts = (src.get("params") or {}).get("prompts") or []
    topic_block = "\n".join(f"- {p}" for p in prompts) if prompts else f"- {src['name']}"
    return f"""你是 pa-fetch-deepresearch。对以下研究主题做多源深研（exa 搜 → 深读 → 合成带引用 markdown），产出 radar 可消费的信号文件。
研究主题（采集源 {src['name']}）：
{topic_block}

严格按 persona 输出契约：只吐一行 JSON，结构 {{"title":"...","markdown":"<完整带引用 md 全文>","sources_count":N,"confidence":"High|Medium|Low"}}。markdown 字段内换行用 \\n 转义。"""


def wechat_url_prompt(src: dict) -> str:
    """① wechat-url：把 params.urls 喂给 pa-fetch-wechat-url，要 Contract A items JSON。"""
    urls = (src.get("params") or {}).get("urls") or []
    url_block = "\n".join(f"- {u}" for u in urls) if urls else f"- {src['name']}"
    return f"""你是 pa-fetch-wechat-url。抓取以下微信文章（mp.weixin.qq.com）正文，逐篇 normalize 成 markdown。
文章 URL（采集源 {src['name']}）：
{url_block}

每篇先试 mcp__web_reader__webReader(url=..., return_format='markdown')；抓失败/正文明显残缺（如只剩导航）→ 用 mcp__plugin_ecc_exa__web_fetch_exa(urls=[url]) 兜底；都失败该篇 fetched_via='failed'、markdown 留空。
严格按 persona 输出契约：只吐一行 JSON，结构 {{"items":[{{"url":"...","title":"<篇名（ascii 优先便于 slug）>","markdown":"<干净正文 md，换行 \\n 转义>","fetched_via":"web_reader|exa|failed","ok":true}}]}}。"""


def github_repo_prompt(src: dict) -> str:
    """② github-repo：把 params.repos + window 喂给 pa-fetch-github-repo，gh CLI 拉活动，要 items JSON。"""
    params = src.get("params") or {}
    repos = params.get("repos") or []
    window = params.get("window", "7d")
    repo_block = "\n".join(f"- {r}" for r in repos) if repos else f"- {src['name']}"
    return f"""你是 pa-fetch-github-repo。监控以下 GitHub 仓库近 {window} 的活动，逐仓 summarize 成 markdown digest（radar 可消费）。
仓库（采集源 {src['name']}）：
{repo_block}
窗口：{window}

每仓用 Bash 跑 gh CLI：`gh api repos/OWNER/REPO/commits?per_page=30` 拉最近 commit、`gh api repos/OWNER/REPO/pulls?state=all&sort=updated&per_page=20` 拉最近 PR；按窗口筛日期；summarize 成「近期 commit（msg/日期/作者）+ 合并 PR（标题/url）」markdown。github MCP 在 headless 不可用，**必须用 gh CLI**。
严格按 persona 输出契约：只吐一行 JSON，结构 {{"items":[{{"repo":"owner/repo","title":"<owner-repo 窗口摘要（ascii）>","markdown":"<digest md，换行 \\n 转义>","commits_count":N,"prs_count":M}}]}}。"""


# kind → fetcher 配置。mode: "single"=一份合成 md（agent-deepresearch）；
#       "items"=一次调用产 N 篇（wechat-url/github-repo，Contract A 每 item 一文件）。
FETCH_CONFIG: dict[str, dict] = {
    "agent-deepresearch": {"agent": FETCH_AGENT, "tools": FETCH_ALLOWED_TOOLS,
                           "prompt": fetch_prompt, "mode": "single"},
    "wechat-url":         {"agent": "pa-fetch-wechat-url",
                           "tools": ["mcp__web_reader__webReader",
                                     "mcp__plugin_ecc_exa__web_fetch_exa"],
                           "prompt": wechat_url_prompt, "mode": "items"},
    "github-repo":        {"agent": "pa-fetch-github-repo",
                           "tools": ["Bash(gh api:*)"],   # 限定 gh api 子命令预授权（消冒烟里的 permission_denials）
                           "prompt": github_repo_prompt, "mode": "items"},
}


def _payload_to_items(payload: dict, mode: str, src: dict) -> list[tuple[str, str, dict]]:
    """统一落盘视角：single → [(title, md, payload)]；items → payload['items'] 逐条。
    返回 (title, markdown_stripped, raw) 三元组列表，raw 透传 kind-specific 字段（如 sources_count）。"""
    if mode == "items":
        return [(it.get("title") or src["name"], (it.get("markdown") or "").strip(), it)
                for it in (payload.get("items") or [])]
    return [(payload.get("title") or src["name"], (payload.get("markdown") or "").strip(), payload)]


def stage_fetch(args, sources, stamp) -> dict:
    """persona-based 源 fetcher：按 FETCH_CONFIG[kind] 分发 agent → 落 YYYYMMDD_<slug>.md 到 source.root。

    directory/local-file/未知 kind 不在 FETCH_CONFIG → 跳过（无 fetcher）。
    mode=items：一次 agent 调用产 N 篇 → N 文件（Contract A）；mode=single：一份合成 md（agent-deepresearch）。
    fetch 不碰 marker（radar 消费后才 bump，ADR-0007 #3）；--dry-run 不影响 fetch（写文件是 fetch 的全部意义）。
    复用门：fetch_{stamp}.json 已存在且非 --force → 复用（成本护栏，镜像 stage_radar:507-509，防重跑重花）。
    per-source try/except + per-item 跳空 md：一个源/一篇炸只跳过它，不拖垮整段 fetch（fault isolation）。"""
    fetch_file = STATE_DIR / f"fetch_{stamp}.json"
    if fetch_file.is_file() and not getattr(args, "force", False):
        log(f"[fetch] 复用已有 {fetch_file.name}（--force 重跑）")
        return json.loads(fetch_file.read_text(encoding="utf-8"))
    produced = []
    for src in sources:
        cfg = FETCH_CONFIG.get(src.get("kind"))
        if not cfg:                                   # directory/local-file/未知 → 无 fetcher，跳过
            continue
        try:
            root = VAULT_ROOT / src["root"]
            root.mkdir(parents=True, exist_ok=True)
            payload, meta = run_persona(cfg["agent"], cfg["prompt"](src), "fetch",
                                        f"fetch-{src['name']}", allowed_tools=cfg["tools"])
            items = _payload_to_items(payload, cfg["mode"], src)
            if not items:
                log(f"[fetch] ⚠ {src['name']} agent 未返回任何 item（跳过落盘）")
                continue
            src_cost_logged = False
            for title, md, raw in items:
                if not md:
                    log(f"[fetch] ⚠ {src['name']} item「{title}」markdown 空（跳过该 item）")
                    continue
                slug = dev_slugify(title) or src["name"]            # 复用 ADR-0006 单一源头
                out = root / f"{stamp}_{slug}.md"
                out.write_text(md, encoding="utf-8")
                entry = {"source": src["name"], "title": title,
                         "path": str(out.relative_to(VAULT_ROOT)),
                         "cost": meta["cost"], "turns": meta["turns"]}
                if "sources_count" in raw:            # kind-specific 透传（agent-deepresearch；保 ③ 既有测试绿）
                    entry["sources_count"] = raw["sources_count"]
                produced.append(entry)
                log(f"[fetch] ✅ {src['name']} → {out.relative_to(VAULT_ROOT)}｜「{title}」"
                    + (f" sources={raw['sources_count']}" if "sources_count" in raw else ""))
        except Exception as e:
            log(f"[fetch] ✗ {src['name']} 失败（跳过，不拖垮其他源）：{e}")
            continue
    out_json = {"produced": produced, "stamp": stamp}
    fetch_file.write_text(json.dumps(out_json, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_json
```

- [ ] **Step 1.4：跑测试确认全绿（含 ③ 既有 11 用例）**

Run: `cd Projects/项目推进流水线 && python3 -m pytest scripts/test_fetch_deepresearch.py -q`
Expected: 14 passed（11 旧 + 3 新）。**关键回归网**：`test_stage_fetch_writes_md_with_stamp_slug` 仍断言 `produced[0]["sources_count"]==6`——靠 `if "sources_count" in raw` 透传保住。

- [ ] **Step 1.5：cron 副作用硬验证（顶部 import 不触 sdk）**

Run: `cd Projects/项目推进流水线/scripts && /usr/bin/python3 -c "import sys; sys.path.insert(0,'.'); import run_daily; print(len(run_daily.FETCH_CONFIG), run_daily.stage_fetch.__name__)"`
Expected: `3 stage_fetch`（裸 `/usr/bin/python3` 无 sdk 下不崩——FETCH_CONFIG/prompt builder 都只依赖 re/yaml/json，无 sdk 连带）。

- [ ] **Step 1.6：提交**

```bash
cd /mnt/disk01/workspaces/worksummary/vault
git add Projects/项目推进流水线/scripts/run_daily.py Projects/项目推进流水线/scripts/test_fetch_deepresearch.py
git commit -m "feat(pa): stage_fetch 泛化为 FETCH_CONFIG kind 分发 + Contract A per-item 落盘"
```

---

## Task 2：① pa-fetch-wechat-url persona + 接线

**Files:**
- Create: `.claude/agents/pa-fetch-wechat-url.md`
- Test: `Projects/项目推进流水线/scripts/test_fetch_deepresearch.py`

FETCH_CONFIG 的 `wechat-url` 条目 Task 1 已就位（prompt + tools + mode=items）。本任务建 persona 文件 + 补 prompt-builder 用例。

- [ ] **Step 2.1：先写失败测试（wechat_url_prompt 含 urls + stage_fetch 端到端 items 落盘）**

追加到 `test_fetch_deepresearch.py`：

```python
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
```

- [ ] **Step 2.2：跑确认失败**

Run: `cd Projects/项目推进流水线 && python3 -m pytest scripts/test_fetch_deepresearch.py::test_wechat_url_prompt_embeds_urls scripts/test_fetch_deepresearch.py::test_stage_fetch_wechat_url_writes_one_file_per_url -q`
Expected: `test_stage_fetch_wechat_url_writes_one_file_per_url` PASS（Task 1 已接好 wechat-url 分发）；`test_wechat_url_prompt_embeds_urls` PASS（Task 1 已加 `wechat_url_prompt`）。若都 PASS 说明 Task 1 已覆盖——保留作回归网。

- [ ] **Step 2.3：新建 persona `.claude/agents/pa-fetch-wechat-url.md`**

镜像 `pa-fetch-deepresearch.md` 结构（frontmatter `name`/`description`/`tools` + body）。完整内容：

```markdown
---
name: pa-fetch-wechat-url
description: 项目推进流水线·微信文章采集（控制面 headless persona）。对一组 mp.weixin.qq.com URL，用 web_reader 抓正文（exa web_fetch 兜底），逐篇 normalize 成 markdown，每篇一个 item 作为一行 JSON {items:[...]} 返回给编排器落盘。由编排器 scripts/run_daily.py 经 `claude --agent pa-fetch-wechat-url -p --allowedTools ...` 链式调用。
tools: mcp__web_reader__webReader, mcp__plugin_ecc_exa__web_fetch_exa
---

# pa-fetch-wechat-url · 微信文章采集（控制面，headless）

> For future Claude：你是「项目推进流水线」采集段的 **wechat-url 源 fetcher**。编排器把「一组微信文章 URL（该源的 params.urls）」喂给你；你逐篇抓正文、normalize 成干净 markdown，**每篇一个 item** 作为一行 JSON 返回。编排器负责落盘到 `source.root/YYYYMMDD_<slug>.md`、radar 负责拾取——你不碰盘。

## 你会收到什么（编排器 prompt 提供）

1. **文章 URL 列表**：该源的 `params.urls`（1~N 条 `mp.weixin.qq.com/s/...`）。

## 你做什么

逐 URL：
1. **首选 web_reader**：`mcp__web_reader__webReader(url=<url>, return_format='markdown')`。它对反爬/JS 页面抽取能力最强。
2. **失败兜底 exa**：web_reader 抛错、或返回正文明显残缺（只剩导航/菜单、正文 < 200 字、含「环境异常/验证」），改用 `mcp__plugin_ecc_exa__web_fetch_exa(urls=[<url>])`。
3. **都失败**：该 item `fetched_via='failed'`、`markdown=''`、`ok=false`（编排器会跳过空 markdown，不落盘）。
4. **normalize**：剥公众号壳（顶部分享条、底部二维码/阅读原文/点赞在看）、保留正文（标题/作者/正文/图片 alt）。title 取文章 `<title>` 或正文首行 H1，**ascii 优先便于 slug**（CJK 会被 `dev_slugify` 丢，故 title 尽量带 ascii 词或纯英文摘要）。

## 输出契约（硬性）

**只输出一行 JSON**，无多余文字、无 markdown 代码块包裹、无解释。结构：

```
{"items":[{"url":"<原 URL>","title":"<篇名（ascii 优先）>","markdown":"<干净正文 md 全文>","fetched_via":"web_reader|exa|failed","ok":true}]}
```

- `markdown` 是**完整**正文（换行用 `\n` 转义）；`items` 长度 = 输入 URL 数（含失败项，编排器按 markdown 是否空决定落盘）。

## 硬约束

- **不编造**：正文必须来自 web_reader/exa 实抓结果；抓不到就 `failed`，绝不凭 URL 猜内容。
- **只吐那一行 JSON**：多一个字算失败（headless 结构化输出硬要求）。

## 禁区

- 不写任何文件（落盘是编排器的活，ADR-0001）。
- 不自行决定 `YYYYMMDD` 文件名（编排器按采集日盖戳）。
- 不生成 PRD / candidates（那是 pa-prd / pa-radar 的活）。
```

- [ ] **Step 2.4：persona 可被 headless 解析验证**

Run: `cd /mnt/disk01/workspaces/worksummary/vault && head -5 .claude/agents/pa-fetch-wechat-url.md`
Expected: frontmatter `name: pa-fetch-wechat-url` + `tools:` 两工具齐全（web_reader + exa）。

- [ ] **Step 2.5：提交**

```bash
git add .claude/agents/pa-fetch-wechat-url.md Projects/项目推进流水线/scripts/test_fetch_deepresearch.py
git commit -m "feat(pa): ① pa-fetch-wechat-url persona（web_reader + exa 兜底，Contract A）"
```

---

## Task 3：② pa-fetch-github-repo persona + 接线 + Bash(gh api:*) 预授权验证

**Files:**
- Create: `.claude/agents/pa-fetch-github-repo.md`
- Test: `Projects/项目推进流水线/scripts/test_fetch_deepresearch.py`

FETCH_CONFIG 的 `github-repo` 条目 Task 1 已就位（`tools: ["Bash(gh api:*)"]`, mode=items）。本任务建 persona + 验证 `Bash(gh api:*)` 限定范围真能消 permission_denials（冒烟里 plain Bash 有过 1 次 denial）。

- [ ] **Step 3.1：先写测试（github_repo_prompt 含 repos/window + 端到端 items 落盘）**

追加到 `test_fetch_deepresearch.py`：

```python
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


def test_fetch_config_github_repo_tools_scope_gh_api():
    """② 走 gh CLI（非 github MCP——headless 不可用），且 Bash 限定 gh api:* 预授权。"""
    cfg = run_daily.FETCH_CONFIG["github-repo"]
    assert cfg["agent"] == "pa-fetch-github-repo"
    assert cfg["tools"] == ["Bash(gh api:*)"]            # 非 mcp__plugin_ecc_github__*
    assert cfg["mode"] == "items"
```

- [ ] **Step 3.2：跑测试**

Run: `cd Projects/项目推进流水线 && python3 -m pytest scripts/test_fetch_deepresearch.py -q`
Expected: 全绿（Task 1 的 github-repo 分发已就位，这批锁 prompt 形状 + 工具选择契约）。

- [ ] **Step 3.3：新建 persona `.claude/agents/pa-fetch-github-repo.md`**

```markdown
---
name: pa-fetch-github-repo
description: 项目推进流水线·GitHub 仓库监控采集（控制面 headless persona）。对一组 owner/repo，用 gh CLI（Bash）拉 window 内的 commits + pulls，逐仓 summarize 成 markdown digest，每仓一个 item 作为一行 JSON {items:[...]} 返回给编排器落盘。github MCP 在 headless 不可用（冒烟证实），故走 gh CLI。由编排器 scripts/run_daily.py 经 `claude --agent pa-fetch-github-repo -p --allowedTools "Bash(gh api:*)"` 链式调用。
tools: Bash
---

# pa-fetch-github-repo · GitHub 仓库监控（控制面，headless）

> For future Claude：你是「项目推进流水线」采集段的 **github-repo 源 fetcher**。编排器把「一组仓库（params.repos）+ 窗口（params.window，如 7d）」喂给你；你用 **gh CLI**（经 Bash）拉每仓近窗口活动，summarize 成 markdown digest，**每仓一个 item** 作为一行 JSON 返回。编排器落盘、radar 拾取——你不碰盘。

## 重要：用 gh CLI，不要用 github MCP

`mcp__plugin_ecc_github__*` **在 headless `claude -p` 不可用**（只在交互 session 注入）。冒烟已证：headless 工具集仅 `context7/exa/web_reader/4_5v_mcp`。**你必须用 Bash 跑 `gh` CLI**（已 auth，token 落 `~/.config/gh/hosts.yml`）。

## 你会收到什么（编排器 prompt 提供）

1. **仓库列表**：`params.repos`（`owner/repo` 形式，1~N 条）。
2. **窗口**：`params.window`（默认 `7d`）。

## 你做什么

逐仓：
1. **commits**：`gh api repos/OWNER/REPO/commits?per_page=30`（取最近 30 条，按 `window` 筛 `commit.author.date`）。
2. **pulls**：`gh api repos/OWNER/REPO/pulls?state=all&sort=updated&per_page=20`（近窗口内 updated/merged 的 PR）。
3. **summarize 成 md**：`# OWNER/REPO 近 {window}` + 「## Commits (N)」（msg / 日期 / 作者）+ 「## Pull Requests (M)」（标题 / 状态 / url）。重点突出**对本仓订阅项目有价值的变化**（breaking、release、重要 fix）。
4. **失败处理**：某仓 gh api 报错（私有/不存在/rate-limit）→ 该 item 仍返回，`markdown` 写明「⚠ <repo> 拉取失败：<错误>」，`commits_count=0`（编排器会落盘这篇「失败说明」，radar 可见，不静默吞）。

## 输出契约（硬性）

**只输出一行 JSON**：

```
{"items":[{"repo":"owner/repo","title":"<owner-repo 窗口摘要（ascii）>","markdown":"<digest md 全文>","commits_count":N,"prs_count":M}]}
```

- `items` 长度 = 输入 repo 数；`markdown` 换行用 `\n` 转义；title 尽量 ascii（`dev_slugify` 丢 CJK）。

## 硬约束

- **只调 `gh api`**：不要 `gh repo clone`、不要写盘、不要改仓。`--allowedTools "Bash(gh api:*)"` 只放了 gh api 子命令。
- **不编造数据**：commit/PR 必须来自 gh api 实返；拉不到如实标失败。

## 禁区

- 不写任何文件（落盘是编排器的活）。
- 不自行决定 `YYYYMMDD` 文件名。
- 不生成 PRD / candidates。
```

- [ ] **Step 3.4：验证 `Bash(gh api:*)` 限定范围真能消 permission_denials（关键去风险）**

冒烟里 plain `Bash` 跑 `gh api` 触发过 1 次 `permission_denials`。验证限定范围后是否 0 denial：

Run:
```bash
cd /mnt/disk01/workspaces/worksummary/vault
timeout 180 claude -p "Use Bash to run: gh api repos/pallets/flask/commits?per_page=1. Output exactly one line: TOOL_OK <commit message>." --allowedTools "Bash(gh api:*)" --output-format json --max-turns 4 2>&1 | tail -c 1500
```
Expected: `result` 含 `TOOL_OK ...`，且 JSON 里 `"permission_denials":[]`（空）。**若仍非空**：`Bash(gh api:*)` 限定语法在该 claude 版本不预授权 → 退回 `FETCH_CONFIG["github-repo"]["tools"]=["Bash"]`（plain，冒烟证可用，靠 MAX_TURNS=40 吸收偶发 denial-retry），并在 ADR 注明。

- [ ] **Step 3.5：提交**

```bash
git add .claude/agents/pa-fetch-github-repo.md Projects/项目推进流水线/scripts/test_fetch_deepresearch.py
# 若 Step 3.4 退回 plain Bash，连带 amend run_daily.py 的 FETCH_CONFIG：
# git add Projects/项目推进流水线/scripts/run_daily.py
git commit -m "feat(pa): ② pa-fetch-github-repo persona（gh CLI / Bash，Contract A）"
```

---

## Task 4：sources.yaml 接源 + ADR-0007 文档对齐

**Files:**
- Modify: `.project-auto/sources.yaml`（gitignored——本地配置，不入仓；但仍要改对）
- Modify: `Projects/项目推进流水线/docs/adr/0007-multi-source-radar.md`

把 ADR schema 里 wechat-url/github-repo 的**过时 `fetcher: scripts/fetchers/*.py` 字段去掉**（persona-based，同 quant-research），kind 表 ⏳→✅，follow-up ①② 标完成，补冒烟发现。

- [ ] **Step 4.1：sources.yaml 加两条源（替换 45-47 行的注释占位）**

把 sources.yaml 末尾的注释块（45-47 行）替换为真实源条目（**无 `fetcher:` 键**，persona-based）：

```yaml
  # wechat-url 源：fetcher = 专用 headless agent pa-fetch-wechat-url（web_reader + exa 兜底）。
  # 无 fetcher: 键 —— fetcher 是 agent，加 fetcher: 会触发 load_sources 的 missing-script warn。
  - name: wechat-picked
    kind: wechat-url
    root: Knowledge/微信精选
    content_glob: "**/[0-9]*.md"                # fetcher 产 YYYYMMDD_<slug>.md，radar 零分支拾取
    params:
      urls:
        - "https://mp.weixin.qq.com/s/EXAMPLE"  # 实际 URL 按需填
    target_projects: [ashare-llm-analyst]
    marker: state/consumed_wechat_picked

  # github-repo 源：fetcher = 专用 headless agent pa-fetch-github-repo（gh CLI / Bash）。
  # 无 fetcher: 键 —— 同理。github MCP 在 headless 不可用，故走 gh CLI（冒烟证实）。
  - name: github-watch
    kind: github-repo
    root: Knowledge/gh-watch
    content_glob: "**/[0-9]*.md"
    params:
      repos:
        - "akfamily/akshare"
      window: "7d"
    target_projects: [ashare-llm-analyst]
    marker: state/consumed_github_watch
```

- [ ] **Step 4.2：sources.yaml 加载校验**

Run: `cd Projects/项目推进流水线/scripts && python3 -c "import sys; sys.path.insert(0,'.'); import run_daily; srcs=run_daily.load_sources(); print([s['name'] for s in srcs])"`
Expected: 列表含 `wechat-picked` + `github-watch`，无「root 重复 / name 重复」硬错，无 missing-script warn。

- [ ] **Step 4.3：ADR-0007 schema 去 `fetcher:` 字段**

`docs/adr/0007-multi-source-radar.md:92-106` 的 schema 示例，把两条源的 `fetcher: scripts/fetchers/*.py` 行删掉、params 对齐（github 加 `window`）、补指向新计划。改为：

```markdown
  - name: wechat-picked                       # ② 指定微信文章（fetcher = pa-fetch-wechat-url agent，web_reader + exa 兜底）
    kind: wechat-url
    root: Knowledge/微信精选
    content_glob: "**/[0-9]*.md"
    params: { urls: ["https://mp.weixin.qq.com/s/…"] }
    target_projects: [ashare-llm-analyst]
    marker: state/consumed_wechat_picked

  - name: github-watch                        # ④ 指定 github 仓库（fetcher = pa-fetch-github-repo agent，gh CLI）
    kind: github-repo
    root: Knowledge/gh-watch
    content_glob: "**/[0-9]*.md"
    params: { repos: ["akfamily/akshare"], window: "7d" }
    target_projects: [ashare-llm-analyst]
    marker: state/consumed_github_watch
```

- [ ] **Step 4.4：ADR kind 表 ⏳→✅**

`docs/adr/0007-multi-source-radar.md:115-116` 两行改：

```markdown
| `wechat-url` | web_reader 抓指定 mp.weixin URL（exa 兜底）→ md，落 root | 同上 | ✅ follow-up ① |
| `github-repo` | gh CLI 拉 window 内 commits/pulls → md，落 root（github MCP headless 不可用） | 同上 | ✅ follow-up ② |
```

- [ ] **Step 4.5：ADR follow-up 行 + 补冒烟发现**

`docs/adr/0007-multi-source-radar.md:43` 改为：

```markdown
- **follow-up 待办**：① `wechat-url` fetcher ✅ 已实现（专用 headless agent `pa-fetch-wechat-url` + web_reader/exa；冒烟证 web_reader headless 可用）；② `github-repo` fetcher ✅ 已实现（专用 headless agent `pa-fetch-github-repo` + gh CLI/Bash——**冒烟发现 `mcp__plugin_ecc_github__*` 在 headless `claude -p` 不可用**，只在交互 session 注入，故 ② 走 gh CLI 而非 github MCP；计划见 `docs/plans/2026-07-19-pa-fetch-wechat-github.md`）；③ `agent-deepresearch` fetcher ✅ 已实现（专用 headless agent `pa-fetch-deepresearch` + ECC-MCP `deep-research` skill/exa 后端；计划见 `docs/plans/2026-07-19-pa-fetch-deepresearch.md`）。
```

- [ ] **Step 4.6：提交（ADR 入仓；sources.yaml 被 gitignore 不入）**

```bash
git add Projects/项目推进流水线/docs/adr/0007-multi-source-radar.md
git commit -m "docs(pa): ADR-0007 follow-up ①② 收尾——wechat-url/github-repo persona 化 + 冒烟发现"
```

---

## Task 5：全量测试 + 端到端冒烟（可选观测）

**Files:** 无新建（验证 only）

- [ ] **Step 5.1：fetch 段全量测试**

Run: `cd Projects/项目推进流水线 && python3 -m pytest scripts/test_fetch_deepresearch.py scripts/ -q`
Expected: 全绿（fetch 段 17 用例 + 其他既有用例无回归）。

- [ ] **Step 5.2：headless 端到端冒烟（① web_reader 真链路，观测用，非阻断）**

实跑 `pa-fetch-wechat-url` persona 抓一条真实微信 URL，验 persona→web_reader→items JSON→落盘全链路（成本约 $0.2-0.5，用户已示「cost 不作指标」）：

Run:
```bash
cd /mnt/disk01/workspaces/worksummary/vault
timeout 300 claude --agent pa-fetch-wechat-url -p "文章 URL（采集源 smoke）：
- https://mp.weixin.qq.com/s/EXAMPLE

严格按 persona 输出契约吐一行 JSON。" --allowedTools "mcp__web_reader__webReader,mcp__plugin_ecc_exa__web_fetch_exa" --output-format json --max-turns 8 2>&1 | tail -c 2000
```
Expected: `result` 是合法 `{"items":[...]}` JSON（外层 envelope `is_error:false`）。**若 EXAMPLE 是占位 URL**：换一条真实公开微信文章 URL 再跑；若 web_reader 被反爬挡、exa 兜底也失败 → item `fetched_via:"failed"`，**不阻断**（persona/fallback 架构已设计吸收，真实成功率是运行时经验问题）。

- [ ] **Step 5.3：headless 端到端冒烟（② gh CLI 真链路）**

实跑 `pa-fetch-github-repo` persona 监控一个真实仓：

Run:
```bash
cd /mnt/disk01/workspaces/worksummary/vault
timeout 300 claude --agent pa-fetch-github-repo -p "仓库（采集源 smoke）：
- pallets/flask
窗口：7d
严格按 persona 输出契约吐一行 JSON。" --allowedTools "Bash(gh api:*)" --output-format json --max-turns 10 2>&1 | tail -c 2000
```
Expected: `result` 是 `{"items":[{"repo":"pallets/flask",...,"commits_count":N,...}]}`，`permission_denials:[]`。若 Step 3.4 已退回 plain `Bash`，这里改 `--allowedTools "Bash"`。

- [ ] **Step 5.4：收尾汇报**

无提交。汇报：fetch 段测试数、①② 端到端冒烟结果（items JSON 合法性、web_reader 反爬表现、gh CLI denial 情况）、遗留（真实微信 URL 成功率待运行期观测）。

---

## 不做（YAGNI）

- **不改 `pa-fetch-deepresearch`** 的 single-mode 契约为 items（③ 已 ship，避免回归；deepresearch 是合成式 1 doc 语义，与 ①② 枚举式 N items 本就不同）。
- **不引入 github MCP headless 化**（配 PAT 入 config 违「无硬编码 secret」、且 gh CLI 已证可用，无必要）。
- **不做采集框架通用化**（ADR-0007 已拒；每 kind 仍各自薄 persona）。
- **不改 `discover_today_new` / `stage_radar` / `load_sources`**（消费侧零分支契约保持）。
- **不触发灰度**（sources.yaml 加源即生效，但不主动跑整条 cron 流水线——由用户/cron 触发）。

## 自检（writing-plans self-review）

- **Spec 覆盖**：①（web_reader+exa, Contract A）= Task 2；②（gh CLI, Contract A）= Task 3；分发骨架 = Task 1；schema/doc 对齐 = Task 4；验证 = Task 5。无遗漏。
- **占位符扫描**：无 TBD/TODO；所有代码步含完整代码；sources.yaml 的 `EXAMPLE` URL 是配置占位（用户按需填），非计划占位。
- **类型一致**：`FETCH_CONFIG` 条目四键 `agent/tools/prompt/mode` 全程一致；`_payload_to_items` 返回 `(title,md,raw)` 三元组，`stage_fetch` 解包一致；`produced` entry 字段（`source/title/path/cost/turns/sources_count?`）跨 mode 一致。
- **回归网**：③ 既有 11 用例（含 `sources_count` 断言）靠 `if "sources_count" in raw` 透传 + Step 1.4 全量跑保住；cron 副作用靠 Step 1.5/`/usr/bin/python3` 保住。
