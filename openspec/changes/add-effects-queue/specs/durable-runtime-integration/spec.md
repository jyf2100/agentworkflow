## ADDED Requirements

### Requirement: Effects 持久队列（先落账再执行）

dispatch 的每个外部副作用动作（commit / push / PR 创建 / MM 通知 / state 写入 / reconcile）SHALL 在执行前先写入 db effects 队列（kind + payload + status=pending），执行后标记 done 或 failed。崩溃恢复 SHALL 先处置 pending Effects（幂等类重放、非幂等类查远端后标记）再走 journal 对账。Effects 的幂等键 SHALL 为 (kind, sha256(payload))——同键重放只执行一次。Effects 与 journal 互补不替代（journal=事实流 append-only；Effects=意图流 pending→done/failed）。done 项 MUST NOT 重放。

#### Scenario: 正常 run 全部 Effects 完成
- **WHEN** run 正常终态
- **THEN** effects 表该 run 的全部项 status=done

#### Scenario: 崩溃恢复重放幂等 Effect
- **WHEN** 容器 kill 后恢复，effects 有 pending 的 state_write 项
- **THEN** 恢复流程重放该写操作（幂等键防重复）→ 标 done → 进 journal 对账

#### Scenario: 非幂等 Effect 查远端后标记
- **WHEN** 恢复时有 pending 的 pr_open 项
- **THEN** 查 gh PR 是否已开（已开=done / 未开=failed）→ 不盲目重放（防双开 PR）
