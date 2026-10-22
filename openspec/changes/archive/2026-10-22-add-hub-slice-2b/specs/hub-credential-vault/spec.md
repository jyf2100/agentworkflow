## ADDED Requirements

### Requirement: 会话用户凭据录入
hub SHALL 提供会话守卫的凭据录入端点：登录用户提交三值（ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / GH_TOKEN）+ 可选模型路由，以本人 mm_user_id 为 key 写入保险箱（幂等覆盖本人记录，加密复用既有通道）。录入响应 MUST NOT 回显明文值（仅确认与字段名）。运维通道 `vault-import --from-env` SHALL 保留并存（key 空间不同，互不冲突）。

#### Scenario: 登录后录入本人凭据
- **WHEN** 已登录会话用户提交三值 + 可选路由
- **THEN** 保险箱出现或覆盖该 mm_user_id 的加密记录；响应只含确认与字段名，不含任何值

#### Scenario: 无会话录入被拒
- **WHEN** 无有效会话调用凭据录入端点
- **THEN** 401 拒绝，保险箱不落任何记录

#### Scenario: 缺必需字段明确报错
- **WHEN** 会话用户提交缺任一必需三值
- **THEN** 明确列出缺失字段，不落部分记录（整 payload 原子）
