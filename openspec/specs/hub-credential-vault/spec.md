# hub-credential-vault

## Purpose

凭据保险箱——Fernet 加密 per-user 凭据存储与最小读取面。

## Requirements

### Requirement: 凭据加密落盘
hub SHALL 将 per-user 凭据（三值 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / GH_TOKEN + 可选模型路由）以整 payload Fernet 加密存储于 `<data_root>/vault/credentials.json`：文件内容为单一加密 token，明文 MUST NOT 落盘。保险箱文件与目录权限 MUST 为 0600 / 0700。解密密钥（master key）MUST 与密文分离存放（宿主 0600 文件经 compose env 注入），MUST NOT 入仓。

#### Scenario: 落盘即密文
- **WHEN** 保险箱写入任一 user 的凭据
- **THEN** 磁盘上只有 Fernet token（无法从文件读出任何明文字段），文件 0600、所在目录 0700

#### Scenario: master key 缺失或错误明确报错
- **WHEN** hub 打开保险箱时 master key 缺失或不匹配
- **THEN** 明确报错（不猜测、不静默清空重建保险箱）

### Requirement: 原子写与读取面
保险箱写入 MUST 原子（临时文件 + rename，崩溃不产半写文件）。读取面 SHALL 仅两个：派发时按 user 解密取 env 字典；bootstrap 导入命令。MUST NOT 存在通用「列出全部明文」的 API/端点——hub 最小 API 面不新增凭据查询端点。

#### Scenario: 崩溃不半写
- **WHEN** 写入过程中进程崩溃
- **THEN** 保险箱保持旧完整内容或新完整内容之一，无混合态（rename 原子性）

#### Scenario: 无明文枚举面
- **WHEN** 审视 hub 的 API 端点与 CLI 命令面
- **THEN** 不存在返回保险箱明文凭据的入口（读取只服务于注入通道）

### Requirement: 单用户 bootstrap 导入
hub SHALL 提供 `vault-import --from-env` 导入命令：把 hub 进程 env 中的三值 + 可选模型路由写入保险箱（单用户过渡形态；OAuth 录入归切片 2b）。导入 MUST 幂等（重复导入覆盖同 user 记录）且导入后输出确认（不回显明文值）。

#### Scenario: 从 env 导入
- **WHEN** 部署机执行 vault-import 且 env 具备三值
- **THEN** 保险箱出现该 user 的加密记录；重复执行覆盖更新；输出仅确认与字段名，不回显值
