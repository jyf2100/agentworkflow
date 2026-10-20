# repo-admission

## Purpose

Define the admission gate a target repository must pass before the pipeline will dispatch to it: `admit_repo.py` three-state checks (branch protection, test entry `scripts.test`, git commit identity — UNKNOWN blocks, reusing fail-safe-dispatch semantics), an explicit whitelist of permitted platform operations, and the control-plane/target-plane boundary that keeps pipeline code from writing target-repo files directly.

## Requirements
### Requirement: 三态准入检查
admit_repo.py 对目标仓的每项准入检查（branch protection、测试入口 `scripts.test`、git commit 身份）SHALL 返回显式 FOUND / NOT_FOUND / UNKNOWN 结果；UNKNOWN MUST 视为阻断信号并附修复指引，MUST NOT 当作通过或不存在处理（沿用 fail-safe-dispatch 三态语义）。

#### Scenario: protection 状态不可知
- **WHEN** branch protection 查询超时或鉴权失败（UNKNOWN）
- **THEN** admit 输出 blocked 与修复指引，不给出「可准入」结论

#### Scenario: 全项通过
- **WHEN** 三项检查均为 FOUND 且满足准入规则（protection 已启用、测试入口存在、commit 身份已配置）
- **THEN** admit 输出准入通过与后续注入指引

### Requirement: 平台操作白名单
admit_repo.py MAY 经 `gh api` 配置目标仓 branch protection（平台操作，与 dispatch 运行时实查对称）；除此之外 MUST NOT 执行任何写目标仓状态的平台操作。

#### Scenario: 配置 protection
- **WHEN** 同事确认后 admit 经 gh api 为目标仓启用 branch protection
- **THEN** 操作结果以 FOUND/UNKNOWN 显式回报，失败即 UNKNOWN 阻断

### Requirement: 控制面不写目标面
admit_repo.py MUST NOT 写目标仓的任何文件（含 CLAUDE.md、scripts、配置）；缺失项 SHALL 以待确认的指引文本或补丁形式输出，由同事显式应用。

#### Scenario: 目标仓缺 CLAUDE.md
- **WHEN** 目标仓不存在 CLAUDE.md
- **THEN** admit 输出建议内容作为文本供同事自行创建，不直接写文件

