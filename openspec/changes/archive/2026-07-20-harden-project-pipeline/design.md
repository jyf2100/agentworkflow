## Context

流水线由 `run_daily.py` 负责阶段编排，`dev-agent.py` 负责在目标仓 worktree 中驱动 SDK 完成修改、测试、提交和 PR。当前实现已经有独立验证和 GitHub 对账，但仍存在三个跨模块缺口：测试结果没有成为提交前硬门；ADR-0006 指定的控制面标准执行器尚未接入 dispatch；GitHub/远端查询异常被折叠为“未找到”或计数零。仓库也缺少声明式依赖和 CI，导致干净环境中的测试结果不可复现。

变更必须保持控制面与目标面隔离、永不自动 merge、已有 state JSON 可读、cron 可从中断点续跑。目标项目可以是 Node 或 Python 仓，但执行器统一使用 Python，并以目标 worktree 为 cwd。

## Goals / Non-Goals

**Goals:**

- 只有存在可信的最近一次绿色测试结果时才允许 commit、push 和开 PR。
- 由控制面单一 `dev-agent.py` 服务所有已准入项目，消除目标仓脚本副本和 slug 算法漂移。
- 外部真源不可用时以可恢复的 blocked 状态停止危险动作，而不是默认放行。
- 在干净环境中一条命令可安装依赖并运行完整质量检查。
- 通过 fixture 覆盖 Node/Python 目标仓与主要故障分支。

**Non-Goals:**

- 不重构整个 `run_daily.py` 模块布局。
- 不改变 radar、PRD、critic 的语义和 persona 提示词。
- 不自动 merge 或放宽 branch protection、在途 PR 上限等既有刹车。
- 不替目标项目定义业务测试内容；只机械执行并验证其声明的命令。
- 不在本变更中删除历史 state 文件或转换既有报告。

## Decisions

### 1. 测试证明作为发布动作的能力令牌

执行器维护结构化 `TestEvidence`，至少包含命令、退出码、完成时间和当前 HEAD/工作树版本标识。只有证据存在、退出码为零且证据对应当前待提交内容，才允许执行 commit、push 和 PR。测试未运行、无法识别、失败或测试后又发生写操作时，执行器输出非零状态和结构化原因，并保留 worktree 供 dispatch 对账。

选择结构化证据而不是继续扩大输出文本正则，因为文本格式跨 pytest、npm、vitest 等工具不稳定。现有 tool result 配对可以作为采集入口，但发布门只消费结构化状态。

### 2. 控制面执行器为唯一源码，运行时仍贴目标仓

`run_daily.py` 从控制面路径调用 `dev-agent.py`，并把目标 worktree 设置为 cwd。Python runtime 根据 profile 和宿主平台解析；目标仓语言不决定执行器语言。`dev_slugify` 从执行器模块直接导入，dispatch 删除影子实现。

迁移期间可以读取旧 profile，但不再以目标仓脚本是否存在作为准入条件。准入改为显式 readiness 配置和运行时/仓规则可用性检查。

### 3. 外部查询使用 FOUND/NOT_FOUND/UNKNOWN 三态

GitHub PR、远端分支、branch protection 和在途数量查询返回带原因的结构化结果。只有明确 `NOT_FOUND` 或有效计数才能继续。超时、命令缺失、认证失败、非零退出码、无效 JSON 都映射为 `UNKNOWN`。

dispatch 准入遇到 `UNKNOWN` 时记录 `blocked_external_state` 并停止启动 dev agent。对账遇到 `UNKNOWN` 时不删除分支、不补开 PR、不覆盖已有状态，只保留证据供后续重试。这样牺牲外部故障期间的吞吐量，换取幂等与资产安全。

### 4. 状态契约向后兼容扩展

新增字段采用可选形式，例如 `block_reason`、`test_evidence` 和 `external_checks`。报告读取旧记录时继续使用默认值；新状态单列为“外部状态阻塞”或“测试门未通过”，不计入已投递或验证绿。

### 5. 使用 `pyproject.toml` 定义环境和质量入口

运行依赖、可选操作系统集成和开发依赖统一声明。CI 在支持版本的 Python 上安装项目开发依赖，运行编译、pytest 和 ruff。测试不得访问真实 GitHub、SMTP、Keychain 或模型服务；通过 subprocess/SDK adapter 和临时 git fixture 隔离。

## Risks / Trade-offs

- [测试命令未被采集导致更多任务被拦截] → 在灰度期记录未识别命令和证据来源，补齐结构化采集后再移除旧文本 fallback；任何 fallback 也不得把未知判为绿。
- [GitHub 短暂故障降低每日吞吐] → blocked 状态保留明确原因并允许同一 PRD 后续重试；不以重复派发换可用性。
- [控制面执行器依赖目标仓 Python 环境不一致] → 执行器优先使用控制面已验证 runtime，目标仓 conda 配置只注入测试命令 PATH；用 Node/Python fixture 覆盖。
- [导入 `dev-agent.py` 会触发 SDK 依赖或初始化副作用] → 将共享纯函数移到无 SDK 副作用的小模块，或保证模块导入不启动 SDK；由单元测试锁定。
- [状态字段增加影响历史报告] → 读取端使用容错默认值，并用历史 JSON fixture 做兼容测试。

## Migration Plan

1. 先落依赖声明、CI 和现状测试，确保基线稳定。
2. 增加测试证据模型与提交硬门，先在 dry-run/fixture 中验证所有红绿分支。
3. 接入控制面执行器并共享 slug 实现；保留 profile 兼容读取，但停止调用目标仓脚本。
4. 将外部查询逐个改为三态，先覆盖幂等与在途准入，再覆盖对账和清理。
5. 更新报告分类、ADR 和部署文档，运行 `--dispatch-skip-dev` smoke。
6. 对一个白名单项目做真实灰度；绿后扩到其余项目。

回滚时可以恢复上一版本的 dispatch/执行器代码；新增 state 字段不会阻止旧版本读取。已经进入 `blocked_external_state` 的记录不包含远端写操作，无需补偿清理。

## Open Questions

- 测试证据应绑定 commit SHA，还是绑定测试前后的工作树 diff hash；实现阶段应选择能覆盖“测试后继续修改”的方式。
- 控制面 Python runtime 是固定虚拟环境还是 profile 可覆盖；需结合 Linux cron 部署路径确定默认值。
- blocked PRD 是否由下一次日跑自动重试，还是继续遵循当前“待投递不自动重试”策略；本变更默认只记录为可重试，不自动改变重试策略。
