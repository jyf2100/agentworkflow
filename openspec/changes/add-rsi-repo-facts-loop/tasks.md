# add-rsi-repo-facts-loop — tasks

## 1. pa 侧：仓事实候选提取（轨道 2）

- [ ] 1.1 `learning_memory_schema.py`：新 schema 类 `repo_fact_candidate`（D2 字段集 + fact_key 机械派生 + fact ≤200 字 + evidence 必填可复核）；不可复核候选转轨道 1 的分类规则
- [ ] 1.2 `learning_memory_reflection.py`：curated evidence bundle 增仓事实提取视角（dev 独白 log / off_track 判定 / verify-report 中的「仓能力探测」证据段）；提取 prompt 分轨指令（事实 vs 模式）
- [ ] 1.3 `run_daily.py`：终态钩子——仓事实候选产出后写事件 `repo_fact_proposed`（event_outlet 增事件类，data 含候选全文 + fact_key）；轨道 1 路径零触碰
- [ ] 1.4 上游注入四点（机械拼接零 LLM）：`_wrap_mm_prd` 与 radar-PRD prompt 尾「仓事实」节；critic prompt 附节 + 判据文本增冲突理由（drop 引 fact key，B 环落地后 needs_metrics 分支预留）；test-author prompt 附节；spec-author prompt 附节（B 环实施时对接）
- [ ] 1.5 profile 读面：`repo_facts` 节解析（YAML → 结构化注入源；条数上限 12 溢出 warn）

## 2. hub 侧：人审环

- [ ] 2.1 `hub/db.py`：`fact_requests` 表（id/project/fact_key/payload_json/status CHECK(requested,confirmed,rejected,expired)/created_at/decided_at）+ CRUD + expire_sweep（72h）
- [ ] 2.2 `hub/lifecycle.py`：`_forward_events` 消费 `repo_fact_proposed` → 频道 @admin post（fact 全文 + evidence 摘要 + 用法行）+ 建行（事件幂等）
- [ ] 2.3 `hub/mm_intake.py`：`fact ok|no|merge` 子命令（admin 白名单判据）；ok = 写 profile repo_facts 节（hub 经 data_root 直写 profile 文件，带溯源三字段）+ 置 confirmed + 频道回执；no/merge 同构；非 admin 拒；幂等/过期语义同 B 环
- [ ] 2.4 `hub/cli.py` check-intake 增仓事实面步骤（待决 fact 数 / profile repo_facts 条数）

## 3. 测试与真机

- [ ] 3.1 pa 单测：分轨提取（#76 fixture → repo_fact_candidate；偶发模式 → 轨道 1 零变化）/ fact_key 派生去重 / 不可复核转轨 / 四注入点机械拼接（repo_facts 空时节缺省）/ critic 冲突判定
- [ ] 3.2 hub 单测：fact_requests 状态机全转移 / 事件幂等建行 / ok 写 profile 溯源完整 / 非 admin 拒 / 72h 过期——FakeMMClient + tmp data_root
- [ ] 3.3 真机坐实：重放 #76 形态需求 → 候选进频道 → `/pa fact ok` → profile repo_facts 落条 → 再发同指标冲突需求 → critic 以冲突理由 drop/转 needs_metrics（B 环就绪时）——RSI 闭环（失败 → 学习 → 上游拦截）一镜到底
- [ ] 3.4 RUNBOOK 新节：仓事实生命周期（候选→人审→注入→人工清理）+ `/pa fact` 用法 + 陈旧事实处理；`bash scripts/quality.sh` + `bash hub/quality.sh` 双绿
