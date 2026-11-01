## 1. 实现
- [ ] 1.1 db effects 表（幂等迁移）+ insert/complete/list_pending 三个函数
- [ ] 1.2 dispatch 六个副作用点包裹（commit/push/pr_open/mm_notify/state_write/reconcile）
- [ ] 1.3 recovery_cli 崩溃恢复升级：先清 Effects 再 journal 对账
- [ ] 1.4 单测：写入/完成/幂等/崩溃恢复重放

## 2. 验证
- [ ] 2.1 真机：正常 run → effects 全 done；kill 容器 → 恢复 → pending 项正确处置
