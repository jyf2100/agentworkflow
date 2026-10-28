# add-acceptance-spec-confirmation — tasks

## 1. pa 侧：critic 第四态 + spec-author

- [ ] 1.1 `.claude/agents/pa-prd-critic.md`：判据段四态化——「有据但验收不可执行」→ `needs_metrics`（drop 收窄：无据/无关/自相矛盾）；补 needs_metrics 输出契约（verdict + summary）
- [ ] 1.2 新 persona `.claude/agents/pa-spec-author.md`：输入 PRD 路径 + profile；输出一行 JSON `{"metrics":[{"id","metric","from"}...],"refused"}`；Read-only；指标纪律 = 可断言（色值/键名/选择器/行为动词）、逐条注 PRD 原文依据、≤8 条
- [ ] 1.3 `run_daily.py`：critic 判定消费面——`needs_metrics` → 调 spec-author（persona_call 管道）→ 指标清单写事件 `metrics_proposed`（data 含 metrics + 原文 sha）→ 该 PRD 不进 dispatch；persona 调用失败 fail-safe：降级 drop + summary 注原因（绝不半拟指标入队）
- [ ] 1.4 pa 单测：needs_metrics 路径（spec-author 成功产事件/失败降级 drop/不 dispatch）；pass/drop/revise 三既有路径零回归（#74 原文 fixture 应 needs_metrics、#75 原文 fixture 应 pass——真机回归判据入测试）

## 2. hub 侧：确认状态机 + 事件消费

- [ ] 2.1 `hub/db.py`：新表 `confirmation_requests`（D4 schema；CRUD：get_by_item / insert / decide（状态机校验）/ expire_sweep）；建表挂既有 schema 初始化路径（重启幂等）
- [ ] 2.2 `hub/lifecycle.py`：`_forward_events` 消费 `metrics_proposed`——格式化清单（每条一行 ≤8 截断）post 频道 @发起人 + 用法行 + 建行 requested（事件重复到达幂等：行已存在跳过）
- [ ] 2.3 `hub/mm_intake.py`：`confirm` / `reject` / `metrics` 三子命令——发起人 user_id 判据（非发起人 ephemeral 拒）、非 requested 幂等回执、confirm = 指标并入 PRD 快照新节 → 既有 wrap/落盘/入队路径复用（新 sha 新 deliver_key）、reject = 置态 + 频道回执、metrics = 补充并入快照重新入队
- [ ] 2.4 `hub/db.py` expire_sweep 挂 reconcile tick（>72h requested → expired + 频道回执）；`hub/cli.py` check-intake 增确认面步骤（待决行数/最老行龄）
- [ ] 2.5 hub 单测：四态状态机全转移（requested→confirmed/rejected/expired；非法转移拒）/ 事件幂等建行 / confirm 并节新 sha 入队 / 非发起人拒 / 三子命令回执形态 / 超时扫描——FakeMMClient + tmp data_root 全离线

## 3. 真机联调与文档

- [ ] 3.1 真机坐实：重放 #74 模糊原文 → 频道收指标清单 → rocju `/pa confirm` → 新 run critic 直接 pass → dispatch；另一轮 `/pa reject` 与超时形态抽查
- [ ] 3.2 RUNBOOK 新节：确认环生命周期（needs_metrics→清单→confirm/reject/metrics→超时）+ 三子命令用法 + 故障形态（spec-author 失败降级 drop 的回执语义）；`bash scripts/quality.sh` + `bash hub/quality.sh` 双绿
