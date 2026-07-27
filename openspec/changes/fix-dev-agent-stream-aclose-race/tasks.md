# Tasks — fix-dev-agent-stream-aclose-race

## 1. 复现根因

- [ ] 1.1 确定性 SDK 集成 RED：固定 `claude-agent-sdk==0.2.121`，用 fake transport 驱动真实 `Query.stream_input` / control request 路径；单条 prompt 后发起后续 `can_use_tool` request，证明当前实现先 `end_input()`、permission response 无法写回
- [ ] 1.2 分离复现 `aclose(): asynchronous generator is already running`，确认它是第一因、独立第二问题或退出清理噪声；不得用人为并发关闭 generator 的必然报错代替 SDK 因果证据
- [ ] 1.3 真实最小 canary：多轮 tool 后运行一个需审批的 Node 测试命令，捕获当前 `AbortError: Stream closed`，并证明目标进程未启动
- [ ] 1.4 据 1.1 + 1.2 + 1.3 的证据把根因层（正常耗尽后过早 `end_input` / concurrent `aclose` / SDK client path / 其他）落到 design §3；判据 = L1 复现成立 ∧ L2 分类完成 ∧ L3 症状形态映射同一根因层，并明确排除其余层

## 2. 修复

- [ ] 2.1 据 §1.4 选方案；候选必须说明如何把 permission stdin/control channel 保持到 result/cancel。有限 iterator class 和裸升级 `0.2.123` 不得在无反例证据时宣称修复
- [ ] 2.2 实现修复（最小改动，不弱化 `can_use_tool`/`decide_bash` 权限闸）
- [ ] 2.3 以 1.1 的同一 permission request/response 集成反例完成 RED → GREEN；另锁 admitted 与 denied 两条权限路径

## 3. 验证

- [ ] 3.1 端到端：重跑一份 dev loop，dev 在后续 tool turn 请求 `npm test`；admitted 时命令实际启动并返回真实 exit status，denied 时返回结构化拒绝，均不出现 stream-lifetime AbortError
- [ ] 3.2 全量 `bash scripts/quality.sh` 绿（compileall + pytest + ruff）
- [ ] 3.3 回归：`verified-dev-execution` / `durable-runtime-*` 既有测试不破

## 4. 规约同步

- [ ] 4.1 评审通过后 `/opsx:sync` delta → main spec
- [ ] 4.2 `/opsx:archive`
