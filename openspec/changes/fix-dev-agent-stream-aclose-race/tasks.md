# Tasks — fix-dev-agent-stream-aclose-race

## 1. 复现根因

- [ ] 1.1 端到端最小复现：构造 dev loop（`prompt_stream` + 多轮 tool，含需审批的 `npm test`），跑 `dev-agent.py`，捕获 `AbortError: Stream closed` + `aclose(): asynchronous generator is already running`
- [ ] 1.2 单元复现（更轻）：mock SDK 消费 `prompt_stream` + 中途 aclose，断言竞态
- [ ] 1.3 确认根因层（`prompt_stream` aclose vs SDK 内部 vs 其他），落证据到 design §3

## 2. 修复

- [ ] 2.1 据 §1.3 选方案（A `prompt_stream` 改 aiterator class / B stream 守护 / C 原生 streaming 迁移）
- [ ] 2.2 实现修复（最小改动，不弱化 `can_use_tool`/`decide_bash` 权限闸）
- [ ] 2.3 复现测试 RED → GREEN

## 3. 验证

- [ ] 3.1 端到端：重跑一份 dev loop，dev 能在 worktree 跑 `npm test`（不 AbortError）
- [ ] 3.2 全量 `bash scripts/quality.sh` 绿（compileall + pytest + ruff）
- [ ] 3.3 回归：`verified-dev-execution` / `durable-runtime-*` 既有测试不破

## 4. 规约同步

- [ ] 4.1 评审通过后 `/opsx:sync` delta → main spec
- [ ] 4.2 `/opsx:archive`
