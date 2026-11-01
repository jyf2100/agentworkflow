## MODIFIED Requirements

### Requirement: 原子写与读取面

保险箱写入 MUST 原子（临时文件 + rename，崩溃不产半写文件）。读取面 SHALL 仅三个：派发时按 user 解密取 env 字典；bootstrap 导入命令；配置漂移看门狗的派发源核对（spec: hub-config-drift-watchdog——仅经 `get_user_env` 原语读**配置单用户一条**记录，供 L4/L5 层间一致性比对，无新查询形态）。MUST NOT 存在通用「列出全部明文」的 API/端点——hub 最小 API 面不新增凭据查询端点。

#### Scenario: 崩溃不半写

- **WHEN** 写入过程中进程崩溃
- **THEN** 保险箱保持旧完整内容或新完整内容之一，无混合态（rename 原子性）

#### Scenario: 无明文枚举面

- **WHEN** 审视 hub 的 API 端点与 CLI 命令面
- **THEN** 不存在返回保险箱明文凭据的入口（读取只服务于注入通道）

#### Scenario: 看门狗读点最小面

- **WHEN** 配置漂移看门狗执行 L4/L5 断言
- **THEN** 仅经 `get_user_env` 读配置单用户（cfg.user_id）一条记录（会话用户其他记录不读），比对在内存完成、值不入日志与通知；无任何枚举/列出明文的新入口
