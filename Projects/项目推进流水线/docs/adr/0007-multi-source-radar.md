# 0007 — radar 多源：target_projects 白名单 + source kind + 消费/采集解耦

> **术语**：本文 `source`（`sources.yaml` 一条）= **采集源 (Ingest)**，radar 输入侧；与 CONTEXT.md「信息源 (Source)」（radar 输出侧、随 PRD 投递给 dev）是不同概念，勿混。代码标识符 `source`/`sources.yaml` 保留（实现细节）。
>
> 不违背 [[0001-vault-target-isolation]]（radar 仍只读 vault）；延续 [[0004-dispatch-devagent-source-of-truth]] 的去重清单（per-project 不变）。
> 把 `sources.yaml` 从单源（v1 仅 wechat、`stage_radar` 硬编码 `sources[0]`）扩为多源，并引入「源↔项目」路由，让非 AI-coding 项目（如 ashare）也能被合适的源喂到。

## 决定

1. **`sources.yaml` 每个 source 加 `target_projects` 白名单**：声明该源的文件只喂哪些项目；**缺省 = 不喂任何项目**（必须显式声明，grilling 2026-07-19 改——原「缺省=喂所有 admission」是默认开放，新源忘标 target_projects 会喂到不相关项目、radar 白读 + 噪音）。一个源可喂多项目，一个项目可被多源喂。
2. **source 加 `kind` 维度**：`directory` / `local-file` / `wechat-url` / `github-repo` / `agent-deepresearch`。kind 是**采集层**分类，消费侧（radar/discover）kind 无关。
   > **消费侧完全不读 kind（零分支）**（grilling 2026-07-19 定）：stage_radar/discover 不出现 `if kind==` 分支，5 种 kind 在消费侧完全等价（都 glob 目录）。声明了 `fetcher:` 路径但脚本不存在的源（如本次未实现的 3 种），sources.yaml 加载时 log warn（「该源 fetcher 未就绪、今日无产出」），**但不阻断 radar**——root 不存在/空即该源今日 0 新内容、其订阅项目不调 radar（静默）。fetcher 是否就绪是采集层的事，消费侧只看目录有没有新文件。
3. **全被动 + fetcher 解耦**：每种 kind 配一个独立 fetcher，预先把内容 normalize 成 `YYYYMMDD_*.md` 落到 `source.root`；**radar/discover 永远只按 `content_glob` 扫目录**，不关心 kind、不关心源怎么来的。
   > **日期戳契约（grilling 2026-07-19 定）**：文件名 `YYYYMMDD` = **采集戳**（内容进入采集源的日期：fetcher 跑/用户投的那天），**不是内容本身日期**。保证 marker 单调递增、旧日期文件（如 local-file 投的旧研报）也能被 `> marker` 拾——根治「文件名日期 vs 抓取时间」歧义（Bug B 多源泛化）。内容本身日期存 md frontmatter `date:`，不进文件名、不参与 marker。**迁移**：wechat 现状文件名标内容日（`20260717_*.md`），采集器需改为盖采集戳（生成日）；历史文件迁移时按 mtime 重盖戳。
4. **`stage_radar` 去 `sources[0]` 硬编码**：遍历全部 source，各自 `discover_today_new`；按 `target_projects` 把文件聚合到 `project → [它订阅的所有源的文件并集]`；marker 各源独立 bump（per-source 已是）。
   > **采集源身份 + root 排他**（grilling 2026-07-19 定）：`name` 是采集源唯一 key；一个 `root` 只能属一个采集源（`load_sources` 加载时校验 root 唯一、重复拒载并报错），杜绝双扫 / marker 互相污染 / candidate 重复。marker 路径（`source.marker`）随 name 唯一而隐含唯一。
5. **radar 按项目调 N 次**：只有「订阅到了新文件」的项目才调 `pa-radar`；`radar_prompt` 签名从 `(today_new, profiles, dedup)` 改为 `(project, 该项目订阅文件, 该项目 match_surface, 该项目去重清单)`。**无订阅的项目（如 ashare 暂未接 finance 源）→ 0 文件 → 不调 → 比现状更省**（现状 ashare 要白读全部 wechat 文件、全进低分桶）。
6. **`candidates_{stamp}.json` 带 `source` 字段**：每条 candidate 既有的 `project` 之外加 `source`（追溯来自哪个源），`stats` 升级为 per-project 明细。**dispatch 零改动**（已按 `candidate.project` 分发）。
7. **本次实现范围 = 消费接口**：`directory`（现状 wechat）+ `local-file`（directory 特例，用户直接丢文件到 root）跑通；5 种 kind 的 schema 全定义。`wechat-url` / `github-repo` / `agent-deepresearch` 三个 fetcher 各自后续独立任务。

## 背景

现状 `sources.yaml` 只有一条 wechat 源，`stage_radar` (`run_daily.py:414`) `source = sources[0]` 硬编码取第一个；`radar_prompt` 把全部 `today_new` 文件混喂给**所有** admission 项目的 `match_surface`。

实证痛点（2026-07-19 查证）：历次 4 次 radar 跑（0715/0717/0718/0719），`candidates` 全部归属 `cc-web-control`，**ashare-llm-analyst 一次都没出现过**。根因不是 radar bug、不是去重、不是没加载——两份 profile 都 `admission: true`、`radar_prompt` 确实遍历了全部 profiles——而是**信号源与项目方向零交集**：wechat 抓的全是 AI Coding / Claude Code / 具身智能 / 大模型内容，天然贴 cc-web-control 的 `[Claude Code, tmux, 浏览器控制...]`；与 ashare 的 `[A股, 量化, 选股, baostock, 财报分析, RPS...]` 几乎不撞 → 每条信号 `relevance < 0.5` → 全进 `dropped_low_relevance`。

结论：单源 + 混喂的架构让非 AI-coding 项目永远吃不到信号。要解，得让每个项目能订阅**对它有意义的源**，且能接**异构源**（不止公众号目录）。用户明确要接 4 种新源：① agent + deep-research 搜索；② 指定微信文章；③ 指定本地文件；④ 指定 github 仓库——正是给 ashare 这类项目喂信号的途径。

## 考虑过的替代

- **全混喂（现状）+ 靠 relevance 过滤**：拒——ashare 永远被 wechat 文件淹没、token 白读、低分桶膨胀。已实证无效。
- **领域标签（domain）粗筛**：拒——源和项目都加 `domain` 标签，radar 先按 domain 粗筛再算 relevance。比 `target_projects` 白名单多一层配置维护，而白名单已足够精准（源→项目是明确声明关系，不需要中间抽象）。
- **本次连通用采集框架一起做**：拒——4 种新源形态各异（agent 搜索 / URL 解析 / 本地文件 / gh API），且尚无确定的财经源先例（先有源才知道怎么采）；工程量数倍。先纯消费接口跑通、验证路由正确，fetcher 各自后续单独做（每个就是一个「读指定源 → normalize → 落盘」的脚本 + cron 接入）。
- **radar 单次调、prompt 内按项目分组**：拒——prompt 长、多项目耦合在一个调用里、ashare 仍要白读；按项目调 N 次，无订阅项目不调，总成本不升反降，且单项目失败不拖累其他、candidate 归属清晰。
- **active 源（radar 触发时实时调 fetcher）**：拒——把 fetcher 调度/重试/超时塞进 radar 阶段，耦合采集与消费。全被动模型（fetcher 预落盘、radar 只读目录）让两层彻底解耦，各自独立演进、独立测试。

## 后果

- **ashare 等非 AI-coding 项目获得信号通路**：`local-file` 源本次即用（把量化研报/笔记丢进 `Knowledge/投递箱/`、命名 `YYYYMMDD_*.md`，radar 当晚拾）；`wechat-url` / `github-repo` / `agent-deepresearch` 三个 fetcher 后续接入后自动生效（schema 已就位）。
- **单源硬编码 `sources[0]` 消除**；`stage_radar` 变 source 驱动遍历。
- **radar 调用 1 → N**，但「订阅到新文件才调」→ 总成本不升反降（现状 ashare 白读 wechat 的开销消失）。
- **fetcher 契约确立**：所有 kind 产出 `YYYYMMDD_*.md` 到 `source.root`（`discover_today_new` 的 `re.match(r"\d{8}")` 依赖此前缀）。采集层与消费层解耦，后续各自演进。
- **follow-up 待办**：① `wechat-url` fetcher；② `github-repo` fetcher（gh API 拉 releases/readme/issues）；③ `agent-deepresearch` fetcher（调 agent + deep-research skill）。

> **2026-07-19 终审记录（消费接口实现完成后，独立 reviewer）**——发现 stage_radar 现实现「任一项目 radar 抛错 → **全源** marker 都不 bump」（抛错在 `cand_file.write_text` / bump 循环之前 propagate）。单源时这连贯，多源下变成**跨源耦合**：一个 flaky 的 ashare radar 会拖累 wechat→cc-web-control 重复 radar 已处理的旧文件（token 白烧 + 旧信号污染 candidate 流）。
>
> **决定：本次不改，接受现状。** 理由：① 现配置每源只喂一个项目（wechat→cc-web-control、drop-zone→ashare），且 drop-zone 暂无文件，耦合近期咬不到；② 失败模型从「全崩 loud」改「部分继续」是行为契约变更（影响 cron 是否因单项目失败而整体报错），应独立决策，不混入消费接口交付。原 follow-up ④ 即此项。
>
> 终审另发现 3 项，一并留 follow-up：⑤ `c.setdefault("project", proj)` 应改 **override**（per-project prompt 下编排器才是 project 归属权威，persona 回显的 project 不可信、可能错派到他仓）；⑥ 测试缺**正向 bump happy-path** + **跨源耦合**用例（现 `test_..._bumps_marker_only_after_success` 仅锁既有不变量、对新 bump 门控不判别）；⑦ `--dry-run` 仍写 `candidates_<stamp>.json`（既有、被多源放大 N 倍 persona 调用）——dry-run 后同 stamp 真跑会复用缓存致 radar 静默空转。
>
> **follow-up 待办（续）**：④ stage_radar per-source 失败隔离（try/except per-project + 仅 bump「全部订阅项目成功」的源，解跨源耦合）；⑤ `c["project"] = proj` override；⑥ 补正向 bump + 跨源耦合测试；⑦ dry-run 跳写或独立命名 `candidates_<stamp>.dry.json`。

## 详细设计（供 writing-plans 出实现计划）

### sources.yaml schema

```yaml
sources:
  - name: wechat                              # 现状，本次跑通
    kind: directory
    root: Knowledge/微信
    content_glob: "**/[0-9]*.md"
    exclude_glob: "**/*{审校报告,URL参考列表,文章清单}*.md"
    target_projects: [cc-web-control]         # 缺省=不喂（必须显式声明）
    marker: state/consumed_wechat

  # 投递箱：每个 admission 项目一道 per-project lane（默认模型——不只 ashare，
  # 任何项目都可有自己的手动投递通道；新项目准入时加一道）。一源喂多项目=混喂，禁。
  - name: drop-zone-cc-web-control            # local-file：用户/脚本直接丢文件
    kind: local-file                          # directory 特例，本次即可用
    root: Knowledge/投递箱/cc-web-control
    content_glob: "**/[0-9]*.md"
    target_projects: [cc-web-control]         # 该道只喂 cc-web-control
    marker: state/consumed_dropzone_cc_web_control

  - name: drop-zone-ashare-llm-analyst
    kind: local-file
    root: Knowledge/投递箱/ashare-llm-analyst
    content_glob: "**/[0-9]*.md"
    target_projects: [ashare-llm-analyst]     # 该道只喂 ashare
    marker: state/consumed_dropzone_ashare_llm_analyst

  # 以下三种 fetcher 后续各自做，schema 先就位
  - name: quant-research                      # ① agent + deep-research 搜索
    kind: agent-deepresearch
    root: Knowledge/深研/quant
    fetcher: scripts/fetchers/deepresearch.py
    params: { agent: general-purpose, skill: deep-research, prompts: ["A股量化最新进展…"] }
    target_projects: [ashare-llm-analyst]
    marker: state/consumed_quant_research

  - name: wechat-picked                       # ② 指定微信文章
    kind: wechat-url
    root: Knowledge/微信精选
    fetcher: scripts/fetchers/wechat_url.py
    params: { urls: ["https://mp.weixin.qq.com/s/…"] }
    target_projects: [ashare-llm-analyst]
    marker: state/consumed_wechat_picked

  - name: github-watch                        # ④ 指定 github 仓库
    kind: github-repo
    root: Knowledge/gh-watch
    fetcher: scripts/fetchers/github_repo.py
    params: { repos: ["akfamily/akshare"], watch: [releases, readme] }
    target_projects: [ashare-llm-analyst]
    marker: state/consumed_github_watch
```

### 5 种 source kind 契约

| kind | fetcher 职责 | 输出 | 本次 |
|---|---|---|---|
| `directory` | 无（目录现成） | `YYYYMMDD_*.md` | ✅ 跑通 |
| `local-file` | 无（用户/脚本直接丢文件到 root） | 同上 | ✅ 等同 directory |
| `wechat-url` | 解析指定 mp.weixin URL → md，落 root | 同上 | ⏳ fetcher 后续 |
| `github-repo` | gh API 拉 releases/readme/issues → md，落 root | 同上 | ⏳ fetcher 后续 |
| `agent-deepresearch` | 调 agent + deep-research skill 搜 → md，落 root | 同上 | ⏳ fetcher 后续 |

**统一消费契约**：不论 kind，fetcher 保证产出 `YYYYMMDD_*.md` 到 `source.root`，`discover_today_new` 按 `content_glob` 扫——radar 完全 kind 无关。`local-file` 是 `directory` 的特例（无 fetcher、用户直接丢文件），消费侧代码零分支。

### `discover_today_new` — 零改动

已是 per-source（接 `source` dict、用 `source["root"]/content_glob/exclude_glob/marker`）。多源时 `stage_radar` 对每个 source 各调一次。

### `stage_radar` 改造（核心，伪码）

```python
def stage_radar(args, sources, profiles, stamp) -> dict:
    cand_file = STATE_DIR / f"candidates_{stamp}.json"
    if cand_file.is_file() and not args.force:
        return json.loads(cand_file.read_text(...))           # 复用（不变）

    # 1) 每源 discover + 各自 bump marker（dry_run 不 bump）
    per_source_new: dict[str, list[Path]] = {}
    for src in sources:                                        # ← 去掉 sources[0] 硬编码
        marker = read_marker(src)
        today_new = discover_today_new(src, marker, args.limit)
        per_source_new[src["name"]] = today_new
        log(f"[radar] source={src['name']} kind={src.get('kind','directory')} "
            f"marker={marker} 今日新={len(today_new)}")
        if today_new and not args.dry_run:
            bump_marker(src, max(re.match(r"(\d{8})", p.name).group(1) for p in today_new))

    # 2) 按项目聚合：project → [(source, [files])]
    proj_files: dict[str, list[tuple[str, list[Path]]]] = {}
    for src in sources:
        targets = src.get("target_projects") or []              # 缺省=不喂（grilling 定）
        for proj in targets:
            if proj in profiles:
                proj_files.setdefault(proj, []).append((src["name"], per_source_new[src["name"]]))

    # 3) 按项目调 radar（只有订阅到新文件的项目才调）
    dedup = fetch_dedup_list(profiles)
    all_candidates, stats = [], {}
    for proj, src_files in proj_files.items():
        flat = [f for _, fs in src_files for f in fs]
        if not flat:
            continue                                            # ← 无订阅文件 → 不调（省）
        payload, meta = run_persona("pa-radar",
            radar_prompt(proj, flat, profiles[proj], dedup.get(proj, [])), "radar", f"radar-{proj}")
        for c in payload.get("candidates", []):
            c.setdefault("project", proj)
            c.setdefault("source", _source_of(c, src_files))    # ← 追溯源
            all_candidates.append(c)
        stats[proj] = {**payload.get("stats", {}), "cost": meta["cost"], "turns": meta["turns"]}

    out = {"candidates": all_candidates, "today_new_count": sum(len(v) for v in per_source_new.values()),
           "per_source": {k: len(v) for k, v in per_source_new.items()}, "stats": stats}
    cand_file.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
```

### `radar_prompt` 改 per-project 签名

```python
def radar_prompt(project: str, today_new: list[Path], prof: dict, dedup_items: list[str]) -> str:
    files = "\n".join(f"- {p.relative_to(VAULT_ROOT)}" for p in today_new)
    ms = prof.get("match_surface", {})
    surf = f"- {project}: one_liner=\"{ms.get('one_liner','')}\" keywords={ms.get('keywords',[])}"
    dd_block = "\n".join(dedup_items) if dedup_items else "（无未关闭 PR / 在途 PRD）"
    return f"""今日新内容文件（共 {len(today_new)} 篇，逐篇 Read 后抽技术信号，只针对项目【{project}】）：
{files}

白名单项目 match_surface：
{surf}

去重清单（命中则丢弃）：
{dd_block}

按 persona 输出契约：只吐一行 JSON（candidates 数组，relevance<0.5 丢弃，每条带 source_path）。"""
```

### 测试（AAA）

- `test_discover_multi_source`：两条 source（wechat directory + dropzone local-file），各自 marker 独立、各自 discover。
- `test_target_projects_routes_files`：wechat `target_projects:[cc-web-control]`、dropzone `[ashare]` → cc-web-control 只收到 wechat 文件、ashare 只收到 dropzone 文件。
- `test_target_projects_default_none`：source 无 `target_projects` → 不喂任何项目（targets=[]，订阅空，该项目 0 文件不调 radar）。
- `test_project_with_no_files_skips_radar`：ashare 订阅的源今日 0 新文件 → 该项目不调 radar（省）。
- `test_marker_per_source_bump`：两源各自按自己文件名最大日期 bump，互不干扰。
- `test_radar_prompt_per_project`：prompt 只含该项目 match_surface + 该项目订阅文件。
- `test_candidate_carries_source`：candidate 带 `source` 字段追溯源。
- `test_sources0_hardcode_removed`：`sources` 顺序与 `sources[0]` 无关，全部 source 都被遍历。
