## 本体论声明

**Effect** = 意图产生的外部副作用动作（commit/push/pr_open/mm_notify 等）——与 journal Event（已发生的事实）范畴不同。Effect 的同一性 = (kind, payload_sha)——同 kind 同 payload 即同 Effect（幂等重放依据）。状态机：`pending → done | failed`（单向，不回滚——done 后不重放）。

## Decisions

### D1 db 表 + 状态机
```sql
CREATE TABLE IF NOT EXISTS effects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('commit','push','pr_open','mm_notify','state_write','reconcile')),
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','done','failed')),
    created_at TEXT NOT NULL,
    completed_at TEXT
);
```
幂等键 = (kind, sha256(payload_json))——重放时同键 pending 项只执行一次。

### D2 写入接缝（dispatch 侧最小侵入）
每个副作用点包裹：
```python
effect_id = db.insert_effect(kind, payload)  # 先落账
try:
    result = do_side_effect(payload)          # 再执行
    db.complete_effect(effect_id, "done")     # 标完成
except Exception:
    db.complete_effect(effect_id, "failed")   # 标失败（不重试——重试归 reconcile）
```

### D3 崩溃恢复升级
recovery_cli 现流程「journal 对账」前加一步：
1. 读 pending Effects → 按 kind 分类
2. 幂等类（state_write/mm_notify）→ 重放
3. 非幂等类（commit/push/pr_open）→ 检查远端是否已生效（现有 reconcile 逻辑）→ 生效=done / 未生效=failed
4. 清完 Effects 后走现有 journal 对账

### D4 与 dsh Effects 的分野
dsh Engine 的 Effects 是**编排器内的副作用队列**（五内部件共六类）；pa 的 Effects 是**dispatch 段的跨容器副作用队列**——作用域不同但思想同源（先落账再执行 + 提交后才执行 + 指数退避）。pa 不做指数退避（重试归 reconcile/retry_policy 既有面）。

## Open Questions
- effects 表的清理策略（done 项保留多久——v1 不清，量级=每次 run ≤10 项）
- mm_notify 类 Effect 与 notifier 的 fail-open 语义冲突（Effect 标 failed 但通知已发出？——需按 mm 消息 id 幂等）
