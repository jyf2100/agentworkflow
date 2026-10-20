# prd-injection-console Delta

## ADDED Requirements

### Requirement: 文件注入触发
webapp 的 PRD 上传端点 SHALL 只接受 markdown 文件（不收命令、不收任意参数），落盘后经 subprocess 触发 `run_daily.py --from-stage inject`；注入路径与 CLI 手动注入共享同一实现，MUST NOT 在面板进程内重新实现编排逻辑。

#### Scenario: 上传触发注入
- **WHEN** 同事上传一份合法 PRD markdown
- **THEN** 文件落盘、子进程启动 `--from-stage inject`、journal 留下本次 run 事件，面板返回受理响应

#### Scenario: 拒绝非文件输入
- **WHEN** 请求试图携带命令或非文件载荷
- **THEN** 端点拒绝该请求，不触发任何子进程

### Requirement: run 锁互斥复用
注入触发 SHALL 复用编排器既有 `.run.lock` 互斥；锁被占用时 MUST 返回显式「进行中」响应，MUST NOT 排队或并发启动第二个 run。

#### Scenario: 双重触发
- **WHEN** 一个 run 进行中时收到第二次注入请求
- **THEN** 第二次请求收到「进行中」响应且不产生第二个编排器进程

### Requirement: 只读状态视图
状态视图 SHALL 只读解析既有 `dispatch_*.json` 与报告产物渲染当日终态与 PR 列表；面板 MUST NOT 产生或改写任何 verdict（机械/语义切分不变式）。

#### Scenario: 面板崩溃不影响流水线
- **WHEN** webapp 进程崩溃或被杀
- **THEN** 进行中的编排器 run 不受影响，状态数据仍完整落盘于 state 目录

### Requirement: localhost 安全边界
webapp SHALL 默认 bind `127.0.0.1` 且无鉴权（localhost 单人场景）；MUST NOT 提供远程绑定默认值。

#### Scenario: 默认监听地址
- **WHEN** 同事以默认配置启动 webapp
- **THEN** 服务仅监听 127.0.0.1，外部主机不可达
