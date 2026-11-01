# Proposal: add-proactive-observer

## Why

pa 今天的一切工作都由**人请求**触发（MM 入队 / fetch 链被动采集）——环境在「喊救命」时
（main 分支 CI 连红 / issue 突增）系统沉默。演进路线 P2-⑧（dsh「主动」特征的 pa 翻译）：
目标仓观察者持续盯白名单仓的机械可判异常，产生**影子提案**（「本来会做什么」的结构化记录）。
影子先行是该仓既定文化（shadow parity / fail-open 观测面 / dsh 原型同样默认 observe）——
两个独立判断同一答案。

与 add-event-sources 的本质区别（易混淆，先钉死）：event-sources 收**人的动作**（评论/issue，
授权面=作者白名单，产出进语义链）；observer 收**仓的状态信号**（CI 结果/issue 速率，授权面=
无——它不进任何执行链，只产提案）。前者是 Trigger，后者是 Watchdog 的提议面。

## What Changes

1. **hub/proactive_observer.py**：daemon 第七低频槽（`HUB_OBSERVER_TICKS` 缺省 720 ≈ 每小时）
   按项目 profiles `proactive:` 声明轮询白名单仓两类信号（gh api 三态）：
   - **T1 CI 连红**：默认分支最近 N 次 workflow runs 全败（N 缺省 2，`gh api actions/runs`）
   - **T2 issue 激增**：opened 速率 > 7d 基线 × K（K 缺省 3，events 计数——复用 event_poller 的拉取形态）
2. **判据全机械零 LLM**（v1 提案 = 证据拼接模板，语义生成列 v2——影子模式的价值主要在先收集
   命中率数据，不在提案文采）。
3. **ShadowProposal 落盘**：`data_root/<project>/shadow_proposals/YYYYMMDD_<sp8>.md`（幂等去重 =
   文件名 hash(信号键)）；frontmatter + 证据 + 拟议动作模板。
4. **报告面**：daily_digest 增「影子提案」节（计数 + top 摘要）；MM 不即时推（观察面防噪音——
   提案是数据不是告警）。
5. **policy 声明**：profiles `proactive: {mode: observe|auto, watch_repos: [...], rules: {...}}`；
   **v1 只实现 observe**——auto 键解析但拒绝（spec 声明 + stderr 提示走后续 change）。
6. **采纳追踪**：提案 id 出现在后续 PRD frontmatter `source: shadow-proposal:<id>` 即命中
   （人工 /pa run 时 evidence 填提案 id——RUNBOOK 教学；零 pa 链改动）。

## Non-Goals

- **auto 模式**（提案直进 PRD 链）——后续 change：需预算闸（每项目每日主动 run 上限）+ 置信门
  + 全链 critic 不豁免三重前置，本 change 只留 policy 键位。
- 不做语义提案生成（LLM 拟议动作/聚类）——v2，等命中率数据证明机械模板不够用。
- 不做依赖告警（dependabot alerts 需 admin scope，权限面另案）。
- 不做跨仓信号（单仓信号只进其 target_projects）。
- 与 add-rsi-repo-facts-loop 的协同（观察发现的仓事实走轨道 2）是 v2——v1 提案证据里标
  `repo_fact_candidate: true` 留接口。
