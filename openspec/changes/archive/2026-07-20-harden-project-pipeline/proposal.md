## Why

项目推进流水线已经能够自动生成 PRD、调用开发 agent、验证并创建 PR，但关键安全属性仍依赖 agent 自律或外部服务恰好可用：未确认测试绿也可能提交，GitHub 查询失败会默认放行，同时控制面执行器迁移尚未完成。现在需要把这些隐含假设升级为可测试的机械契约，避免自动运行产生错误 PR、重复派发或不可复现的验证结果。

## What Changes

- 在开发执行器提交、推送和创建 PR 前增加强制的测试绿门；未运行、无法识别或失败的测试均不得进入发布动作。
- 完成 ADR-0006：所有目标项目统一使用控制面维护的 `dev-agent.py`，移除 dispatch 对目标仓内 `scripts/dev-agent.{py,mjs}` 的依赖，并共享唯一的 slug 生成实现。
- 将幂等检查、在途 PR 计数和远端对账改为显式三态结果；关键外部状态未知时停止派发并保留现有分支/产物，不再按“未发现”处理。
- 建立声明式 Python 依赖、统一验证命令和 CI，使单元测试与静态检查在干净环境中可重复运行。
- 同步已被 ADR-0006 修订的架构与部署文档，明确控制面源码归属、目标面执行 cwd 及故障状态含义。

## Capabilities

### New Capabilities

- `verified-dev-execution`: 规定控制面标准执行器的选择、测试结果采集、提交硬门和结果契约。
- `fail-safe-dispatch`: 规定外部真源查询、幂等准入、在途限额、对账和未知状态下的安全行为。
- `reproducible-pipeline-validation`: 规定 Python 依赖声明、统一质量命令、CI 检查和跨语言目标仓 fixture 验证。

### Modified Capabilities

无。仓库当前尚无已发布的 OpenSpec capability。

## Impact

- 核心代码：`Projects/项目推进流水线/scripts/dev-agent.py`、`run_daily.py`。
- 测试与工程配置：现有 pytest 测试、新增执行器/外部故障 fixture、`pyproject.toml`、CI workflow。
- 配置契约：项目 profile 的开发执行器准入字段及 Python runtime 选择方式。
- 状态与报告：新增测试未绿、外部状态未知等可恢复状态；历史 state JSON 的读取需保持向后兼容。
- 文档：ADR-0001/0003/0006、`CONTEXT.md`、Linux 部署说明与运行手册。
