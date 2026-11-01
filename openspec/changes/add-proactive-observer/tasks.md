## 0. 勘察（实装前置）

- [x] 0.1 load_profiles 对未知键容忍度核验（proactive 块零 pa 影响）；gh api actions/runs
  端点形态与 token scope 实证（GITHUB_TOKEN 对公开仓 runs 可读性）
- [x] 0.2 default_branch 获取路径（repo meta 缓存）；event_poller 拉取形态复用点确认

## 1. 实现

- [x] 1.1 `hub/proactive_observer.py`：声明装载（容错/规则键校验 fail loud/mode auto 降级 warn）
  + daemon 第七低频槽 `HUB_OBSERVER_TICKS`（config 同型校验）
- [x] 1.2 T1/T2 判据（D2 参数面）+ gh api 三态（fake client 注入接缝，event_poller 同模式）
- [x] 1.3 ShadowProposal 落盘（D3 schema/幂等 hash/streak 锚定）+ digest 影子节（计数+top1+采纳比）
- [x] 1.4 hub.env.example 键 + RUNBOOK §26（启用块教学 + 采纳关联教学 + 与 event-sources 边界表）

## 2. 测试与验证

- [x] 2.1 单测：声明容错/T1 连红判据（含 streak 锚幂等）/T2 速率判据（基线×K+绝对下限）/
  三态 UNKNOWN 跳轮/提案 schema 断言/auto 降级 warn/digest 节
- [ ] 2.2 真机：白名单仓（jyf2100/agentworkflow 自己）注入 CI 红（手动 fail 一次 workflow ×2）
  → 提案落盘 → 日报节可见 → evidence 关联试一次采纳链

## 实施证据（2026-11-01）

- 0.1：load_profiles 只读 admission/name（run_daily.py:262-269 实证）——proactive 块零 pa 影响；actions/runs 端点形态实证（本仓 3 runs：[id,status,conclusion,head_branch]）；gh Bearer 可读
- 0.2：default_branch 经 repo meta（进程内缓存）；event_poller urllib 形态复用（_get 同模式）
- 1.1：hub/proactive_observer.py 声明装载（规则键 fail loud/mode auto 降级 warn）+ config OBSERVER_TICKS（≥1）+ daemon 第七槽**直调**（绕开 lifecycle 纠缠文件——import proactive_observer 于 daemon）
- 1.2：T1 streak 锚判据（连败段最老 run id——增长锚不变幂等/恢复新锚）+ T2 速率×下限 + 三态 UNKNOWN 零提案（_get 元组解包——首版 bug 单测抓住）
- 1.3：ShadowProposal 落盘（D3 schema 全字段+采纳教学在文内）+ digest 影子节（现存/已采纳扫描 PRD frontmatter 关联）
- 1.4：hub.env.example HUB_OBSERVER_TICKS 注记 + RUNBOOK §26（启用块/判据/边界表/采纳教学）
- 2.1：test_proactive_observer 6 测试（声明容错/auto 降级/规则键拒/T1 锚幂等+恢复新锚+下限+无CI/T2 速率+下限/UNKNOWN 隔离）+ daemon 槽 + config 键；hub/quality.sh 全绿
- 2.2 真机：本仓 main 天然 CI 连红（3 连败实测）——部署后 one-shot 即真验证，见部署记录
