# radar 多源消费接口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `stage_radar` 从单源硬编码（`sources[0]`、混喂所有项目）改为多源遍历 + `target_projects` 白名单按项目路由，让非 AI-coding 项目（ashare）能收到对它有意义的源；本次只交付消费接口（`directory` + `local-file` 两种 kind 跑通，另 3 种 kind schema 先就位、fetcher 各自 follow-up）。

**Architecture:** 采集层与消费层彻底解耦——`sources.yaml` 每条源加 `kind` + `target_projects`；`stage_radar` 遍历全部源各自 `discover_today_new`，按 `target_projects` 把文件聚合到 `project → [它订阅的所有源的文件并集]`，只有订阅到新文件的项目才调 `pa-radar`（per-project 签名）；消费侧完全不读 `kind`（零分支），fetcher 未就绪的源静默（root 空 = 0 新内容）。设计依据：`docs/adr/0007-multi-source-radar.md`。

**Tech Stack:** Python 3 / PyYAML / pytest（`/usr/bin/python3` 可跑，零第三方以外的依赖；cron 兼容）。

**关键约束（沿用既有规约）:**
- `run_daily.py` 顶部 import 不得连带加载 `claude_agent_sdk`（cron 的 `/usr/bin/python3` 无 sdk 会崩——`test_run_daily_import_does_not_load_claude_sdk` 回归网）。本次只动 `load_sources`/`radar_prompt`/`stage_radar`/新增 `_source_of`，不碰 sdk。
- marker 失败不 bump（原 `stage_radar` 在 `run_persona` 成功后才 bump；本次保持：所有 per-project radar 成功后才 bump，任一抛错则不 bump → 下次重发现）。
- vault 独立本地仓（never push），提交时机/分支由用户定；本计划不自行 commit 到 main。

**File Structure:**

| 文件 | 责任 | 动作 |
|---|---|---|
| `scripts/run_daily.py` | 编排器 | Modify `load_sources`(130-133)、`radar_prompt`(311-331)、`stage_radar`(408-437)；Add `_source_of` |
| `scripts/test_multi_source_radar.py` | 多源消费接口单测（TDD） | Create |
| `.project-auto/sources.yaml` | 采集源配置 | Modify（wechat 加 `kind`+`target_projects`；加 `drop-zone` 源） |
| `Knowledge/投递箱/` | ashare 手动投递落点 | Create 目录 + 说明 |

---

## Task 1: `load_sources` 校验（name 唯一 + root 排他 + kind 缺省 + fetcher warn）

**Files:**
- Modify: `scripts/run_daily.py:130-133`（`load_sources`）
- Test: `scripts/test_multi_source_radar.py`（新建）

- [ ] **Step 1: 写失败测试（新建测试文件）**

新建 `scripts/test_multi_source_radar.py`，文件头沿用 `test_dev_agent_source.py` 的 import 模式：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_multi_source_radar.py — ADR-0007 多源 radar 消费接口单测（TDD）。

覆盖：
    - load_sources 校验（name 唯一 / root 排他 / kind 缺省 / fetcher warn 不阻断）
    - _source_of 候选源追溯
    - radar_prompt per-project 签名
    - stage_radar 多源遍历 + target_projects 路由 + 无订阅不调 + 失败不 bump + candidate 带 source

跑：python3 -m pytest scripts/test_multi_source_radar.py -q
AAA 结构（Arrange / Act / Assert）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402


# ─── load_sources 校验（ADR-0007 决定 #2/#4）──────────────────────────
def _write_sources(tmp_path, body: str, monkeypatch) -> Path:
    sf = tmp_path / "sources.yaml"
    sf.write_text(body, encoding="utf-8")
    monkeypatch.setattr(run_daily, "SOURCES_FILE", sf)
    monkeypatch.setattr(run_daily, "PROJECT_DIR", tmp_path)   # fetcher 路径解析兜底
    return sf


def test_load_sources_rejects_duplicate_name(tmp_path, monkeypatch):
    # Arrange：两条源同名
    _write_sources(tmp_path, "sources:\n  - {name: a, root: r1}\n  - {name: a, root: r2}\n", monkeypatch)
    # Act / Assert：name 重复 → 拒载退出
    with pytest.raises(SystemExit):
        run_daily.load_sources()


def test_load_sources_rejects_shared_root(tmp_path, monkeypatch):
    # Arrange：两条源共用 root
    _write_sources(tmp_path, "sources:\n  - {name: a, root: same}\n  - {name: b, root: same}\n", monkeypatch)
    # Act / Assert：root 排他 → 拒载退出
    with pytest.raises(SystemExit):
        run_daily.load_sources()


def test_load_sources_defaults_kind_directory(tmp_path, monkeypatch):
    # Arrange：源未写 kind
    _write_sources(tmp_path, "sources:\n  - {name: a, root: r1}\n", monkeypatch)
    # Act / Assert：缺省 kind=directory（消费侧零分支）
    out = run_daily.load_sources()
    assert out[0]["kind"] == "directory"


def test_load_sources_warns_missing_fetcher_but_keeps_source(tmp_path, monkeypatch, capsys):
    # Arrange：声明 fetcher 指向不存在的脚本（本次未实现的 kind）
    _write_sources(
        tmp_path,
        'sources:\n  - {name: deep, root: r1, kind: agent-deepresearch,'
        '  fetcher: "scripts/fetchers/x.py"}\n',
        monkeypatch,
    )
    # Act：不抛（消费侧只看目录；fetcher 未就绪静默）
    out = run_daily.load_sources()
    # Assert：源保留 + 打了 warn
    assert out[0]["name"] == "deep"
    captured = capsys.readouterr()
    assert "fetcher" in captured.out and "不存在" in captured.out
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /mnt/disk01/workspaces/worksummary/vault/Projects/项目推进流水线 && python3 -m pytest scripts/test_multi_source_radar.py -q`
Expected: FAIL — `test_load_sources_defaults_kind_directory` 断言 `kind == "directory"` 失败（现状无 setdefault）；`test_load_sources_rejects_*` 不抛（现状无校验）；`test_load_sources_warns_missing_fetcher_but_keeps_source` 断言 warn 文案缺失。

- [ ] **Step 3: 实现 `load_sources` 校验**

替换 `scripts/run_daily.py:130-133` 的 `load_sources`：

```python
def load_sources() -> list[dict]:
    with open(SOURCES_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    sources = data["sources"]
    # 采集源身份（ADR-0007 决定 #4）：name 唯一 + root 排他（一个 root 只属一个源）。
    # 杜绝双扫 / marker 互相污染 / candidate 重复。重复即拒载（硬错，不静默）。
    seen_names: set[str] = set()
    seen_roots: set[str] = set()
    for src in sources:
        name = src.get("name")
        root = src.get("root")
        if name in seen_names:
            sys.exit(f"✗ sources.yaml 采集源 name 重复：{name}（name 必须唯一）")
        seen_names.add(name)
        if root in seen_roots:
            sys.exit(f"✗ sources.yaml root 被多源共用：{root}（一个 root 只属一个采集源）")
        seen_roots.add(root)
        src.setdefault("kind", "directory")   # 缺省 directory（消费侧零分支，决定 #2）
        # fetcher 声明了但脚本不存在 → warn 不阻断（消费侧只看目录；未实现 kind 的源今日 0 产出、订阅项目不调 radar）
        fetcher = src.get("fetcher")
        if fetcher and not (PROJECT_DIR / fetcher).is_file():
            log(f"⚠ [sources] {name} 声明 fetcher={fetcher} 但脚本不存在（未实现？）——"
                f"该源今日无产出、其订阅项目不调 radar（静默，不阻断）")
    return sources
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /mnt/disk01/workspaces/worksummary/vault/Projects/项目推进流水线 && python3 -m pytest scripts/test_multi_source_radar.py -q`
Expected: PASS（4 条 load_sources 用例全绿）。

- [ ] **Step 5: Commit**

```bash
git add scripts/run_daily.py scripts/test_multi_source_radar.py
git commit -m "feat(pa-radar): load_sources 校验 name 唯一/root 排他 + kind 缺省 + fetcher warn

ADR-0007 决定 #2/#4。采集源身份确立：name 唯一 key、root 排他（重复拒载）。
缺省 kind=directory（消费侧零分支）；fetcher 未就绪 warn 不阻断。"
```

---

## Task 2: `_source_of` 候选源追溯（pure helper）

**Files:**
- Modify: `scripts/run_daily.py`（在 `radar_prompt` 前、`fetch_dedup_list` 后新增 `_source_of`，约 218 行后）
- Test: `scripts/test_multi_source_radar.py`（追加）

- [ ] **Step 1: 追加失败测试**

在 `scripts/test_multi_source_radar.py` 末尾追加：

```python
# ─── _source_of 候选源追溯（ADR-0007 决定 #6）────────────────────────
def test_source_of_traces_candidate_to_source(tmp_path, monkeypatch):
    # Arrange：VAULT_ROOT 置 tmp，源文件在 vault 相对路径下
    monkeypatch.setattr(run_daily, "VAULT_ROOT", tmp_path)
    root = tmp_path / "Knowledge/微信"
    root.mkdir(parents=True)
    f = root / "20260719_x.md"
    f.write_text("#", encoding="utf-8")
    src_files = [("wechat", [f]), ("drop-zone", [])]
    cand = {"source_path": "Knowledge/微信/20260719_x.md"}   # persona 吐的 vault 相对路径
    # Act / Assert：回溯到 wechat
    assert run_daily._source_of(cand, src_files) == "wechat"


def test_source_of_unknown_when_no_match(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "VAULT_ROOT", tmp_path)
    root = tmp_path / "Knowledge/微信"
    root.mkdir(parents=True)
    f = root / "20260719_x.md"
    f.write_text("#", encoding="utf-8")
    src_files = [("wechat", [f])]
    cand = {"source_path": "Knowledge/别处/missing.md"}      # 命中不到
    assert run_daily._source_of(cand, src_files) == "unknown"


def test_source_of_unknown_when_source_path_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "VAULT_ROOT", tmp_path)
    src_files = [("wechat", [])]
    assert run_daily._source_of({}, src_files) == "unknown"  # 无 source_path 字段
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /mnt/disk01/workspaces/worksummary/vault/Projects/项目推进流水线 && python3 -m pytest scripts/test_multi_source_radar.py -q`
Expected: FAIL — `AttributeError: module 'run_daily' has no attribute '_source_of'`。

- [ ] **Step 3: 实现 `_source_of`**

在 `scripts/run_daily.py` 的 `fetch_dedup_list` 之后（约 217 行后、`# ─── 核心：调 persona` 之前）插入：

```python
def _source_of(cand: dict, src_files: list[tuple[str, list[Path]]]) -> str:
    """candidate.source_path（vault 相对）回溯到所属采集源 name；命中不到回 'unknown'。

    一个 candidate 来自一个文件，一个文件只属一个源（root 排他，决定 #4），故首匹配即定。
    src_files = [(source_name, [该源喂进来的绝对路径])]，由 stage_radar 聚合时传入。"""
    sp = cand.get("source_path", "")
    if sp:
        target = str(VAULT_ROOT / sp)
        for sname, files in src_files:
            if any(str(f) == target for f in files):
                return sname
    return "unknown"
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /mnt/disk01/workspaces/worksummary/vault/Projects/项目推进流水线 && python3 -m pytest scripts/test_multi_source_radar.py -q`
Expected: PASS（3 条 `_source_of` 用例绿；Task 1 的 4 条仍绿）。

- [ ] **Step 5: Commit**

```bash
git add scripts/run_daily.py scripts/test_multi_source_radar.py
git commit -m "feat(pa-radar): _source_of 候选源追溯 helper

ADR-0007 决定 #6。candidate.source_path 回溯到采集源 name（root 排他 → 首匹配即定）。"
```

---

## Task 3: `radar_prompt` per-project 签名 + `stage_radar` 多源重写（耦合，一次提交）

> **为何耦合一次提交：** `radar_prompt` 签名从 `(today_new, profiles, dedup)` 改为 `(project, today_new, prof, dedup_items)`，唯一调用点是 `stage_radar`。两者必须同一次改动，否则中间态 `stage_radar` 用旧签名调新函数会崩（cron 才暴露）。本 task 内先写测试、再一起改函数 + 调用点、一次提交。

**Files:**
- Modify: `scripts/run_daily.py:311-331`（`radar_prompt`）、`scripts/run_daily.py:408-437`（`stage_radar`）
- Test: `scripts/test_multi_source_radar.py`（追加）

- [ ] **Step 1: 追加失败测试**

在 `scripts/test_multi_source_radar.py` 末尾追加。先建一个共享的 `_setup_radar` helper（多源集成测试用）：

```python
# ─── radar_prompt per-project（ADR-0007 决定 #5）──────────────────────
def test_radar_prompt_targets_single_project(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "VAULT_ROOT", tmp_path)
    f = tmp_path / "20260719_x.md"
    prof = {"match_surface": {"one_liner": "量化选股", "keywords": ["A股", "RPS"]}}
    # Act
    p = run_daily.radar_prompt("ashare-llm-analyst", [f], prof, ["PR:已有分支"])
    # Assert：只含这一个项目、明确点名
    assert "只针对项目【ashare-llm-analyst】" in p
    assert "量化选股" in p and "A股" in p and "RPS" in p
    assert "PR:已有分支" in p          # 去重清单传入
    assert "cc-web-control" not in p   # 不串项目


# ─── stage_radar 多源核心（ADR-0007 决定 #1/#3/#5/#6）─────────────────
def _setup_radar(tmp_path, monkeypatch, sources, profiles):
    """把 stage_radar 的模块全局指向 tmp_path，建好 source roots；返回 state 目录。"""
    monkeypatch.setattr(run_daily, "VAULT_ROOT", tmp_path)
    pa = tmp_path / ".pa"
    state = pa / "state"
    state.mkdir(parents=True)
    monkeypatch.setattr(run_daily, "PA_HOME", pa)
    monkeypatch.setattr(run_daily, "STATE_DIR", state)
    for src in sources:
        (tmp_path / src["root"]).mkdir(parents=True, exist_ok=True)
    return state


def _put(root_rel, name, tmp_path):
    f = tmp_path / root_rel / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("# stub\n", encoding="utf-8")
    return f


def test_stage_radar_routes_files_by_target_projects(tmp_path, monkeypatch):
    # Arrange：wechat→cc-web-control、drop-zone→ashare，各放一篇
    sources = [
        {"name": "wechat", "kind": "directory", "root": "Knowledge/微信",
         "content_glob": "**/[0-9]*.md", "target_projects": ["cc-web-control"],
         "marker": "state/consumed_wechat_date"},
        {"name": "drop-zone", "kind": "local-file", "root": "Knowledge/投递箱",
         "content_glob": "**/[0-9]*.md", "target_projects": ["ashare"],
         "marker": "state/consumed_dropzone"},
    ]
    profiles = {"cc-web-control": {"name": "cc-web-control", "match_surface": {}},
                "ashare": {"name": "ashare", "match_surface": {}}}
    _setup_radar(tmp_path, monkeypatch, sources, profiles)
    _put("Knowledge/微信", "20260719_a.md", tmp_path)
    _put("Knowledge/投递箱", "20260719_b.md", tmp_path)

    seen = {}
    def fake_persona(name, prompt, stage, label):
        proj = label.split("-", 1)[1]                  # label="radar-<project>"
        seen[proj] = prompt
        m = __import__("re").search(r"- (Knowledge/[^\n]+\.md)", prompt)
        return ({"candidates": [{"project": proj, "source_path": m.group(1) if m else ""}],
                 "stats": {"signals_extracted": 1, "dropped_low_relevance": 0, "dropped_dedup": 0}},
                {"cost": 0.0, "turns": 1})
    monkeypatch.setattr(run_daily, "run_persona", fake_persona)
    monkeypatch.setattr(run_daily, "fetch_dedup_list", lambda profs: {})

    # Act
    out = run_daily.stage_radar(
        SimpleNamespace(force=False, dry_run=True, limit=None), sources, profiles, "20260719")

    # Assert：两项目各只收到自己订阅源的文件
    assert set(seen) == {"cc-web-control", "ashare"}
    assert "微信" in seen["cc-web-control"] and "投递箱" not in seen["cc-web-control"]
    assert "投递箱" in seen["ashare"] and "微信" not in seen["ashare"]
    # candidate 带源追溯 + per_source 计数
    assert {c["source"] for c in out["candidates"]} == {"wechat", "drop-zone"}
    assert out["per_source"] == {"wechat": 1, "drop-zone": 1}
    assert out["today_new_count"] == 2


def test_stage_radar_source_without_target_projects_feeds_nothing(tmp_path, monkeypatch):
    # Arrange：wechat 有文件但无 target_projects → 喂 0 项目、不调 radar
    sources = [{"name": "wechat", "kind": "directory", "root": "Knowledge/微信",
                "content_glob": "**/[0-9]*.md", "marker": "state/consumed_wechat_date"}]
    profiles = {"cc-web-control": {"name": "cc-web-control", "match_surface": {}}}
    _setup_radar(tmp_path, monkeypatch, sources, profiles)
    _put("Knowledge/微信", "20260719_a.md", tmp_path)
    called = []
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a: called.append(a) or ({"candidates": [], "stats": {}}, {"cost": 0.0, "turns": 0}))
    monkeypatch.setattr(run_daily, "fetch_dedup_list", lambda profs: {})

    out = run_daily.stage_radar(
        SimpleNamespace(force=False, dry_run=False, limit=None), sources, profiles, "20260719")

    # Assert：文件被发现（per_source=1）但不喂任何项目、不调 radar
    assert called == []
    assert out["candidates"] == []
    assert out["per_source"] == {"wechat": 1}
    # 无有效订阅 → marker 不 bump（文件保持可发现，待日后接源）
    assert not (tmp_path / ".pa" / "state" / "consumed_wechat_date").exists()


def test_stage_radar_project_with_no_files_skips_radar(tmp_path, monkeypatch):
    # Arrange：ashare 订阅 drop-zone，但 drop-zone 今日 0 新文件 → ashare 不调
    sources = [
        {"name": "wechat", "kind": "directory", "root": "Knowledge/微信",
         "content_glob": "**/[0-9]*.md", "target_projects": ["cc-web-control"],
         "marker": "state/consumed_wechat_date"},
        {"name": "drop-zone", "kind": "local-file", "root": "Knowledge/投递箱",
         "content_glob": "**/[0-9]*.md", "target_projects": ["ashare"],
         "marker": "state/consumed_dropzone"},
    ]
    profiles = {"cc-web-control": {"name": "cc-web-control", "match_surface": {}},
                "ashare": {"name": "ashare", "match_surface": {}}}
    _setup_radar(tmp_path, monkeypatch, sources, profiles)
    _put("Knowledge/微信", "20260719_a.md", tmp_path)   # 仅 wechat 有
    seen = []
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda n, p, s, lbl: seen.append(lbl) or ({"candidates": [], "stats": {}}, {"cost": 0.0, "turns": 0}))
    monkeypatch.setattr(run_daily, "fetch_dedup_list", lambda profs: {})

    run_daily.stage_radar(
        SimpleNamespace(force=False, dry_run=True, limit=None), sources, profiles, "20260719")

    # Assert：只调了 cc-web-control，ashare 因无订阅文件不调（省）
    assert seen == ["radar-cc-web-control"]


def test_stage_radar_bumps_marker_only_after_success(tmp_path, monkeypatch):
    # Arrange：有文件 + 有效订阅，但 run_persona 抛错 → marker 不 bump（失败语义保持）
    sources = [{"name": "wechat", "kind": "directory", "root": "Knowledge/微信",
                "content_glob": "**/[0-9]*.md", "target_projects": ["cc-web-control"],
                "marker": "state/consumed_wechat_date"}]
    profiles = {"cc-web-control": {"name": "cc-web-control", "match_surface": {}}}
    _setup_radar(tmp_path, monkeypatch, sources, profiles)
    _put("Knowledge/微信", "20260719_a.md", tmp_path)
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("persona boom")))
    monkeypatch.setattr(run_daily, "fetch_dedup_list", lambda profs: {})

    # Act / Assert：radar 抛 → 不 bump
    with pytest.raises(RuntimeError):
        run_daily.stage_radar(
            SimpleNamespace(force=False, dry_run=False, limit=None), sources, profiles, "20260719")
    assert not (tmp_path / ".pa" / "state" / "consumed_wechat_date").exists()


def test_stage_radar_no_sources0_hardcode(tmp_path, monkeypatch):
    # Arrange：sources 顺序无关，全部被遍历（去 sources[0] 硬编码）
    sources = [
        {"name": "drop-zone", "kind": "local-file", "root": "Knowledge/投递箱",
         "content_glob": "**/[0-9]*.md", "target_projects": ["ashare"],
         "marker": "state/consumed_dropzone"},
        {"name": "wechat", "kind": "directory", "root": "Knowledge/微信",
         "content_glob": "**/[0-9]*.md", "target_projects": ["cc-web-control"],
         "marker": "state/consumed_wechat_date"},
    ]
    profiles = {"cc-web-control": {"name": "cc-web-control", "match_surface": {}},
                "ashare": {"name": "ashare", "match_surface": {}}}
    _setup_radar(tmp_path, monkeypatch, sources, profiles)
    _put("Knowledge/投递箱", "20260719_b.md", tmp_path)   # drop-zone 排第一
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda n, p, s, lbl: ({"candidates": [], "stats": {}}, {"cost": 0.0, "turns": 0}))
    monkeypatch.setattr(run_daily, "fetch_dedup_list", lambda profs: {})

    out = run_daily.stage_radar(
        SimpleNamespace(force=False, dry_run=True, limit=None), sources, profiles, "20260719")
    # Assert：两源都被发现（不只 sources[0]）
    assert out["per_source"] == {"drop-zone": 1, "wechat": 0}
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /mnt/disk01/workspaces/worksummary/vault/Projects/项目推进流水线 && python3 -m pytest scripts/test_multi_source_radar.py -q`
Expected: FAIL — `radar_prompt` 当前签名不接受 4 参；`stage_radar` 仍用 `sources[0]`（路由/无订阅/失败不 bump/顺序无关 全不满足）。

- [ ] **Step 3: 改 `radar_prompt` 为 per-project 签名**

替换 `scripts/run_daily.py:311-331` 的 `radar_prompt`：

```python
def radar_prompt(project: str, today_new: list[Path], prof: dict, dedup_items: list[str]) -> str:
    """per-project radar prompt（ADR-0007 决定 #5）。

    只含这一个项目的 match_surface + 它订阅到的文件 + 它的去重清单。多源时 stage_radar 按项目各调一次，
    避免单源混喂让无关项目白读（现状 ashare 被 wechat 文件淹没、全进低分桶）。"""
    files = "\n".join(f"- {p.relative_to(VAULT_ROOT)}" for p in today_new)
    ms = prof.get("match_surface", {})
    surf = f"- {project}: one_liner=\"{ms.get('one_liner','')}\" keywords={ms.get('keywords',[])}"
    dd_block = "\n".join(dedup_items) if dedup_items else "（无未关闭 PR / 在途 PRD）"
    return f"""今日新内容文件（共 {len(today_new)} 篇，逐篇 Read 后抽技术信号，只针对项目【{project}】）：
{files}

白名单项目 match_surface：
{surf}

去重清单（命中则丢弃该信号）：
{dd_block}

按你的 persona 输出契约：只吐一行 JSON（candidates 数组，relevance<0.5 丢弃，每条带 source_path）。"""
```

- [ ] **Step 4: 重写 `stage_radar` 多源遍历**

替换 `scripts/run_daily.py:408-437` 的整个 `stage_radar`：

```python
def stage_radar(args, sources, profiles, stamp) -> dict:
    cand_file = STATE_DIR / f"candidates_{stamp}.json"
    if cand_file.is_file() and not args.force:
        log(f"[radar] 复用已有 {cand_file.name}（--force 重跑）")
        return json.loads(cand_file.read_text(encoding="utf-8"))

    # 1) 每源 discover（marker 在「全部 radar 成功后」才 bump——保持原失败不 bump 语义；
    #    先记 new_max，不立即写 marker）。dry_run 时同样延后到末尾跳过。
    per_source_new: dict[str, list[Path]] = {}
    per_source_newmax: dict[str, str] = {}
    for src in sources:                                   # ← 去 sources[0] 硬编码（ADR-0007 决定 #3）
        marker = read_marker(src)
        today_new = discover_today_new(src, marker, args.limit)
        per_source_new[src["name"]] = today_new
        if today_new:
            per_source_newmax[src["name"]] = max(re.match(r"(\d{8})", p.name).group(1) for p in today_new)
        log(f"[radar] source={src['name']} kind={src.get('kind','directory')} "
            f"marker={marker}｜今日新（>marker）={len(today_new)}")
        for p in today_new:
            log(f"        - {p.relative_to(VAULT_ROOT)}")

    total_new = sum(len(v) for v in per_source_new.values())
    if total_new == 0:
        log("[radar] 全源今日无新内容，跳过")
        empty = {"candidates": [], "today_new_count": 0, "per_source": {}, "stats": {}, "per_project_stats": {}}
        cand_file.write_text(json.dumps(empty, ensure_ascii=False, indent=2), encoding="utf-8")
        return empty

    # 2) 按项目聚合：project → [(source_name, [该源喂进来的文件])]
    #    target_projects 缺省 = 不喂任何项目（grilling Q4 定，防新源忘标喂到不相关项目）
    proj_src_files: dict[str, list[tuple[str, list[Path]]]] = {}
    for src in sources:
        for proj in (src.get("target_projects") or []):
            if proj in profiles:
                proj_src_files.setdefault(proj, []).append((src["name"], per_source_new[src["name"]]))

    # 3) 按项目调 radar（只有「订阅到新文件」的项目才调——无订阅不调，比现状更省）
    dedup = fetch_dedup_list(profiles)
    all_candidates: list[dict] = []
    per_project_stats: dict[str, dict] = {}
    for proj, src_files in proj_src_files.items():
        flat = [f for _, fs in src_files for f in fs]
        if not flat:
            continue                                      # 无订阅文件 → 不调
        payload, meta = run_persona(
            "pa-radar", radar_prompt(proj, flat, profiles[proj], dedup.get(proj, [])),
            "radar", f"radar-{proj}")
        for c in payload.get("candidates", []):
            c.setdefault("project", proj)
            c.setdefault("source", _source_of(c, src_files))   # 追溯来自哪个源（决定 #6）
            all_candidates.append(c)
        per_project_stats[proj] = {**payload.get("stats", {}), "cost": meta["cost"], "turns": meta["turns"]}
        log(f"[radar] ✅ {proj}: candidates={len(payload.get('candidates', []))}｜"
            f"cost=${meta['cost']:.4f} turns={meta['turns']}")

    # 4) stats 扁平聚合（report 段向后兼容：仍读 stats.signals_extracted / dropped_*）
    flat_stats = {"signals_extracted": 0, "dropped_low_relevance": 0, "dropped_dedup": 0}
    for s in per_project_stats.values():
        for k in flat_stats:
            flat_stats[k] += s.get(k, 0)

    out = {"candidates": all_candidates,
           "today_new_count": total_new,
           "per_source": {k: len(v) for k, v in per_source_new.items()},
           "stats": flat_stats,
           "per_project_stats": per_project_stats}
    cand_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # 5) radar 全成功后才 bump：只 bump「有新文件 且 至少喂了一个有效订阅项目」的源。
    #    无 target_projects / 目标不是已知 profile 的源不 bump——文件保持可发现，待日后接源（防静默丢失）。
    if not args.dry_run:
        for src in sources:
            newmax = per_source_newmax.get(src["name"])
            if newmax is None:
                continue
            valid_targets = [t for t in (src.get("target_projects") or []) if t in profiles]
            if not valid_targets:
                continue
            bump_marker(src, newmax)
            log(f"[radar] {src['name']} marker bump → {newmax}")
    else:
        log("[radar] --dry-run，不 bump marker")
    return out
```

- [ ] **Step 5: 跑测试验证通过**

Run: `cd /mnt/disk01/workspaces/worksummary/vault/Projects/项目推进流水线 && python3 -m pytest scripts/test_multi_source_radar.py -q`
Expected: PASS（全部用例绿：load_sources 4 + _source_of 3 + radar_prompt 1 + stage_radar 5 = 13）。

- [ ] **Step 6: 跑全量回归（不破其他单测）**

Run: `cd /mnt/disk01/workspaces/worksummary/vault/Projects/项目推进流水线 && python3 -m pytest scripts/ -q`
Expected: PASS（含 `test_dev_agent_source.py`/`test_verify_loop.py`/`test_inject.py`/`test_bash_allowlist.py` 全绿——本 task 未碰它们的代码面）。

- [ ] **Step 7: Commit**

```bash
git add scripts/run_daily.py scripts/test_multi_source_radar.py
git commit -m "feat(pa-radar): stage_radar 多源 + target_projects 路由 + per-project radar

ADR-0007 决定 #1/#3/#5/#6。去 sources[0] 硬编码：遍历全部源各自 discover、
按 target_projects 聚合到 project、订阅到新文件才调 radar（per-project 签名）。
candidate 带 source 字段追溯；stats 扁平聚合保 report 段向后兼容；
marker 仅在「全 radar 成功 + 有有效订阅」后 bump（失败不 bump / 无订阅不 bump）。"
```

---

## Task 4: `sources.yaml` 配置（wechat 标 target_projects + drop-zone 源 + 投递箱目录）

**Files:**
- Modify: `.project-auto/sources.yaml`
- Create: `Knowledge/投递箱/README.md`

> **关键：** Q4 定 `target_projects` 缺省=不喂。若不给 wechat 加 `target_projects: [cc-web-control]`，本次改动后 wechat 将停止喂 cc-web-control（线上回归）。所以必须同步给 wechat 显式标。

- [ ] **Step 1: 改 `sources.yaml`**

替换 `.project-auto/sources.yaml` 全文：

```yaml
# pa-radar 采集源集合（source set）—— ADR-0007 多源。
# 每条源：kind（采集层分类，消费侧零分支）+ target_projects（喂哪些项目，缺省=不喂，grilling Q4 定）+ marker（各源独立）。
# marker 仅作"跳旧日"快路径；幂等真靠 GitHub 去重 + run 锁（ADR-0004），不靠 marker。
# 文件名 YYYYMMDD = 采集戳（内容进入源的日期：fetcher 跑/用户投日），非内容日（决定 #3）。
sources:
  - name: wechat
    kind: directory                             # 采集层 kind（消费侧零分支）
    root: Knowledge/微信
    content_glob: "**/[0-9]*.md"                # 8位日期前缀 .md：分类总结/深度解读/单篇深度全收
    exclude_glob: "**/*{审校报告,URL参考列表,文章清单}*.md"  # 跳 meta
    target_projects: [cc-web-control]           # 缺省=不喂；wechat 显式喂 cc-web-control（保现状）
    marker: state/consumed_wechat_date

  - name: drop-zone                             # local-file 源（directory 特例，本次即可用）
    kind: local-file                            # 用户/脚本直接丢 YYYYMMDD_*.md 到 root
    root: Knowledge/投递箱
    content_glob: "**/[0-9]*.md"
    target_projects: [ashare-llm-analyst]       # 给 ashare 的手动投递通道（解"单源零交集"）
    marker: state/consumed_dropzone

  # 以下三种 fetcher 后续各自独立任务（schema 先就位，本次不实现）：
  # - agent-deepresearch（agent + deep-research skill 搜）
  # - wechat-url（解析指定 mp.weixin URL）
  # - github-repo（gh API 拉 releases/readme/issues）
```

- [ ] **Step 2: 建 `Knowledge/投递箱/` 目录 + 说明**

```bash
mkdir -p /mnt/disk01/workspaces/worksummary/vault/Knowledge/投递箱
```

写入 `Knowledge/投递箱/README.md`：

```markdown
# 投递箱（drop-zone 采集源）

ashare-llm-analyst 项目的**手动投递通道**（ADR-0007 local-file 源）。

## 用法

把要喂给 ashare radar 的内容丢到这里，文件名 **`YYYYMMDD_*.md`**（`YYYYMMDD` = **投递日**，
不是内容日；radar marker 按此前缀判新）。

- 例：`20260719_量化研报-选股因子.md`
- 内容本身的日期写在 md frontmatter `date:` 里，不进文件名。

当晚 cron 跑 radar 时，本目录新文件会被拾取、按 `target_projects: [ashare-llm-analyst]` 喂给 ashare。
```

- [ ] **Step 3: 冒烟校验配置加载**

Run: `cd /mnt/disk01/workspaces/worksummary/vault/Projects/项目推进流水线 && python3 -c "import sys; from pathlib import Path; sys.path.insert(0,'scripts'); import run_daily; srcs=run_daily.load_sources(); print([(s['name'],s.get('kind'),s.get('target_projects')) for s in srcs])"`
Expected: 输出 `[('wechat', 'directory', ['cc-web-control']), ('drop-zone', 'local-file', ['ashare-llm-analyst'])]`，无 warn、无 exit（root 不重复、name 唯一）。

- [ ] **Step 4: Commit**

```bash
git add .project-auto/sources.yaml Knowledge/投递箱/README.md
git commit -m "feat(sources): wechat 标 target_projects + drop-zone local-file 源

ADR-0007。wechat 显式 target_projects=[cc-web-control]（Q4 缺省=不喂，不标则回归）；
新增 drop-zone local-file 源喂 ashare（解单源零交集）。3 个 fetcher kind 注释占位。"
```

> 注：`.project-auto/` 与 `Knowledge/` 均被 gitignore（运行态/隐私）。若 `git add` 报「does not match files」（被 ignore），用 `git add -f` 强加，或按仓内既有约定跳过 commit（仅本地生效即可）。**先跑 `git check-ignore .project-auto/sources.yaml Knowledge/投递箱/README.md` 确认**，被 ignore 则本步仅落盘、不 commit（commit 跳过，标 Step 4 为「config 落盘（gitignored，不入仓）」）。

---

## Task 5: 端到端冒烟（dry-run 不花钱）+ 全量回归

**Files:** 无新增（纯验证）

- [ ] **Step 1: cron 副作用回归网（保护每晚 cron 不崩）**

Run: `cd /mnt/disk01/workspaces/worksummary/vault/Projects/项目推进流水线 && python3 -m pytest scripts/test_dev_agent_source.py::test_run_daily_import_does_not_load_claude_sdk -q`
Expected: PASS（本次改动未在顶部加 sdk import）。

- [ ] **Step 2: 三文件 syntax 绿**

Run: `cd /mnt/disk01/workspaces/worksummary/vault/Projects/项目推进流水线 && python3 -c "import ast; [ast.parse(open(f).read()) for f in ('scripts/run_daily.py','scripts/test_multi_source_radar.py')]; print('syntax ok')"`
Expected: 输出 `syntax ok`。

- [ ] **Step 3: 全量单测绿**

Run: `cd /mnt/disk01/workspaces/worksummary/vault/Projects/项目推进流水线 && python3 -m pytest scripts/ -q`
Expected: PASS（所有既有测试 + 13 条多源新测试全绿）。

- [ ] **Step 4: radar 段 dry-run 端到端（不调真 persona、不花钱）**

`stage_radar` 会调真 `run_persona`（花钱）。为零成本验证「多源 discover + 路由」机械逻辑，用 `--limit 0` 让每源 discover 返回 0 篇 → 走「全源无新内容」早退分支、不调 persona：

Run: `cd /mnt/disk01/workspaces/worksummary/vault && python3 Projects/项目推进流水线/scripts/run_daily.py --from-stage radar --to-stage radar --limit 0 --dry-run --stamp 20260719 --force 2>&1 | tail -20`
Expected: 日志见两行 `[radar] source=wechat kind=directory ...` + `[radar] source=drop-zone kind=local-file ...` + `[radar] 全源今日无新内容，跳过`；`candidates_20260719.json` 被写成 `{"candidates": [], "today_new_count": 0, "per_source": {}, "stats": {}, "per_project_stats": {}}`。

> 验证后**回滚这个 force 重跑出的 candidates 文件**（避免污染当日真实产物）：
> `git -C /mnt/disk01/workspaces/worksummary/vault status .project-auto/state/candidates_20260719.json`（gitignored，本地残留无害；若当日还要真跑 radar，`--force` 会覆盖，无需手动删）。

- [ ] **Step 5: 确认 report 段未被新 stats 结构打破（静态读）**

人工核对 `scripts/run_daily.py` 的 `stage_report`（1211-1303 行）：它读 `cand.get("stats", {})` 取 `signals_extracted/dropped_low_relevance/dropped_dedup`。新 `stage_radar` 输出的 `stats` 是扁平聚合（含这三个键），report **无需改动**。核对 `today_new_count` 仍由 report 读（1242 行）→ 新输出保留该键 ✓。

- [ ] **Step 6: 终态 commit（若有未提交的语法/注释微调）**

```bash
git status   # 确认仅余预期文件
# 若有零散改动：
git add -p
git commit -m "chore(pa-radar): 多源消费接口冒烟校验收尾"
```

---

## Self-Review（plan 自检）

**1. Spec 覆盖（对照 ADR-0007 决定 #1–#7）:**
- 决定 #1（target_projects 白名单，缺省=不喂）→ Task 3 `stage_radar` 聚合 + Task 4 wechat 显式标 ✓
- 决定 #2（kind 维度，消费侧零分支，未就绪 warn 不阻断）→ Task 1 `load_sources` ✓
- 决定 #3（全被动 + fetcher 解耦 + 日期戳契约）→ 文件名契约已在 Task 4 README + sources.yaml 注释落地；`discover_today_new` 零改动（本就按 `\d{8}` 前缀，Task 3 复用）✓（**注：wechat 采集器改盖生成日 / 历史文件 mtime 重盖 = 采集层 follow-up，不在本次消费接口范围，ADR 决定 #7 已圈**）
- 决定 #4（name 唯一 + root 排他）→ Task 1 `load_sources` 校验 ✓
- 决定 #5（radar 按项目调 N 次 + per-project 签名）→ Task 3 `radar_prompt` + `stage_radar` ✓
- 决定 #6（candidate 带 source 字段 + stats per-project）→ Task 2 `_source_of` + Task 3 ✓
- 决定 #7（本次实现 directory + local-file，3 fetcher 各自 follow-up）→ Task 1-5 全是消费接口；3 fetcher 仅 schema 注释占位（Task 4）✓

**2. 占位扫描:** 无 TBD/TODO；每步含可跑命令 + 预期输出 + 完整代码。fetcher ⏳ 是设计意图（决定 #7 明确圈外），非占位。

**3. 类型/签名一致:**
- `radar_prompt(project, today_new, prof, dedup_items)` — Task 3 定义签名，`stage_radar` 调用点（`radar_prompt(proj, flat, profiles[proj], dedup.get(proj, []))`）+ 测试调用一致 ✓
- `_source_of(cand, src_files)` — Task 2 定义，Task 3 `stage_radar` 调用 `_source_of(c, src_files)` 一致；`src_files` 元素类型 `tuple[str, list[Path]]` 在定义/聚合/调用三处一致 ✓
- `per_source_new` / `per_source_newmax` / `proj_src_files` / `per_project_stats` / `flat_stats` 命名贯穿 Task 3 各步一致 ✓
- candidates JSON 新键 `per_source` / `per_project_stats` + 旧键 `candidates/today_new_count/stats` 保持 → report 段（1211-1303）零改动 ✓

**4. 风险点已处理:**
- Q4 缺省=不喂 导致 wechat 线上回归 → Task 4 显式标 `target_projects: [cc-web-control]` ✓
- marker 失败不 bump 语义 → Task 3 Step 4 注释 + `test_stage_radar_bumps_marker_only_after_success` ✓
- 无 target_projects 源静默丢文件 → Task 3 bump 规则「无有效订阅不 bump」+ `test_stage_radar_source_without_target_projects_feeds_nothing` ✓
- report 段读旧 stats 结构 → Task 3 flat_stats 扁平聚合 + Task 5 Step 5 静态核对 ✓
- cron sdk 副作用 → Task 5 Step 1 回归网 ✓
- `.project-auto`/`Knowledge` gitignored → Task 4 Step 4 注明 `git check-ignore` + `-f` 兜底 ✓

---

## Execution Handoff

Plan complete and saved to `Projects/项目推进流水线/docs/plans/2026-07-19-multi-source-radar.md`. Two execution options:

**1. Subagent-Driven (recommended)** — 我每个 task 派一个 fresh subagent，task 间两段 review（实现质量 + spec 对齐），迭代快、上下文干净。

**2. Inline Execution** — 在本会话用 executing-plans 逐 task 跑，批次执行 + 检查点 review。

哪种？
