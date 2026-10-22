## RENAMED Requirements

- FROM: `### Requirement: 单用户 env 直注 bootstrap`
- TO: `### Requirement: 单用户凭据来源（vault 优先 + env 兜底）`

## MODIFIED Requirements

### Requirement: 单用户凭据来源（vault 优先 + env 兜底）
hub SHALL 以凭据保险箱（hub-credential-vault capability）为容器注入的凭据来源：派发时按发起 user 从保险箱取三值（ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / GH_TOKEN）与可选模型路由，起容器时写 0600 权限临时 env-file 注入、容器即弃后删除。单用户过渡形态 SHALL 保留 env 直注兜底：保险箱无该 user 记录时降级读 hub 进程 env（warn 日志可见），保证切片 1 部署形态不破。凭据 MUST NOT 落入 hub DB、日志或 meta 文件。

#### Scenario: vault 优先注入
- **WHEN** 派发起容器且保险箱存在该 user 的凭据记录
- **THEN** env-file 内容取自保险箱解密结果（三值 + 可选模型路由），不经 hub 进程 env

#### Scenario: 保险箱缺记录时 env 兜底
- **WHEN** 保险箱无该 user 记录但 hub 进程 env 具备必需值
- **THEN** 降级用 env 直注并记 warn 日志（可追溯降级发生），容器正常起

#### Scenario: 缺凭据明确报错
- **WHEN** 保险箱与 env 均无必需凭据
- **THEN** 明确报错列出缺失项，不静默降级或空值注入

## ADDED Requirements

### Requirement: 排度常驻服务
hub SHALL 提供常驻排度循环：启动时先跑一次重启对账（reconcile_startup，幂等），随后按可配间隔（HUB_TICK_INTERVAL，默认 5s）循环执行派发 tick 与在跑对账（reconcile_running）。进程收到 SIGTERM/SIGINT SHALL 优雅退出：完成当前迭代后停止循环，MUST NOT 杀在跑容器（docker -d 进程树分离，容器由崩溃对账状态机接管）。循环体异常 MUST 捕获记日志并续跑（无静默失败），连续异常计数告警。

#### Scenario: 常驻循环驱动派发
- **WHEN** 常驻服务运行且有可派队列项
- **THEN** 每个间隔至多派发一个容器并轮询在跑容器终态（与手动 tick 语义一致）

#### Scenario: 优雅退出不动容器
- **WHEN** 服务收到 SIGTERM 且当前迭代进行中
- **THEN** 完成当前迭代后退出进程；在跑容器不受影响，重启后经 reconcile_startup 续跟踪或归档

#### Scenario: 循环异常不静默
- **WHEN** 单次迭代抛出异常（如 docker CLI 瞬时失败）
- **THEN** 异常记日志（含计数），下个间隔照常迭代；不因单次异常退出常驻循环
