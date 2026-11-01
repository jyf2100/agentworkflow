# Proposal: add-effects-queue

## Why

pa 的 durable runtime 靠 journal 驱动 + reconcile **事后对账**（crash 后 reconcile 远端副作用到 exactly-once）。dsh「Engine 单写者 + Effects 持久队列」的思想：把副作用从「先执行后对账」升级为「**先落账再执行**」——执行前把意图写进 Effects 队列，执行后标记完成，崩溃恢复时按队列精确重放/清理。演进路线 P3-⑫。

**与既有 journal 的关系**：journal 记录「发生了什么」（事件流，append-only）；Effects 队列记录「**想要发生什么**」（意图+完成标记，可重放）。两者互补不替代——journal 是事实流（exactly-once 对账依据），Effects 是意图流（重放/清理依据）。

## What Changes

1. **Effects 队列表**（db 新表 `effects`）：`{id, kind, payload_json, status: pending|done|failed, created_at, completed_at}`——六类副作用（commit/push/pr_open/mm_notify/state_write/reconcile）。
2. **写入接缝**：dispatch 的每个副作用动作前先 insert pending → 执行 → update done；crash 恢复时 pending 项按 kind 重放或标记 failed。
3. **reconcile 升级**：从「对账远端状态」到「先清 Effects 队列再对账远端」——pending Effects 优先处置（重放幂等动作或放弃非幂等动作）。

## Non-Goals

- 不替代 journal（事实流不动）；不引入新调度器。
- 不做分布式事务/两阶段提交——单机 Effects 队列 + 幂等重放。
- 不改 verify/publish 阶段的副作用语义（只加记录面）。
