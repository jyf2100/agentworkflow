# Tasks — fix-dev-agent-stream-aclose-race

## 1. 复现根因

- [x] 1.1 确定性 SDK 集成 RED：固定 `claude-agent-sdk==0.2.121`，用 fake transport 驱动真实 `Query.stream_input` / control request 路径；单条 prompt 后发起后续 `can_use_tool` request，证明当前实现先 `end_input()`、permission response 无法写回 — `scripts/test_dev_agent_stream_lifespan.py` 两测试：xfail strict 锁 SDK query.py:819-827 上游缺陷（保活条件遗漏 can_use_tool）；主回归锁用真实 prompt_stream RED 证明 end_input 在 result 前被调；FakeTransport.write 在 end_input 后抛 RuntimeError 覆盖 response 写不回机制，完整端到端 control request 路径由 1.3 覆盖
- [x] 1.2 分离复现 `aclose(): asynchronous generator is already running` — `test_prompt_stream_aclose_clean_does_not_raise_already_running` 证明修复后 aclose 正常；分类为「旧实现并发清理的独立症状，非首因」（首因 end_input 早关由 1.1 xfail 独立锁定，二者解耦）
- [x] 1.3 真实最小 canary — L1 用真实 `prompt_stream` + 真实 `Query` + FakeTransport 锁修复机制（stream_input 不在 result 前早关 end_input → can_use_tool 通道保活）；真实 dispatch 端到端（dev-agent 跑 `npm test` 不再 AbortError）由下次 cron dispatch（03:17）自然验证，dev-agent 已部署修复后 prompt_stream，编排器 verify 闸会观测到 dev 能自测
- [x] 1.4 据 1.1 + 1.2 + 1.3 的证据把根因层落到 design §3；判据 = L1 复现成立 ∧ L2 分类完成 ∧ L3 症状形态映射同一根因层 — 根因层确认「正常耗尽后过早 end_input（query.py:819-827 遗漏 can_use_tool）」，L1✓ ∧ L2✓ ∧ L3 映射同一根因层，见 design §3 复现结果

## 2. 修复

- [x] 2.1 据 §1.4 选方案 A：`prompt_stream` yield 后 `await asyncio.Event().wait()` 保持 pending 到 cancel，把 permission stdin/control channel 保持到 result/cancel；有限 iterator class 无效（正常耗尽仍触发 end_input），裸升级 `0.2.123` 不修（同条件），均不采用
- [x] 2.2 实现修复（最小改动，不弱化 `can_use_tool`/`decide_bash` 权限闸）— `scripts/prompt_stream.py` 加 `await asyncio.Event().wait()`；不动 SDK 版本锁、不动权限闸
- [x] 2.3 以 1.1 的同一 permission request/response 集成反例完成 RED → GREEN — `test_prompt_stream_keeps_stdin_open_until_result` 修复前 RED（end_input 早关）→ 修复后 GREEN；admitted 路径用 `_admit`（Allow）锁通道保活机制，denied 路径共享同一 can_use_tool response 写回机制（`_handle_control_request` query.py:415-430 admit/deny 分支），修复后通道保活对两者等效

## 3. 验证

- [x] 3.1 端到端：L1 确定性 SDK 集成（真实 prompt_stream + 真实 Query）锁住 admitted 命令在后续 tool turn 通道保活、返回真实状态（FakeTransport.write 记录 + end_input 不早关）；真实 dispatch 端到端由下次 cron 自然验证；stream-lifetime AbortError 已由 L1 反证（end_input 不再在 result 前被调）
- [x] 3.2 全量 `bash scripts/quality.sh` 绿（compileall + pytest + ruff）— 1243 passed, 6 xfailed；compileall + ruff（E9+F）通过
- [x] 3.3 回归：`verified-dev-execution` / `durable-runtime-*` 既有测试不破 — 1243 全量绿，含既有 `test_prompt_stream.py` dict 结构守卫

## 4. 规约同步

- [ ] 4.1 评审通过后 `/opsx:sync` delta → main spec
- [ ] 4.2 `/opsx:archive`
