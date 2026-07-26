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


# ─── 每个来源 kind 的消费侧行为（ADR-0007 决定 #2：消费侧零分支）────────
# 决定 #2：消费侧完全不读 kind——5 种 kind 在 radar/discover 完全等价（都 glob 目录）。
# 故「每个来源都测」= 证明 kind 不改变消费行为 + 未就绪 fetcher 静默降级 + 源卫生（exclude）。
_KINDS = ["directory", "local-file", "wechat-url", "github-repo", "agent-deepresearch"]


@pytest.mark.parametrize("kind", _KINDS)
def test_stage_radar_kind_agnostic(tmp_path, monkeypatch, kind):
    """5 种 kind 消费侧完全等价：同样文件 → 同样 discover/route/bump（决定 #2 零分支）。

    不论 directory / local-file / 三个 fetcher kind，消费侧只按 content_glob 扫目录，
    kind 从不进分支。本用例对每种 kind 跑同一场景，断言产出逐字相同。
    顺带覆盖「正向 bump happy-path」（files + 有效订阅 + radar 成功 → marker bump）。"""
    sources = [{"name": "s", "kind": kind, "root": "Knowledge/src",
                "content_glob": "**/[0-9]*.md", "target_projects": ["cc-web-control"],
                "marker": "state/consumed_m"}]
    profiles = {"cc-web-control": {"name": "cc-web-control", "match_surface": {}}}
    _setup_radar(tmp_path, monkeypatch, sources, profiles)
    _put("Knowledge/src", "20260719_a.md", tmp_path)
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda n, p, s, lbl: ({"candidates": [], "stats": {}}, {"cost": 0.0, "turns": 0}))
    monkeypatch.setattr(run_daily, "fetch_dedup_list", lambda profs: {})

    out = run_daily.stage_radar(
        SimpleNamespace(force=False, dry_run=False, limit=None), sources, profiles, "20260719")

    # 无论哪种 kind：发现 1 篇、喂 cc-web-control、marker bump 到 20260719
    assert out["per_source"] == {"s": 1}
    assert out["today_new_count"] == 1
    assert (tmp_path / ".pa" / "state" / "consumed_m").read_text(encoding="utf-8").strip() == "20260719"


def test_unready_fetcher_source_silent_at_stage(tmp_path, monkeypatch):
    """fetcher 未就绪的源（3 个 follow-up kind）在 stage_radar 静默降级：
    root 空（fetcher 没跑）→ 0 文件 → 不调 radar、不 bump、不崩。
    把「决定 #2 未就绪 warn 不阻断」从 load_sources 级延伸到 stage_radar 级验证。"""
    sources = [
        {"name": "deep", "kind": "agent-deepresearch", "root": "Knowledge/深研",
         "content_glob": "**/[0-9]*.md", "fetcher": "scripts/fetchers/deepresearch.py",
         "target_projects": ["ashare"], "marker": "state/consumed_deep"},
    ]
    profiles = {"ashare": {"name": "ashare", "match_surface": {}}}
    _setup_radar(tmp_path, monkeypatch, sources, profiles)
    # root 故意不放任何 YYYYMMDD 文件（fetcher 未跑 → 无产出）
    called = []
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda *a: called.append(a) or ({"candidates": [], "stats": {}}, {"cost": 0.0, "turns": 0}))
    monkeypatch.setattr(run_daily, "fetch_dedup_list", lambda profs: {})

    out = run_daily.stage_radar(
        SimpleNamespace(force=False, dry_run=False, limit=None), sources, profiles, "20260719")

    # root 空 → 全源 0 新内容 → 早退：不调 radar、不 bump
    assert called == []
    assert out["today_new_count"] == 0
    assert out["candidates"] == []
    assert not (tmp_path / ".pa" / "state" / "consumed_deep").exists()


def test_directory_source_exclude_glob_filters_meta(tmp_path, monkeypatch):
    """wechat 源的 exclude_glob 跳 meta（审校报告 / URL参考列表 / 文章清单）——
    源特定采集卫生：discover 须排除这些、不进 radar。"""
    sources = [{"name": "wechat", "kind": "directory", "root": "Knowledge/微信",
                "content_glob": "**/[0-9]*.md",
                "exclude_glob": "**/*{审校报告,URL参考列表,文章清单}*.md",
                "target_projects": ["cc-web-control"], "marker": "state/consumed_wechat"}]
    profiles = {"cc-web-control": {"name": "cc-web-control", "match_surface": {}}}
    _setup_radar(tmp_path, monkeypatch, sources, profiles)
    _put("Knowledge/微信", "20260719_正文.md", tmp_path)           # 留
    _put("Knowledge/微信", "20260719_审校报告.md", tmp_path)        # 排
    _put("Knowledge/微信", "20260719_URL参考列表.md", tmp_path)     # 排
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda n, p, s, lbl: ({"candidates": [], "stats": {}}, {"cost": 0.0, "turns": 0}))
    monkeypatch.setattr(run_daily, "fetch_dedup_list", lambda profs: {})

    out = run_daily.stage_radar(
        SimpleNamespace(force=False, dry_run=True, limit=None), sources, profiles, "20260719")

    # 只剩正文 1 篇（meta 被 exclude_glob 排掉）
    assert out["per_source"] == {"wechat": 1}
    assert out["today_new_count"] == 1


# ─── 取数窗口（window_days，ADR-0007 follow-up「往前追溯 N 天」）────────
def test_discover_window_days_picks_rolling_window(tmp_path, monkeypatch):
    # window_days=2：固定 today=20260721，窗口下沿=07-19；marker=00000000 也只取窗口内
    monkeypatch.setattr(run_daily, "VAULT_ROOT", tmp_path)
    import datetime as _dt   # datetime.date 不可 setattr today → 整体替换 run_daily.date 为带 today 的 stub
    monkeypatch.setattr(run_daily, "date", SimpleNamespace(today=lambda: _dt.date(2026, 7, 21)))
    root = tmp_path / "Knowledge/微信"
    root.mkdir(parents=True)
    for d in ("20260718", "20260719", "20260720", "20260721"):
        (root / f"{d}_x.md").write_text("#", encoding="utf-8")
    src = {"root": "Knowledge/微信", "content_glob": "**/[0-9]*.md", "window_days": 2}
    out = run_daily.discover_today_new(src, "00000000", None)
    # 07-19/20/21 在窗口内；07-18 早于 floor → 跳过（且无视 marker=00000000）
    assert sorted(p.name for p in out) == ["20260719_x.md", "20260720_x.md", "20260721_x.md"]


def test_discover_window_days_zero_is_waterline(tmp_path, monkeypatch):
    # window_days 缺省/0 → 现状水位线（>marker，单调不回溯），向后兼容
    monkeypatch.setattr(run_daily, "VAULT_ROOT", tmp_path)
    root = tmp_path / "Knowledge/微信"
    root.mkdir(parents=True)
    for d in ("20260717", "20260718", "20260719"):
        (root / f"{d}_x.md").write_text("#", encoding="utf-8")
    src = {"root": "Knowledge/微信", "content_glob": "**/[0-9]*.md"}    # 无 window_days
    out = run_daily.discover_today_new(src, "20260718", None)           # marker=20260718
    assert sorted(p.name for p in out) == ["20260719_x.md"]             # 仅取 >20260718


def test_stage_radar_dedup_skips_candidate_with_existing_prd(tmp_path, monkeypatch, capsys):
    # 出口去重（兜底）：前置过滤挡住「flat 里已产 PRD 的文件」后，persona 若仍为某个已产 PRD 的
    # source_path 产了 candidate（幻觉 / 该文件未在 flat），出口再拦一道。本用例专测这个兜底：
    # 喂进去的文件是未产的 20260720_new，persona 却返回指向已产 PRD 的 20260719_done。
    sources = [{"name": "wechat", "kind": "directory", "root": "Knowledge/微信",
                "content_glob": "**/[0-9]*.md", "target_projects": ["cc-web-control"],
                "marker": "state/consumed_wechat"}]
    profiles = {"cc-web-control": {"name": "cc-web-control", "match_surface": {}}}
    state = _setup_radar(tmp_path, monkeypatch, sources, profiles)
    _put("Knowledge/微信", "20260720_new.md", tmp_path)   # 喂进去的（未产 PRD）
    # 已有 PRD 指向另一个文件（不在 flat → 前置过滤挡不住 → 靠出口去重兜底）
    prd_dir = state / "prd" / "cc-web-control"
    prd_dir.mkdir(parents=True)
    (prd_dir / "20260719_old.md").write_text(
        "---\nproject: cc-web-control\nsource_path: Knowledge/微信/20260719_done.md\n---\n# old\n",
        encoding="utf-8")
    HALLUC = "Knowledge/微信/20260719_done.md"             # persona 幻觉返回的已产 PRD source_path
    monkeypatch.setattr(run_daily, "run_persona",
                        lambda n, p, s, lbl: ({"candidates": [{"project": "cc-web-control",
                                                                "source_path": HALLUC}],
                                               "stats": {}}, {"cost": 0.0, "turns": 1}))
    monkeypatch.setattr(run_daily, "fetch_dedup_list", lambda profs: {})

    out = run_daily.stage_radar(
        SimpleNamespace(force=False, dry_run=True, limit=None), sources, profiles, "20260721")

    assert out["candidates"] == []                          # 幻觉的 source_path 已有 PRD → 出口去重
    captured = capsys.readouterr()
    assert "去重" in captured.out or "去重" in captured.err  # 产出去重日志


def test_stage_radar_prefilter_excludes_already_prd_files_from_prompt(tmp_path, monkeypatch):
    # 前置去重（省 LLM）：已产 PRD 的文件根本不进 radar_prompt，只喂未产的。
    # 与出口去重（test_stage_radar_dedup_skips_candidate_with_existing_prd）互补：
    #   出口去重在 candidate 层（防重复产 PRD）；前置在 prompt 层（防重复 Read 已处理文件）。
    sources = [{"name": "wechat", "kind": "directory", "root": "Knowledge/微信",
                "content_glob": "**/[0-9]*.md", "target_projects": ["cc-web-control"],
                "marker": "state/consumed_wechat"}]
    profiles = {"cc-web-control": {"name": "cc-web-control", "match_surface": {}}}
    state = _setup_radar(tmp_path, monkeypatch, sources, profiles)
    _put("Knowledge/微信", "20260719_done.md", tmp_path)   # 已产 PRD
    _put("Knowledge/微信", "20260720_new.md", tmp_path)    # 未产
    prd_dir = state / "prd" / "cc-web-control"
    prd_dir.mkdir(parents=True)
    (prd_dir / "20260719_old.md").write_text(
        "---\nproject: cc-web-control\nsource_path: Knowledge/微信/20260719_done.md\n---\n# old\n",
        encoding="utf-8")
    seen_prompt = {}

    def fake_persona(name, prompt, stage, label):
        seen_prompt[label] = prompt
        return ({"candidates": [{"project": "cc-web-control",
                                 "source_path": "Knowledge/微信/20260720_new.md"}],
                 "stats": {}}, {"cost": 0.0, "turns": 1})

    monkeypatch.setattr(run_daily, "run_persona", fake_persona)
    monkeypatch.setattr(run_daily, "fetch_dedup_list", lambda profs: {})

    run_daily.stage_radar(
        SimpleNamespace(force=False, dry_run=True, limit=None), sources, profiles, "20260721")

    p = seen_prompt["radar-cc-web-control"]
    assert "20260720_new.md" in p            # 未产的喂了
    assert "20260719_done.md" not in p       # 已产 PRD 的被前置过滤，不进 prompt
