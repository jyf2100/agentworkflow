## Context

dsh 四特征的「主动」：持续观察环境事件，无人 @ 也推进值得处理的事项，不需要时保持安静。
其原型把主动默认设为 observe 影子模式。pa 已有全部基础设施件：轮询槽（六个先例）、gh api
三态（stale_check/event_poller）、语料/提案文件形态（events 增量文件同款幂等模式）、日报聚合面
（daily_digest）。本 change 是这些件的再组合，唯一新本体是 ShadowProposal。

## 本体论声明（先钉概念，防 P2 扩展期漂移）

- **ShadowProposal（影子提案）**：未经请求的**提议记录**（continuant 文件），不是 Trigger、不是
  PRD、不是告警。同一性 = hash(信号键)（repo + 信号类型 + 信号窗口锚），文件名承载、幂等、
  重启安全。范畴：**观察面产物**——绝不进执行链、绝不即时推送（与漂移项的告警范畴不同：
  漂移项=需要人修的错，提案=可选的机会）。
- **主动工作（proactive work）**：授权方向与被动工作相反——被动先授权后工作（确认环），
  主动先观察后**请求**授权（提案 → 人采纳）。auto 模式 = 把「请求」环节交给 policy（显式、
  可审计、可预算），不是取消授权。
- **信号（signal）**：仓的状态事实（CI 结果序列 / issue 速率），非人的动作（与 event-sources
  的 Trigger 范畴互斥——同一 GitHub API 事件流，两个消费面按「actor 是不是人」分流）。

## Decisions

### D1 观察面挂点与预算
hub/proactive_observer.py + daemon 第七低频槽 `HUB_OBSERVER_TICKS`（缺省 720，≥1 校验同型）。
单轮预算：每声明仓至多 2 次 gh api（runs 一页 + events 一页），fail-open 逐仓吞异常不连坐；
三态 UNKNOWN（限流/5xx）跳轮——绝不断源不提案（零误报取向：提错误比不提案更糟——人会
流失信任）。整体槽兜底 try/except（七槽同型）。

### D2 判据定义（全机械，参数进 policy 可覆盖）
- **T1 CI 连红**：`gh api repos/{repo}/actions/runs?branch={default_branch}&per_page=N` 取
  最近 N 次 conclusion——全部 `failure` 且 N ≥ rules.ci_red_streak（缺省 2）→ 命中。无
  workflows 的仓（404 / 空列表）= 无信号（UNKNOWOWN 不提案）。default_branch 取 repo meta
  （缓存进程内）。
- **T2 issue 激增**：复用 event_poller 拉取形态数 IssuesEvent(opened)——滑动窗 24h 计数 >
  7d 日均 × rules.issue_surge_ratio（缺省 3.0）且绝对数 ≥ rules.issue_surge_min（缺省 3，
  防小仓 1→3 即「激增」）→ 命中。基线计算零额外 API（同一 events 分页窗口内自算）。
- 参数缺省在模块常量，profile `proactive.rules` 可覆盖（键名校验 fail loud——配置错启动期爆）。

### D3 ShadowProposal schema（落盘形态）
```markdown
---
project: "cc-web-control"
source: shadow-proposal
id: "sp-a1b2c3d4"            # sp+hash(信号键) 前 8——采纳关联键
repo: "jyf2100/cc-web-control"
signal: "ci_red_streak"       # ci_red_streak | issue_surge
signal_window: "20261101T05-06"  # 命中窗口锚（同信号新窗口=新提案，老提案不覆盖）
evidence: "runs 12345(fail) 12344(fail) branch=main"
proposed_action: "重跑失败的 workflow 并修复 main 分支 CI"   # 机械模板
policy_version: "observe-v1"
repo_fact_candidate: true     # v2 轨道 2 接口（rsi 协同）
created_at: "..."
---
（证据机械拼接段：run 链接 / issue 标题列表截断）
```
幂等：同 (repo, signal, signal_window) 的提案文件已存在 → 跳过（文件名 = YYYYMMDD_sp<hash8>.md，
hash 输入 = repo|signal|window）。**新窗口新提案**（CI 持续红每小时一条直到恢复？——不：窗口
锚含 streak 首 run id，同一 streak 只一条；恢复后新 streak 新提案）。

### D4 报告面（防噪音的三档分离）
| 产物 | 范畴 | 通道 |
|---|---|---|
| 漂移项 | 需要人修的错 | MM 即时 + 去抖 |
| 事件增量 | 进语义链的原料 | 零推送（链路自取） |
| 影子提案 | 可选的机会 | **仅日报聚合**（digest 新节：计数 + 每信号类型 top1 摘要行） |
提案永不即时 MM（dsh「不需要时保持安静」——提案的静默是特性不是缺陷）。

### D5 采纳追踪（命中率 = auto 放量的唯一依据）
人工 `/pa run` 时 evidence 填 `shadow-proposal:sp-a1b2c3d`（RUNBOOK 教学）；hub mm_intake
wrap PRD 时把该 token 透传进 frontmatter `source:`（现状 evidence → frontmatter 已有链）。
**零 pa 链改动**：统计 = grep shadow_proposals 目录 vs 后续 PRD frontmatter（digest 节顺带
报「本周提案 N / 采纳 M」）。收养判定机械（id 精确匹配）。

### D6 policy 声明与 v1 钉死 observe
profiles/<name>.yaml 增 `proactive:` 块（hub 侧自读，pa load_profiles 不认识该键——同
event-sources 的 events 键先例，需核 load_profiles 对未知键的容忍度，任务 0.1）：
```yaml
proactive:
  mode: observe            # v1 唯一合法值；auto = 解析成功但启动 warn + 观察者拒绝产出 auto 行为
  watch_repos: [owner/repo]
  rules: {ci_red_streak: 2, issue_surge_ratio: 3.0, issue_surge_min: 3}
```
mode: auto → stderr warn「auto 未实装（后续 change：预算闸+置信门+全链 critic），降级 observe」
+ 面板/日报标注 policy 预期与实际形态（防「配了 auto 以为在跑」的静默漂移——六层事故同课）。

### D7 与既有概念的四条边界（防 P2 期范畴漂移）
1. vs **event-sources**：actor 是人 → Trigger（进链）；仓状态 → 信号（只提案）。同一 API 两面，
   模块各自拉取不共享缓存（量小，共享是过早抽象）。
2. vs **config-drift-watchdog**：漂移=配置层间分歧（修错）；提案=仓环境机会（做事）。都走
   「文件+日报」但绝不共用 DriftItem（范畴不同报文不同）。
3. vs **stale_check**：镜像陈旧=hub 自身运维；observer=目标仓环境。互不替代。
4. vs **add-rsi-repo-facts-loop**：观察中撞见的结构性仓事实（如「无 CI 基建」）不是提案素材——
   标 repo_fact_candidate 留 v2 走轨道 2（人审 confirm 沉淀 facts sidecar），v1 不实现。

## 状态机（单轮 observer tick）

```
tick 到 → 载声明（容错）→ 逐项目逐仓:
  拉 CI runs ──三态──→ UNKNOWN → 跳轮（记日志）
                    → FOUND → 判 T1 → 命中 → 提案文件（存在即跳）
  拉 events  ──三态──→ 同上 → 判 T2 → 命中 → 提案
digest 节在每日聚合时读提案目录（observer 不自带推送）
```

## Rollout 与回滚

默认零声明零行为（无 proactive 块 = byte-identical）；启用 = profiles 加块（运维动作，不重建
镜像——profiles 是挂载面）。回滚 = 删块。观察数据（提案目录）无清理策略（量级：每仓每小时至多
1 文件，年千级——v2 再议修剪）。

## Metrics（P2 北极星：人的注意力预算）

- **影子命中率** = 采纳提案数 / 提案总数（周窗口）——auto 放量的唯一依据（路线图 P2 度量承诺）。
- 提案信号分布（ci vs surge）、假信号率（提案后 24h 内信号自愈的比例——衡量判据松紧）。

## Open Questions（不阻塞起案）

- issue 激增的「基线」在仓活跃度剧变期（发版周）会失真——v2 自适应基线。
- CI 红 streak 的恢复信号（再绿）是否产出「已自愈」回执关提案——v1 提案静默留存，日报自然
  淡出（无活跃提案即不报）。
