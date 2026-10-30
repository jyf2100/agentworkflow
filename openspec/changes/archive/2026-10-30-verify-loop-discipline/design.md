# design — verify-loop-discipline

## 决策 D1：同分支累积 vs 分支级隔离（A4）

**现状**：run_daily L1258 round≥2 `--base` = 上轮 dev 分支 + dev-agent 每轮新 stamp 新分支 = 每轮独立分支，round2 改坏不污染 round1（分支级隔离），可整分支回滚。

**问题**：每轮新分支 → 每轮新 PR（A 根因）。

**选项**：
- (a) **同分支累积**（本 change 选）：branch / worktree 跟 slug，verify revise 各轮在同一分支追加 commits，同一 PR 自动更新。失去分支级隔离，退化为 commit 级回滚（`git revert` 单 commit / 整 PR reset 到 round1 tip）。
- (b) 分支级隔离 + PR 幂等：保留每轮新分支，但 dev-agent 开 PR 前查同 slug 已有 open PR → 关旧开新 / 或只保留最新。保留隔离但 PR 编号变（仍有「多 PR」观感，且关旧开新是 outward 副作用）。
- (c) 强制单 PR + 拒绝 round2 新分支：round2 复用 round1 分支（base = round1 tip），不开新 PR。

**选 (a) 的理由**：用户原则「PR 跟需求，需求不变 PR 不变」直接判 (a)。GitHub 原生支持同 head push 新 commits → 已 open PR 自动更新，零额外 outward 副作用。(b) 的关旧开新仍是多 PR + outward。(c) 是 (a) 的子集（同分支）。

**代价**：commit 级回滚粒度粗于分支级。缓解：round 边界 commit 加 marker（`pa-dev: round N`），回滚可定位到 round。

## 决策 D2：independent_verify 的定位（B2）

**现状**：independent_verify 跑 `npm ci + npm test`（Node）或重放 dev test_cmd（Python），产物 test_rc / test_log 喂 pa-verify。

**问题**：① npm ci 与 dev loop 自治测试（dev 自选 npm test，宽松）标准不一致 → 假阴性；② npm ci 失败（基建）被当 verify 红 → 无意义 revise。

**选项**：
- (a) **保留 independent_verify，基建 / 测试 / 语义三分**（本 change 选）：install_rc 失败 = UNKNOWN 升人工（不 revise）；test_rc 失败 = 代码层，喂 pa-verify 判 revise；pa-verify 综合三维度（测试只是证实输入之一）。
- (b) 去掉 independent_verify，测试完全信任 dev loop 发布门：pa-verify 只做三维度审核，不重跑测试。
- (c) 对齐 install 标准（dev loop 也 npm ci）：治标，不治本（pa-verify 仍被 test_rc 绑架，且 dev loop npm ci 可能更易红）。

**选 (a) 的理由**：(b) 失去独立验证（dev loop 自欺绿无交叉验证，违 fail-safe）；(c) 治标不治本（用户已否决「对齐命令治本」）。(a) 保留独立验证价值（交叉证伪 dev loop 假绿）同时区分基建 vs 代码，符合 fail-safe 三态。install 失败升人工 = 我们不知代码能否跑，不臆断。

## 决策 D3：pa-verify 三方审核判定一致性（B1）

**风险**：pa-verify 从「test 红绿」升级到「三方审核三维度（功能对齐 / 开发遗漏 / 质量达标）」，verdict 一致性下降（LLM 对质量硬伤的判断比 test_rc 红绿主观）→ 可能判红过严 → 回环依旧甚至加剧。

**缓解**：
- pa-verify persona 明确「默认怀疑但不苛刻」基线（沿用现有「半成品可能有价值，drop 留给编排器」）；
- canary 校准 verdict 分布（pass / revise 比率与改造前 baseline 对比，revise 率暴涨 → persona 调参）；
- 三维度按项目类型 profile 裁剪（前端项目重 UX / 响应式，后端重安全 / 性能 / 兼容）—— 避免对纯后端仓要求 UX 设计稿；
- 锚点纪律（B3）：功能对齐 / 遗漏锚 PRD 验收清单，质量硬伤锚 diff 行——每个 revise 必须带可定位锚点，约束 LLM 主观发散。

**OQ1（open question，评审定）**：三维度是否全量每轮审，还是 test 绿时轻审（防跑题 sanity）+ test 红时全量？倾向后者（绿轻红重），与现有「green-path quick sanity / revise-path exhaustive」对称。**r2 锁定**：base spec Bundle-scoped review coverage（L110/L117-125）已确立「per-criterion coverage 只在 revise path，green path quick-confirm」——OQ1 答案 = 对齐 base，绿轻红重是既有纪律非新发明；scope requirement scenario「Green-path review carries no per-criterion coverage accounting」显式对齐。

**spec / persona 分工（r2 R2-B 纪律收敛）**：verified-dev-execution spec 的 scope requirement **只断言编排器侧可测不变式**（context 含三锚源 / 不机械映射 `test_rc→verdict` / PRD 验收点带稳定 id / 维度按 profile 裁剪 / 绿轻红重对齐 Bundle-scoped / 两类锚点正交于 Line-anchored）；**三维度综合判定逻辑**（绿但任一维度阻塞缺陷→revise、三维度全过→pass、默认怀疑但不苛刻基线、feedback 锚点类型选择、绿轻红重深度）落 `.claude/agents/pa-verify.md`（task B1）。判据 = base spec L91 范式「the structured anchor field is the machine-asserted contract; persona prose compliance is design guidance, not a scenario THEN」。

## ADR-0002 边界澄清：三方独立审核与项目自审并行（B1 合法性）

**张力**：ADR-0002《投递契约》把「文件范围、设计、代码质量、测试、review/回归流程」划归「项目自治」，控制面「独立验证闸」原文定义为「验项目自报的绿是否属实……不是 scope 规定」。B1 把 pa-verify 从「验绿」扩到「审功能对齐 / 开发遗漏 / 质量达标」，字面看触及了 ADR 划给项目的「质量 / review」域。

**澄清（B1 不违 ADR-0002）**：

1. **两道闸并行，不替代**。ADR-0002 的「项目自治（质量 / review 流程）」指目标仓**开发期内部**的 review——项目自己怎么 review 自己的代码、内建质量。B1 的 pa-verify 是控制面**发布前**的独立三方审核，定位是 full-auto 下的一道独立兜底（ADR-0002 本就为 full-auto 留了「独立验证闸」方案 A）。两者并行：项目自审管「开发期质量内建」，控制面三方审管「发布前独立把关」——B1 不替代、不推翻项目 review，只在其外加一道。

2. **验证 ≠ 干预**。ADR-0002 区分「验证」（验已产出物）与「scope 规定」（替项目决定怎么开发）。pa-verify 审的是**已产出的 PR diff**（对齐需求没？遗漏没？质量硬伤没？），不达标 → 打回让项目自己改（项目仍自治「怎么改」）。这正是「验证」非「干预」：verify 不告诉项目「你应该这么写」，只判「这个 PR 是否达标」，不达标把锚点反馈给 dev loop 增量重做。与 ADR「独立验证闸是验证非干预」一致。

3. **扩的是验证内容，不是验证性质**。「验绿」→「验交付物（对齐/遗漏/质量）」扩的是**审什么**，不是**审的性质**（仍是发布前独立验证，仍是非干预）。ADR-0002 留「独立验证闸」时，full-auto 下光验绿不足以兜底（假绿——测试绿但没解决需求 / 有安全洞），扩到验交付物是「独立验证闸」的应有之义，不越 ADR 边界。

**结论**：B1 不需要新 ADR 授权。本 change 在 spec requirement 里明确「三方独立审核（与项目自审并行）」的定位，persona 正文显式声明「不替代项目自审核、只做发布前独立把关」，即落在 ADR-0002「独立验证闸」的合法扩展内。若 r2/r3 评审认为字面张力仍需正式消解，再开 ADR 修订（独立 follow-up）。

## 决策 D4：dev-agent 开 PR 幂等 vs PR 收归 reconcile（A2）

**现状**：dev-agent.py L974 开 PR（body「自治 dev agent 产出...验证闸已绿」）；run_daily reconcile_pr（L2536）是另一套模板（interrupted / 非 interrupted），只在 verify 闭环收尾 / 兜底开。

**选项**：
- (a) **dev-agent 开 PR 幂等**（本 change 选）：L974 加 `_lookup_pr(branch)`，已有 open PR 则只 push。开 PR 仍在 dev-agent（目标面执行器）。
- (b) PR 收归控制面 reconcile：dev-agent 只 commit + push，不开 PR；PR 由 run_daily reconcile 统一开（一次）。更合控制面 / 目标面分工（PR 是控制面对「需求状态」的对外表达）。

**选 (a) 的理由**：(a) 改动小（dev-agent 加幂等检查），不破坏现有「dev-agent 判绿即开 PR」流。(b) 更纯粹但改动大（dev-agent 去开 PR + reconcile 重写为唯一开 PR 点 + 跨 round PR 状态管理）。

**OQ2（open question，评审定）**：(b) 是否作 follow-up（PR 开启收归控制面）。本期范围选 (a) 最小治本。

## 不变式（贯穿）

- dev loop 自治测试不变（发布门 TestEvidence 是 dev loop 内部判绿，pa-verify 不替 dev loop 跑测试——测试归 dev loop）。
- fail-safe 三态不变（UNKNOWN 不当成功；install 失败 = UNKNOWN 升人工）。
- journal / reconcile / cutover 纯函数库不变。
- verdict 只在 PersonaNode（若 langgraph 已合，check_boundary lint 守）。
