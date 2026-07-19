# pa-fetch-deepresearch（agent-deepresearch 源 fetcher）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 ADR-0007 的 `kind: agent-deepresearch` 源装上 fetcher——一个 headless `pa-fetch-deepresearch` agent，用 ECC-MCP `deep-research` skill（exa 后端）做多源深研，产出带引用 markdown 落到 `source.root` 为 `YYYYMMDD_*.md`，被 radar 的 kind 无关 `discover_today_new` 拾取。

**Architecture:** fetcher 是 **agent 不是脚本**（与 pa-radar 同构，由 `run_daily.run_persona()` 经 `claude --agent pa-fetch-deepresearch -p` 链式调用）。agent 驱动 ECC-MCP 工作流（exa 搜 → 深读 → 合成带引用 md），把完整 markdown 作为一行 JSON payload 返回；**编排器（run_daily）负责落盘**（控制面 persona 不碰盘，与 pa-radar/pa-prd 一致，守 ADR-0001）。fetch 不碰 marker（radar 消费后才 bump，ADR-0007 #3）；`--from-stage fetch` 手动触发，默认 cron 不跑（manual-first）。

**Tech Stack:** Python 3（stdlib + PyYAML，cron `/usr/bin/python3` 友好，**顶层禁 import claude_agent_sdk**）；headless `claude -p`；ECC plugin exa MCP（`mcp__plugin_ecc_exa__web_search_exa` / `web_fetch_exa`）；pytest。

---

## Context（为什么这么做）

- **ADR-0007 follow-up ③**：`agent-deepresearch` fetcher 被决策 #7 推迟——schema 先就位、impl 后做。本计划即补这个 fetcher。
- **ADR 字面方案不可行**：placeholder 写 `fetcher: scripts/fetchers/deepresearch.py` + `params: { agent: general-purpose, skill: deep-research }`（嵌入 sdk 的 python 脚本）。拒——① cron `/usr/bin/python3` 无 sdk，脚本顶层 import sdk 会崩（与 ADR-0006 slug_utils 抽离同理）；② 与 pa persona 同构（headless agent）才是一致架构。**改为专用 `pa-fetch-deepresearch` agent。**
- **撞车已清理**（本会话 #1）：`deep-research` 唯一解析到 ① ECC-MCP（exa backend）；② 199-bio→`deep-research-rigorous`、③ awesome-llm-apps→`deep-research-basic`。pa-fetcher 锁定 ①。
- **冒烟已过**（本会话 #3）：ECC-MCP 工作流（exa 搜 → 合成带引用 md）产出的文件，被**真实** `run_daily.discover_today_new` 拾取（content_glob 命中 + 日期 > marker）——VERDICT PASS。故 agent persona 的端到端已证可行，本计划风险低。

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `scripts/run_daily.py` | 编排器：加 `--allowedTools` 透传 + `stage_fetch` + STAGES/TIMEOUT/MAX_TURNS 接 fetch + 索引位移 | Modify |
| `.claude/agents/pa-fetch-deepresearch.md` | fetcher headless persona（驱动 ECC-MCP 工作流，吐一行 JSON） | Create |
| `scripts/test_fetch_deepresearch.py` | run_persona allowedTools 透传 + stage_fetch 落盘/跳过/不碰 marker + STAGES 索引 | Create |
| `.project-auto/sources.yaml` | 加 `quant-research`（agent-deepresearch）源，去掉 bogus `fetcher:` | Modify |
| `docs/adr/0007-multi-source-radar.md` | follow-up ③ + schema 示例改正（fetcher 是 agent 非 .py） | Modify |

`run_daily.py` 行号锚点（基于当前 HEAD，实现时以 grep 再定位为准）：`run_persona` :293、`stage_radar` :440（镜像）、`STAGES` :81、`TIMEOUT` :78、`MAX_TURNS` :79、`_run_pipeline` 索引段 :1562-1576、`argparse` :1512-1513、`load_sources` :130、`read_marker`/`bump_marker` :166/:173、`dev_slugify` import :46。

> **Commit 策略**：vault 本地独立仓（never push），提交时机由用户定。本计划各 task 末尾给 commit 命令作 TDD 节奏参考，执行时可按用户偏好批量提交。

---

## Task 1: `run_persona` 透传 `--allowedTools` + headless stdin 兜底

**Files:**
- Modify: `scripts/run_daily.py:293-305`（`run_persona` 签名 + `base_cmd` + `subprocess.run`）
- Test: `scripts/test_fetch_deepresearch.py`

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_fetch_deepresearch.py
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
    monkeypatch.setattr(run_daily.subprocess, "run", lambda cmd, **kw: (captured.__setitem__("cmd", cmd), _P())[1])
    run_daily.run_persona("pa-x", "hi", "radar", "t1")
    assert "--allowedTools" not in captured["cmd"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /mnt/disk01/workspaces/worksummary/vault/Projects/项目推进流水线/scripts
python3 -m pytest test_fetch_deepresearch.py::test_run_persona_allowed_tools_appended -q
```
Expected: FAIL — `run_persona() got an unexpected keyword argument 'allowed_tools'`（TypeError）。

- [ ] **Step 3: Write minimal implementation**

`run_daily.py:293` 签名 + `base_cmd`（追加 `allowed_tools` 形参，非 None 时拼 `--allowedTools`）：

```python
def run_persona(name: str, prompt: str, stage: str, label: str,
                allowed_tools: list[str] | None = None) -> tuple[dict, dict]:
    """调 `claude --agent <name> -p <prompt> --output-format json`，两层解析返回 (payload, meta)。

    内层 result 容错：先严格 json.loads，失败则 _extract_first_json 抽取（容忍散文前后缀）；
    仍失败重试 1 次（拼 _JSON_RETRY_SUFFIX 加强 JSON-only 契约）。两轮均失败才 raise。
    allowed_tools：MCP 工具白名单透传（fetch 段调 exa 必须，--allowedTools 逗号分隔）。"""
    base_cmd = [resolve_claude_bin(), "--agent", name, "--output-format", "json",
                "--max-turns", str(MAX_TURNS[stage])]
    if allowed_tools:
        base_cmd += ["--allowedTools", ",".join(allowed_tools)]
```

`run_daily.py:305` `subprocess.run` 加 `stdin=subprocess.DEVNULL`（headless/cron 兜底——MCP 工具调用比纯 Read persona 新，防御性；现有 persona 不读 stdin，零副作用）：

```python
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=TIMEOUT[stage], stdin=subprocess.DEVNULL)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest test_fetch_deepresearch.py::test_run_persona_allowed_tools_appended \
                 test_fetch_deepresearch.py::test_run_persona_no_allowed_tools_omits_flag -q
```
Expected: PASS（2 passed）。

- [ ] **Step 5: 回归——既有 persona 调用未被破坏**

```bash
python3 -m pytest -q
```
Expected: 全绿（allowed_tools 缺省 None → 行为同前；stdin=DEVNULL 对既有 radar/prd/critic 调用无影响）。

- [ ] **Step 6: Commit**

```bash
git add scripts/run_daily.py scripts/test_fetch_deepresearch.py
git commit -m "feat(run_daily): run_persona 透传 --allowedTools + stdin DEVNULL 兜底"
```

---

## Task 2: `pa-fetch-deepresearch` agent persona

**Files:**
- Create: `.claude/agents/pa-fetch-deepresearch.md`（frontmatter `name`/`description`/`tools` + persona body，镜像 `pa-radar.md`）
- Test: 结构校验（frontmatter 解析 + headless 烟测返回 JSON）

- [ ] **Step 1: Write the persona file**

```markdown
---
name: pa-fetch-deepresearch
description: 项目推进流水线·深研采集（控制面 headless persona）。对一个研究主题做多源深研（exa 搜 → 深读关键源 → 合成带引用 markdown），把完整报告作为一行 JSON 返回给编排器落盘。由编排器 scripts/run_daily.py 经 `claude --agent pa-fetch-deepresearch -p --allowedTools ...` 链式调用。
tools: mcp__plugin_ecc_exa__web_search_exa, mcp__plugin_ecc_exa__web_fetch_exa
---

# pa-fetch-deepresearch · 深研采集（控制面，headless）

> For future Claude：你是「项目推进流水线」采集段的 **agent-deepresearch 源 fetcher**。编排器把「研究主题（该源的 params.prompts）」喂给你；你驱动 ECC-MCP `deep-research` skill 工作流（exa 多源搜 → 深读 → 合成），产出**一份带引用的 markdown 报告**，整篇作为 JSON 字段返回。编排器负责落盘到 `source.root/YYYYMMDD_*.md`、radar 负责拾取——你不碰盘。

## 你会收到什么（编排器 prompt 提供）

1. **研究主题**：该源的 `params.prompts`（1~N 条；通常是对订阅项目有价值的技术/领域方向，如「A股 量化 大模型 最新进展」）。

## 你做什么（ECC-MCP `deep-research` 工作流）

1. **拆子问题**：把主题拆成 3-5 个研究子问题。
2. **多源搜**：每个子问题用 `mcp__plugin_ecc_exa__web_search_exa(query, numResults=8)` 搜；每个子问题 2-3 个关键词变体；优先学术/官方/权威新闻 > 博客 > 论坛；目标 15-30 条独立源；偏好近 12 个月。
3. **深读关键源**：最有 promise 的 3-5 个 URL 用 `mcp__plugin_ecc_exa__web_fetch_exa(urls=[...])` 取全文，不只依赖摘要。
4. **合成带引用 md**：按下述结构成文，**每条论断带内联引用** `[N]`，末尾 `## Sources` 列全量来源（标题 + URL + 一句话摘要）。

## 输出契约（硬性）

**只输出一行 JSON**，无多余文字、无 markdown 代码块包裹、无解释。结构：

```
{"title":"<报告标题（ascii 优先，便于 slug）>","markdown":"<完整带引用 md 全文>","sources_count":<int>,"confidence":"High|Medium|Low"}
```

- `markdown` 是**完整**报告正文（含 `# 标题` / `*Generated|Sources|Confidence*` 头 / `## Executive Summary` / 3 个 `## 主题`（带 [N] 引用）/ `## Key Takeaways` / `## Sources` / `## Gaps`）。
- `title` 用于编排器生成文件名 slug（`dev_slugify`：非 `[a-z0-9]` 全压成 `-`，CJK 会被丢——故 title 尽量含 ascii 词，如 `Ashare LLM Quant 2026-07`）。
- `sources_count` = `## Sources` 条数；`confidence` 反映证据强度。

## 硬约束

- **每条论断必有源**：无源断言一律删；只有单一来源的标「未验证」。
- **严禁编造**：引用必须来自 exa 实搜结果；找不到就说「insufficient data」，写进 `## Gaps`。
- **事 实 vs 推断 分离**：估计/预测/观点明确标注。
- **只吐那一行 JSON**：`markdown` 字段内的换行用 `\n` 转义；多一个字算失败（headless 结构化输出硬要求）。

## 禁区

- 不写任何文件（落盘是编排器的活，ADR-0001）。
- 不自行决定 `YYYYMMDD` 文件名（编排器按采集日盖戳）。
- 不生成 PRD / candidates（那是 pa-prd / pa-radar 的活）。
```

- [ ] **Step 2: 结构校验——frontmatter 可解析**

```bash
cd /mnt/disk01/workspaces/worksummary/vault
python3 -c "
import re
md = open('.claude/agents/pa-fetch-deepresearch.md', encoding='utf-8').read()
fm = md.split('---', 2)[1]
assert 'name: pa-fetch-deepresearch' in fm
assert 'mcp__plugin_ecc_exa__web_search_exa' in fm
assert 'mcp__plugin_ecc_exa__web_fetch_exa' in fm
print('frontmatter OK')
"
```
Expected: `frontmatter OK`。

- [ ] **Step 3: Headless 烟测——agent 真返回一行 JSON（含 markdown 字段）**

> 这是 persona 的集成验证（替代单元测试——prompt 质量无法单测）。用极小主题 + max-turns 限成本。

```bash
cd /mnt/disk01/workspaces/worksummary/vault
claude --agent pa-fetch-deepresearch --output-format json --max-turns 12 \
  --allowedTools mcp__plugin_ecc_exa__web_search_exa,mcp__plugin_ecc_exa__web_fetch_exa \
  -p '研究主题：baostock A股 数据接口 最新动态（2026）。按输出契约只吐一行 JSON。' \
  < /dev/null | python3 -c "
import sys, json
env = json.loads(sys.stdin.read())
payload = json.loads(env['result'])
assert 'markdown' in payload and len(payload['markdown']) > 200
assert 'sources_count' in payload
print('smoke OK: title=', payload.get('title'), 'sources=', payload.get('sources_count'))
"
```
Expected: `smoke OK: title= ... sources= N`（markdown 非空、有 sources_count）。若 exa 未配/超时 → 见 Task 6 兜底说明。

- [ ] **Step 4: Commit**

```bash
git add .claude/agents/pa-fetch-deepresearch.md
git commit -m "feat(agents): pa-fetch-deepresearch headless 深研采集 persona"
```

---

## Task 3: `stage_fetch`——调 agent、落盘 `YYYYMMDD_*.md`、不碰 marker

**Files:**
- Modify: `scripts/run_daily.py`（新增 `FETCH_AGENT`/`FETCH_ALLOWED_TOOLS` 常量 + `fetch_prompt` + `stage_fetch`）
- Test: `scripts/test_fetch_deepresearch.py`

- [ ] **Step 1: Write the failing tests**（追加到 `test_fetch_deepresearch.py`）

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest test_fetch_deepresearch.py::test_stage_fetch_writes_md_with_stamp_slug -q
```
Expected: FAIL — `AttributeError: module 'run_daily' has no attribute 'stage_fetch'`。

- [ ] **Step 3: Write minimal implementation**

在 `run_daily.py` `stage_radar` 之前（约 :439 `# ─── 三段执行` 段首）插入：

```python
# ─── fetch 段（agent-deepresearch 源：调 pa-fetch-deepresearch 深研 → 落 YYYYMMDD_*.md）────
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


def stage_fetch(args, sources, stamp) -> dict:
    """agent-deepresearch 源 fetcher：调 pa-fetch-deepresearch agent 深研 → 落 YYYYMMDD_<slug>.md 到 source.root。

    其他 kind 跳过（directory/local-file 无 fetcher；wechat-url/github-repo 后续 follow-up ①②）。
    fetch 不碰 marker（radar 消费后才 bump，ADR-0007 #3）；--dry-run 不影响 fetch（写文件是 fetch 的全部意义）。
    stamp = 采集日（编排器传入的 YYYYMMDD），满足「文件名 = 采集戳」契约。"""
    produced = []
    for src in sources:
        if src.get("kind") != "agent-deepresearch":
            continue
        root = VAULT_ROOT / src["root"]
        root.mkdir(parents=True, exist_ok=True)
        payload, meta = run_persona(FETCH_AGENT, fetch_prompt(src), "fetch",
                                    f"fetch-{src['name']}", allowed_tools=FETCH_ALLOWED_TOOLS)
        md = (payload.get("markdown") or "").strip()
        if not md:
            log(f"[fetch] ⚠ {src['name']} agent 未返回 markdown（跳过落盘）")
            continue
        title = payload.get("title") or src["name"]
        slug = dev_slugify(title) or src["name"]            # 复用 ADR-0006 单一源头
        out = root / f"{stamp}_{slug}.md"
        out.write_text(md, encoding="utf-8")
        produced.append({"source": src["name"],
                         "path": str(out.relative_to(VAULT_ROOT)),
                         "sources_count": payload.get("sources_count"),
                         "cost": meta["cost"], "turns": meta["turns"]})
        log(f"[fetch] ✅ {src['name']} → {out.relative_to(VAULT_ROOT)}｜"
            f"sources={payload.get('sources_count')} cost=${meta['cost']:.4f} turns={meta['turns']}")
    out_json = {"produced": produced, "stamp": stamp}
    (STATE_DIR / f"fetch_{stamp}.json").write_text(
        json.dumps(out_json, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_json
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest test_fetch_deepresearch.py -q
```
Expected: PASS（Task 1 两个 + Task 3 四个 = 6 passed）。

- [ ] **Step 5: Commit**

```bash
git add scripts/run_daily.py scripts/test_fetch_deepresearch.py
git commit -m "feat(run_daily): stage_fetch 调 pa-fetch-deepresearch 落盘 YYYYMMDD_*.md"
```

---

## Task 4: 接入 STAGES / TIMEOUT / MAX_TURNS / `_run_pipeline` 索引位移

**Files:**
- Modify: `scripts/run_daily.py:78-81`（三个常量）+ `:1562-1576`（`_run_pipeline` 索引段）
- Test: `scripts/test_fetch_deepresearch.py`

- [ ] **Step 1: Write the failing tests**（追加）

```python
def test_stages_has_fetch_at_zero():
    import importlib
    importlib.reload(run_daily)
    assert run_daily.STAGES[0] == "fetch"
    assert run_daily.STAGES[1] == "radar"   # 原 radar 顺位后移


def test_fetch_timeout_and_maxturns_defined():
    assert "fetch" in run_daily.TIMEOUT and run_daily.TIMEOUT["fetch"] > run_daily.TIMEOUT["radar"]
    assert "fetch" in run_daily.MAX_TURNS and run_daily.MAX_TURNS["fetch"] >= 20
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest test_fetch_deepresearch.py::test_stages_has_fetch_at_zero -q
```
Expected: FAIL — `STAGES[0] == "radar"`（fetch 未插入）。

- [ ] **Step 3: Write minimal implementation**

`run_daily.py:78-81`：

```python
TIMEOUT = {"fetch": 1500, "radar": 600, "prd": 900, "critic": 300, "verify": 300}
MAX_TURNS = {"fetch": 40, "radar": 60, "prd": 90, "critic": 40, "verify": 30}

STAGES = ["fetch", "radar", "prd", "inject", "critic", "dispatch", "report"]
```

`_run_pipeline` 索引段（原 :1562-1576）——fetch 插在 index 0，其后所有 index +1（inject 特例 2→3）：

```python
    try:
        if lo <= 0 <= hi:
            stage_fetch(args, sources, args.stamp)
        if lo <= 1 <= hi:
            candidates_payload = stage_radar(args, sources, profiles, args.stamp)
        if lo <= 2 <= hi:
            manifest = stage_prd(args, candidates_payload, profiles, args.stamp)
        if lo <= 3 <= hi and getattr(args, "inject_prd", None):
            manifest, stamp = stage_inject(args, profiles, stamp)   # 手动注入 PRD（替 radar→prd）
        if lo <= 4 <= hi:
            gate = stage_critic(args, manifest, profiles, stamp)
        if lo <= 5 <= hi:
            if not gate:   # --from-stage dispatch：critic 未跑，从盘读 prd_gate
                gf = STATE_DIR / f"prd_gate_{stamp}.json"
                gate = json.loads(gf.read_text(encoding="utf-8")) if gf.is_file() else []
            dispatch = stage_dispatch(args, gate, profiles, stamp)
        if lo <= 6 <= hi:
            stage_report(args, profiles, stamp)
```

> `argparse` 默认 `--from-stage radar`（:1512 不改）→ 默认 cron 跑 lo=1，`0 <= 1` 范围不含 fetch → **cron 不自动 fetch（manual-first）**。手动触发：`--from-stage fetch`（跑 fetch→后续全段）或 `--from-stage fetch --to-stage fetch`（只 fetch）。

- [ ] **Step 4: Run tests + 全量回归**

```bash
python3 -m pytest -q
```
Expected: PASS（含 Task 4 两个 + 既有 test_multi_source_radar / test_inject / test_verify_loop / test_dev_agent_source / test_bash_allowlist 全绿；inject 索引 2→3 已同步）。

- [ ] **Step 5: 验证 argparse `--from-stage fetch` 被 STAGES choices 接受**

```bash
python3 run_daily.py --from-stage fetch --to-stage fetch --dry-run --help 2>&1 | head -3
# 或直接探测 choices：
python3 -c "import sys; sys.path.insert(0,'.'); import run_daily as r; 
import argparse; ap=argparse.ArgumentParser(); ap.add_argument('--from-stage', choices=r.STAGES);
print('fetch' in ap.parse_args(['--from-stage','fetch']).__dict__['from_stage'])"
```
Expected: choices 含 `fetch`（因 STAGES 已含），输出 `True`。

- [ ] **Step 6: Commit**

```bash
git add scripts/run_daily.py scripts/test_fetch_deepresearch.py
git commit -m "feat(run_daily): fetch 接入 STAGES（index 0）+ TIMEOUT/MAX_TURNS + 索引位移"
```

---

## Task 5: ADR-0007 schema + follow-up ③ 改正

**Files:**
- Modify: `docs/adr/0007-multi-source-radar.md`（schema 示例 :84-90 + follow-up ③ :43）

- [ ] **Step 1: 改 schema 示例**（`fetcher: .py` → 删；`params` 去掉 `agent`/`skill`，留 `prompts`）

原（:84-90）：
```yaml
  - name: quant-research                      # ① agent + deep-research 搜索
    kind: agent-deepresearch
    root: Knowledge/深研/quant
    fetcher: scripts/fetchers/deepresearch.py
    params: { agent: general-purpose, skill: deep-research, prompts: ["A股量化最新进展…"] }
    target_projects: [ashare-llm-analyst]
    marker: state/consumed_quant_research
```
改为：
```yaml
  - name: quant-research                      # ① agent + deep-research 深研（fetcher = pa-fetch-deepresearch agent）
    kind: agent-deepresearch                  # fetcher 是专用 headless agent（非 .py 脚本）：见 docs/plans/2026-07-19-pa-fetch-deepresearch.md
    root: Knowledge/深研/quant
    params: { prompts: ["A股量化最新进展…"] }   # 研究 topic；agent=ECC-MCP deep-research（exa 后端）内定，不需声明
    target_projects: [ashare-llm-analyst]
    marker: state/consumed_quant_research
```

> 理由：① fetcher 是 agent（本会话定，冒烟已证），非嵌入 sdk 的 .py（cron 无 sdk 会崩，同 ADR-0006 slug_utils 理由）；② 删 `fetcher:` 键 → `load_sources` 的「fetcher 脚本不存在」warn 不再误报（agent-deepresearch 的 fetcher 是 agent，无 .py）；③ `agent`/`skill` 内定（单一源头 = `pa-fetch-deepresearch` + ECC-MCP `deep-research`，撞车已清理为唯一解析），不需 per-source 声明。

- [ ] **Step 2: 改 follow-up ③**（:43）

原：`③ \`agent-deepresearch\` fetcher（调 agent + deep-research skill）。`
改为：`③ \`agent-deepresearch\` fetcher ✅ 已实现（专用 headless agent \`pa-fetch-deepresearch\` + ECC-MCP \`deep-research\` skill/exa 后端；计划见 \`docs/plans/2026-07-19-pa-fetch-deepresearch.md\`）。`

- [ ] **Step 3: Commit**

```bash
git add docs/adr/0007-multi-source-radar.md
git commit -m "docs(adr-0007): agent-deepresearch fetcher 改 agent 架构 + 标记 follow-up ③ 已实现"
```

---

## Task 6: sources.yaml 加源 + 端到端手动验证

**Files:**
- Modify: `.project-auto/sources.yaml`（加 `quant-research` 源）

- [ ] **Step 1: 读现状 sources.yaml，加源**

```bash
cat /mnt/disk01/workspaces/worksummary/vault/.project-auto/sources.yaml
```
确认既有 wechat / drop-zone-* 源结构后，追加（root/target_projects/marker 与 ADR 示例一致）：

```yaml
  - name: quant-research
    kind: agent-deepresearch
    root: Knowledge/深研/quant
    params:
      prompts:
        - "A股 量化投资 大模型 LLM 应用 2026 最新进展"
        - "大模型 金融研报生成 / 投资决策 智能体 2026"
    target_projects: [ashare-llm-analyst]
    marker: state/consumed_quant_research
```

- [ ] **Step 2: 验证 load_sources 不误报**

```bash
cd /mnt/disk01/workspaces/worksummary/vault/Projects/项目推进流水线/scripts
python3 -c "import sys; sys.path.insert(0,'.'); import run_daily as r; 
srcs=r.load_sources(); print([s['name'] for s in srcs]); 
qs=[s for s in srcs if s.get('kind')=='agent-deepresearch']; print('agent-deepresearch:', [s['name'] for s in qs])"
```
Expected: sources 含 `quant-research`；**无** `⚠ [sources] quant-research 声明 fetcher=... 但脚本不存在` warn（因已删 `fetcher:` 键）。

- [ ] **Step 3: 端到端——只跑 fetch，产出 md**

```bash
cd /mnt/disk01/workspaces/worksummary/vault/Projects/项目推进流水线/scripts
python3 run_daily.py --from-stage fetch --to-stage fetch --stamp 20260719
ls -la /mnt/disk01/workspaces/worksummary/vault/Knowledge/深研/quant/
cat /mnt/disk01/workspaces/worksummary/vault/.project-auto/state/fetch_20260719.json
```
Expected: `Knowledge/深研/quant/20260719_*.md` 生成（带引用 md）；`fetch_20260719.json` 记 produced。

> **成本提示**：每次 `--from-stage fetch` 触发真实 exa 调用（约 $0.1~0.3/次）。手动触发，非 cron。

- [ ] **Step 4: 端到端——radar 拾取（消费侧零分支验证）**

```bash
# 假设 consumed_quant_research marker < 20260719（初跑默认 00000000）
python3 run_daily.py --from-stage radar --to-stage radar --stamp 20260719 --limit 5 2>&1 | grep -E "\[radar\]|quant"
```
Expected: `[radar] source=quant-research kind=agent-deepresearch ... 今日新=1`（radar 用同一 `discover_today_new` 拾取 fetch 产物，**kind 零分支**）；`candidates_20260719.json` 的 `per_source` 含 `quant-research: 1`。

> 若 exa 在该环境未配（Task 2 Step 3 已冒烟过则已配）→ fetch 段会 `RuntimeError`（agent 退出非 0）。兜底：Task 3 的「empty markdown 跳过」只覆盖 agent 返回空，不覆盖 agent 崩；agent 崩属 run_persona 既有 raise 语义（与 radar 同），`--from-stage fetch` 单独跑时可见错、不污染 radar。可接受。

- [ ] **Step 5: Commit**

```bash
git add .project-auto/sources.yaml
git commit -m "feat(sources): 加 quant-research agent-deepresearch 源（pa-fetch-deepresearch 喂 ashare）"
```

> `.project-auto/` 是否 gitignore？执行前 `git check-ignore .project-auto/sources.yaml` 确认；若被忽略则不 commit（仅本地生效），符合「state 类产物不入库」惯例。

---

## Self-Review

**1. Spec coverage（对 ADR-0007 follow-up ③ + 本会话决定）：**
- 「fetcher 是 agent 非 .py」→ Task 2（persona）+ Task 5（ADR 改正）+ Task 1（allowedTools 透传）。✓
- 「ECC-MCP deep-research skill / exa 后端」→ Task 2 persona tools 锁 exa；撞车清理（#1 已做）保证 `deep-research`=① ECC-MCP。✓
- 「产 YYYYMMDD_*.md 到 source.root」→ Task 3 `stage_fetch` 落盘（stamp=s采集日，slug=dev_slugify(title)）。✓
- 「radar kind 无关拾取」→ Task 6 Step 4 端到端验证（`discover_today_new` 零改动，已冒烟）。✓
- 「manual-first，cron 不自动」→ Task 4（fetch 在 STAGES[0] 但 default from_stage=radar）。✓
- 「--allowedTools + stdin /dev/null」→ Task 1。✓

**2. Placeholder scan：** 无 TBD/TODO；所有代码块完整；行号锚点 + grep 再定位说明齐全；测试 AAA + 具体断言。✓

**3. Type/签名一致性：** `run_persona(name,prompt,stage,label,allowed_tools=None)` — Task 1 定义、Task 3 调用（传 `allowed_tools=FETCH_ALLOWED_TOOLS`）一致；`stage_fetch(args,sources,stamp)` — Task 3 定义、Task 4 `_run_pipeline` 调用 `stage_fetch(args, sources, args.stamp)` 一致；`dev_slugify` 已 import（:46）Task 3 直接用。✓

**4. 风险/边界：**
- 索引位移（inject 2→3 等）—— Task 4 Step 4 全量回归覆盖（test_inject / test_verify_loop 会捕获漏改）。✓
- agent 崩（exa 未配）—— 明确为 run_persona 既有 raise 语义，`--from-stage fetch` 隔离跑可见错。已述。✓
- 成本 —— manual-first，非 cron；Task 6 Step 3 标注。✓
- 199-bio ② 的 `open`/HTML/PDF 噪音 —— 不用 ②（用 ① ECC-MCP prompt-only），规避。✓

## 执行选择（writing-plans 交接）

计划已存 `Projects/项目推进流水线/docs/plans/2026-07-19-pa-fetch-deepresearch.md`。两种执行方式：

1. **Subagent-Driven（推荐）** — 每 task 派新 subagent + 两段 review（spec → quality），快迭代。
2. **Inline Execution** — 本会话内 executing-plans 批量执行 + 检查点。

选哪种？（或先 review 计划本身，有调整先改。）
