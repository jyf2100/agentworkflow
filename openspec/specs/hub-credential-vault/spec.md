# hub-credential-vault

## Purpose

凭据保险箱——Fernet 加密 per-user 凭据存储与最小读取面。

## Requirements

### Requirement: 凭据加密落盘
hub SHALL 将 per-user 凭据（必填两值 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN + 条件必需 GH_TOKEN（仅 github/未声明 forge 的项目派发要求；gitlab/gitcode-only 用户可不设）+ 可选模型路由 + 可选平台 token GITLAB_TOKEN / GITCODE_TOKEN）以整 payload Fernet 加密存储于 `<data_root>/vault/credentials.json`：文件内容为单一加密 token，明文 MUST NOT 落盘。保险箱文件与目录权限 MUST 为 0600 / 0700。解密密钥（master key）MUST 与密文分离存放（宿主 0600 文件经 compose env 注入），MUST NOT 入仓。

#### Scenario: 落盘即密文
- **WHEN** 保险箱写入任一 user 的凭据
- **THEN** 磁盘上只有 Fernet token（无法从文件读出任何明文字段），文件 0600、所在目录 0700

#### Scenario: master key 缺失或错误明确报错
- **WHEN** hub 打开保险箱时 master key 缺失或不匹配
- **THEN** 明确报错（不猜测、不静默清空重建保险箱）

#### Scenario: 可选平台键随 payload 同等加密
- **WHEN** 用户记录含 GITLAB_TOKEN / GITCODE_TOKEN
- **THEN** 与必填值同等整 payload 加密落盘，权限与读取面约束完全一致

#### Scenario: 明文不落盘（不变式覆盖新键）
- **WHEN** 审视保险箱文件与全部写路径
- **THEN** 任何平台 token 键均无明文落盘形态

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

### Requirement: 会话用户凭据录入
hub SHALL 提供会话守卫的凭据录入端点：登录用户提交必填两值（ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN）+ 可选 GH_TOKEN（github 项目派发时必需）+ 可选模型路由 + 可选平台 token（GITLAB_TOKEN / GITCODE_TOKEN），以本人 mm_user_id 为 key 写入保险箱（幂等覆盖本人记录，加密复用既有通道）。可选键留空的语义 = **保留既有键值不变**（非 replace 抹除——重提交不会静默清掉已存的平台 token；显式清除走运维通道 vault-import 覆盖）。录入响应 MUST NOT 回显明文值（仅确认与字段名）。运维通道 `vault-import --from-env` SHALL 保留并存（key 空间不同，互不冲突）。可选键的占位符形态拦截 SHALL 复用既有 placeholder 判据（同阈值与黑名单口径）。

#### Scenario: 登录后录入含平台 token 的完整记录
- **WHEN** 已登录会话用户提交两值 + gitlab token（留空 gitcode）
- **THEN** 保险箱记录含 GITLAB_TOKEN 键、不含 GITCODE_TOKEN 键；响应只含字段名确认

#### Scenario: 重提交留空不清既有平台 token
- **WHEN** 已有记录含 GITLAB_TOKEN 的用户重提交凭据（平台 token 框留空）
- **THEN** 保险箱记录保留 GITLAB_TOKEN 原值（留空 = 保留），响应只含字段名确认

#### Scenario: 平台 token 占位形态被拦
- **WHEN** 提交的 GITLAB_TOKEN 为占位形态（如 `<token>` 或过短）
- **THEN** 拒绝录入（4xx 列键名与原因），保险箱不落部分记录（原子性口径与必填值一致）

#### Scenario: 无会话录入被拒
- **WHEN** 无有效会话调用凭据录入端点
- **THEN** 401 拒绝，保险箱不落任何记录

#### Scenario: 缺必需字段明确报错
- **WHEN** 会话用户提交缺任一必填两值
- **THEN** 明确列出缺失字段，不落部分记录（整 payload 原子）
