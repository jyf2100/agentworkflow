# agent-verify-injection

## Why

独立验证的执行面是纯机械 subprocess（`independent_verify` 双轨：Node → `npm ci + npm test`；非 Node → 重放 dev 自报 `test_cmd`，没报就跳过），有两层根因性问题：

1. **测试面同盲**：非 Node 轨的命令源是 dev 自己挑的测试面——两层验证共享同一盲区，独立验证失去独立性（harden-verify-test-scope 已立案指出，0/9 未施）。更深一层：验收测试本身由 dev 编写，「自己考自己」——测试面应当从 PRD 验收标准**外部派生**，成为控制面工件，dev 与 verify 都只消费注入件。
2. **纯机械执行在真实环境跑不稳**：cron 极简 PATH 下裸 `bash -c` 对陌生工具链（Go/Rust/浏览器二进制/服务依赖）环境脆弱，基建红频繁且无人归因。用户裁决（2026-10-26）：验证执行**不能纯机械**——执行面至少走 claude code / codex 之一；但 agent 必须是**纯执行器**：**环境 / 需求 / 测试用例三个输入全部从外部注入**，agent 不自选命令、不自编测试、不改判据。

## What Changes

- **验收测试成为控制面工件**（新）：critic pass 后由新 persona（pa-test-author）从 PRD 验收点（稳定 id）编写可执行验收测试（harness 由 profile 声明），content-addressed 存 artifact store，digest 随 journal 事件落账。
- **双注入**（新）：dev loop 注入 `--acceptance-tests`（**TDD 目标引导（软）**——管线级 TDD 实现目标，注入件 dev 可读不可改（digest 对账）；dev 发布门不机械强制执行注入套件，验收点独立判定完全由 verify 侧承担，见 design D10）+ verify worktree 注入（独立门：编排器机械拷入，agent 跑完 sha256 复核；**同一次 dispatch iteration 两注入点消费同一 digest 版本**，重编写新版本只对下一次 dispatch 生效，见 design D9）。
- **verify 执行面 agent 化**：headless claude 进 verify worktree，任务 = 按注入环境 spec 装环境 + 跑注入验收测试 + 项目全量门；围栏 = 只准动环境/依赖，**不准碰源码与一切测试文件**。Node / 非 Node **全轨统一**（用户裁决）。flag-gated（`PA_VERIFY_AGENT`，默认 off = 现行为零变更）。
- **机械锚定三件套**（判定权不进 agent）：① 退出码取证——编排器捕获 stream-json 原始流，机械扫出注入测试命令的 tool_result exit code；② tracked 文件不变性——`git diff` 为空，改源码/改项目测试即判废；③ 注入件 digest 复核——跑完重算 sha256 对账，偷改注入测试即判废。
- **吸收 harden-verify-test-scope**（用户裁决）：`verify_test_cmd` 优先序（profile 声明 > dev 上报 hint > 跳过+注明）成为 agent 执行的命令声明源之一；其 change 目录与未施 delta 归并进本题。
- 不变：pa-verify 三维度审查（证实输入换成注入测试结果 + 项目全量门结果）；「无新绿不发布」测试门语义（绿的证据从 subprocess 退出码升级为「transcript 退出码 + 双机械锚」）；ADR-0001 控制面/目标面分离；ADR-0002 项目自治（env spec 与 verify_test_cmd 均为项目 onboard 自声明）。

## Capabilities

### New Capabilities

- `acceptance-test-injection`: PRD 验收点 → 外部编写的可执行验收测试（控制面工件：编写时机/persona/存储/digest 账）+ 双注入面（dev loop / verify worktree）+ 注入件对账不变式。

### Modified Capabilities

- `verified-dev-execution`: 独立验证的执行载体由 subprocess 重放改为 **agent 执行 + 机械锚定**（执行/判定分离）；测试命令声明源改为注入优先序（profile `verify_test_cmd` > dev hint > 跳过）；验收测试从「dev 自写自证」升级为「外部工件注入」。

## Impact

- **代码（改）**：`run_daily.py` `independent_verify`（agent 调用 + 三锚 + 优先序吸收）；`dev-agent.py`（`--acceptance-tests` 注入面）；`feature_flags.py`（`PA_VERIFY_AGENT`）；`graph_pa_verify`/`graph_pa_nodes` 对称；profile schema（`verify_test_cmd` / env spec 字段）。
- **代码（新）**：`scripts/acceptance_tests.py`（编写调度 + 存储 + 注入件构造）；`.claude/agents/pa-test-author.md`（persona）；`scripts/verify_agent.py`（headless 会话 + stream 捕获 + 锚点提取）。
- **既立案 change 处置**：`harden-verify-test-scope`（0/9）吸收归并——delta spec 改写进本题，原目录归档（tasks 内含收尾步骤）。
- **成本**：每次 verify 从零 LLM 变为一次 agent 会话（token 上浮）；绿路径不再零 token，换取环境自适应与测试面独立。
- **风险**：agent 越权改文件（锚②③拦截，fail-safe 判废）；验收测试与项目意图错位（persona 编写依据 PRD 验收点 + critic 已过闸，错位率由 pa-verify 语义审查兜底）。
