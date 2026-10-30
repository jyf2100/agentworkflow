# Delta Spec: execution-hub（修改）

## MODIFIED Requirements

### Requirement: 单用户凭据来源（vault 优先 + env 兜底）
hub SHALL 以凭据保险箱（hub-credential-vault capability）为容器注入的凭据来源：派发时按发起 user 从保险箱取必填两值（ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN）与可选模型路由，以及按**目标项目 forge 声明**要求的对应平台 token（github/未声明 → `GH_TOKEN`；`forge: gitlab` → `GITLAB_TOKEN`；`forge: gitcode` → `GITCODE_TOKEN`；GH_TOKEN 仅 github/未声明项目必需），起容器时写 0600 权限临时 env-file 注入、容器即弃后删除。容器凭据为**派发时点快照**：起容器后 vault 记录变更不影响在跑 run，只影响后续派发。单用户过渡形态 SHALL 保留 env 直注兜底：保险箱无该 user 记录时降级读 hub 进程 env（warn 日志可见），保证切片 1 部署形态不破。凭据 MUST NOT 落入 hub DB、日志或 meta 文件。

#### Scenario: vault 优先注入
- **WHEN** 派发起容器且保险箱存在该 user 的凭据记录
- **THEN** env-file 内容取自保险箱解密结果（必填两值 + 可选模型路由 + 按项目 forge 的平台 token），不经 hub 进程 env

#### Scenario: 跑中 vault 变更不影响在跑容器
- **WHEN** run 执行中用户重提交凭据（vault 记录变更）
- **THEN** 在跑容器继续使用派发时点的 env-file 快照；变更自下一次派发生效

#### Scenario: gitlab 项目缺平台 token
- **WHEN** 项目声明 `forge: gitlab` 而保险箱记录（及 env 兜底）均无 GITLAB_TOKEN
- **THEN** 派发 fail-fast abandon 并明确列出缺失键（与缺三值同口径），不起容器不烧配额

#### Scenario: 保险箱缺记录时 env 兜底
- **WHEN** 保险箱无该 user 记录但 hub 进程 env 具备必需值
- **THEN** 降级用 env 直注并记 warn 日志（可追溯降级发生），容器正常起

#### Scenario: 缺凭据明确报错
- **WHEN** 保险箱与 env 均无必需凭据
- **THEN** 明确报错列出缺失项，不静默降级或空值注入
