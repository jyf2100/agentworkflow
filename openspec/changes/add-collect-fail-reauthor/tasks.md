## 1. 重写触发（collect fail → superseding re-author）

- [ ] 1.1 `_test_author_prompt` 扩可选参数 `evidence: dict | None`（默认 None = 现有调用零变化）：失败文件名 + collect 错误签名（截断）+ 「上一版因 dispatch 期 collect 失败被扣留，规避同类结构性错误」上下文块
- [ ] 1.2 run_daily.py collect fail 分支（`_suite_snap = None` 处）追加：journal 推数该 PRD `acceptance_reauthor` 事件（<1 时）→ 调 pa-test-author 重写（evidence 注入）→ 第一层自检 + 覆盖闭合照走 → `store_suite(supersedes=旧digest)` → journal `acceptance_reauthor`（旧/新 digest、`confirmed_by: "collect_probe"`、证据摘要）
- [ ] 1.3 重写调用 fail-open：author 超时/异常/产物自检不过 → 按未重写处理（journal 记失败原因，本 iteration 照旧扣留跑完，不崩 dispatch）
- [ ] 1.4 熔断：推数 ≥1 → 不重写，journal `acceptance_reauthor_exhausted`；report 段核实 known issue 落点（PRD 记「验收面退化为 PRD 文本判读」可见条目）

## 2. 测试

- [ ] 2.1 单测：mock collect fail + mock author → 断言 `store_suite` 收到 `supersedes=旧digest`、journal 有 `acceptance_reauthor`（confirmed_by 正确）
- [ ] 2.2 单测：journal 已有 1 次 `acceptance_reauthor` + 再 collect fail → 不重写、记 `acceptance_reauthor_exhausted`、无第二次 store
- [ ] 2.3 单测：重写产物过 supersedes 链后 `latest_suite` 指新 digest（下轮 dispatch 消费面）
- [ ] 2.4 单测：author 异常 → fail-open（dispatch 不崩，扣留语义保持）
- [ ] 2.5 双轨共享验证：grep graph_pa_*.py 无独立 collect/reauthor 接线点（聚合 node 直调 per-PRD 函数单点继承）；quality.sh 全绿坐实

## 3. 质量门与收尾

- [ ] 3.1 `cd Projects/项目推进流水线 && bash scripts/quality.sh` 全绿（compileall + pytest + ruff）
- [ ] 3.2 对照 delta 自查：两个 scenario（evidence-carrying re-author + defective-oracle 自动确认通道）逐句有对应实现/测试路径
- [ ] 3.3 git 线性提交（实现 1-2 commit + openspec 工件 1 commit），commit message 引 change 名；提交前 `git status -sb` 确认并发会话工作区稳定（hub/ 脏文件不属本 change，不混提）
