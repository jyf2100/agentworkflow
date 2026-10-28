# add-acceptance-spec-confirmation — tasks

## 1. pa 侧：critic 第四态 + spec-author

- [ ] 1.1 `.claude/agents/pa-prd-critic.md`：判据段四态化——「有据但验收不可执行」→ `needs_metrics`（drop 收窄：无据/无关/自相矛盾）；补 needs_metrics 输出契约（verdict + summary + `reason: no_metrics|fact_conflict` + 可选 `fact_keys: []`——RSI 第二波软出口与 hub 拦截计数复用同名字段）
- [ ] 1.2 新 persona `.claude/agents/pa-spec-author.md`：输入 PRD 路径 + profile；输出一行 JSON `{"metrics":[{"id","metric","from","coverage"}...],"refused"}`；Read-only；指标纪律 = 可断言（色值/键名/选择器/行为动词）、逐条注 PRD 原文依据、≤8 条、**须落在 profile 声明 harness 可执行面内**（超出面标 `requires:<capability>` + 清单尾注基建缺口）、`coverage: proxy` 显式标注代理指标
- [ ] 1.3 `run_daily.py`：critic 判定消费面——`needs_metrics` → 调 spec-author（persona_call 管道）→ 指标清单写事件 `metrics_proposed`（data 含 metrics + 原文 sha + initiator）→ 该 PRD 不进 dispatch；persona 调用失败 fail-safe：降级 drop + summary 注原因（绝不半拟指标入队）；**critic prompt 按来源分叉**（MM 绑定源附四态判据，panel/radar 源保持三态——run_daily 按 item enqueued_by × 绑定存在性判）
- [ ] 1.4 pa 单测：needs_metrics 路径（spec-author 成功产事件/失败降级 drop/不 dispatch）；pass/drop/revise 三既有路径零回归（#74 原文 fixture 应 needs_metrics、#75 原文 fixture 应 pass——真机回归判据入测试）；非 MM 源不附四态判据（radar fixture 仍三态）

## 2. hub 侧：确认状态机 + 事件消费

- [ ] 2.1 `hub/db.py`：新表 `confirmation_requests`（D4 schema + 原始需求 sha 祖先链字段（重入队计数判据）；CRUD：get_by_item / insert / decide（状态机校验）/ expire_sweep）；建表挂既有 schema 初始化路径（重启幂等；CREATE TABLE IF NOT EXISTS + PRAGMA 增列迁移先例在 db.py:22-49/108-117）
- [ ] 2.2 事件消费**三处落点**（缺一整行被静默丢弃——白名单在 hub 侧）：`hub/events.py KNOWN_EVENTS` 增 `metrics_proposed`；`hub/lifecycle.py _event_text` 增该事件分支（@发起人 + confirm/reject/metrics 用法行）；`_forward_events` 增建行路径（**字段映射**：initiator=enqueued_by / channel_id=project_channels 绑定 / prd_text=prd_path 快照；无 initiator 不建行 + warn log；proxy/requires 条目标注进频道文案；事件重复到达幂等：行已存在跳过）
- [ ] 2.3 `hub/mm_intake.py`：`confirm` / `reject` / `metrics` 三子命令——发起人 user_id 判据（非发起人 ephemeral 拒）、非 requested 幂等回执、confirm = 指标并入 PRD 快照新节 → 既有 wrap/落盘/入队路径复用（新 sha 新 deliver_key；**绑定不存在 → 保持 requested + 回执「项目未绑定」**）、reject = 置态 + 频道回执、metrics = 补充并入快照重新入队；**祖先链重入队计数达 3 次 → 回执「请人工改写需求」不再重拟**
- [ ] 2.4 `hub/db.py` expire_sweep 挂 reconcile tick（>72h requested → expired + 频道回执）；**终态 flush**：depends-on `fix-event-outlet-verify-parity` 落地则复用其终态事件补读，否则 `_finish_run` 前自带最小 `read_events` flush（run 早退不丢 metrics_proposed）；`hub/cli.py` check-intake 增确认面步骤（待决行数/最老行龄）
- [ ] 2.5 hub 单测：四态状态机全转移（requested→confirmed/rejected/expired；非法转移拒；**confirm 时未绑定 → 保持 requested**）/ **KNOWN_EVENTS 注册前丢注册后不丢** / 事件幂等建行（confirm 后事件重复到达不重建）/ confirm 并节新 sha 入队 / 非发起人拒 / 三子命令回执形态 / 超时扫描 / 祖先链计数上界——FakeMMClient + tmp data_root 全离线

## 3. 真机联调与文档

- [ ] 3.1 真机坐实：重放 #74 模糊原文 → 频道收指标清单 → rocju `/pa confirm` → 新 run critic 直接 pass → dispatch；另一轮 `/pa reject` 与超时形态抽查；radar 源模糊信号抽查（仍三态、零 metrics_proposed 事件）
- [ ] 3.2 RUNBOOK 新节：确认环生命周期（needs_metrics→清单→confirm/reject/metrics→超时）+ 三子命令用法 + 故障形态（spec-author 失败降级 drop 的回执语义 / 事件未注册死链的排查 / 重入队上界回执）；`bash scripts/quality.sh` + `bash hub/quality.sh` 双绿
