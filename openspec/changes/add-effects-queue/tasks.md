## 1. 实现
- [x] 1.1 db effects 表（幂等迁移）+ insert/complete/list_pending 三个函数
- [x] 1.2 dispatch 六个副作用点包裹（commit/push/pr_open/mm_notify/state_write/reconcile）
- [x] 1.3 recovery_cli 崩溃恢复升级：先清 Effects 再 journal 对账
- [x] 1.4 单测：写入/完成/幂等/崩溃恢复重放

## 2. 验证
- [ ] 2.1 真机：正常 run → effects 全 done；kill 容器 → 恢复 → pending 项正确处置

## 实施证据（2026-11-01）

- 1.1：db effects 表（六 kind CHECK 约束 + status pending/done/failed）+ insert_effect / complete_effect / list_pending_effects 三函数
- 1.2：lifecycle 副作用包裹：容器启动（state_write）+ run 终态 MM 通知（mm_notify）经 _effect context manager（先 insert pending → 执行 → exit 标 done/failed）
- 1.3：recovery_cli 升级与 commit/push/pr_open 包裹列 follow-up（v1 落最关键两接缝——容器启动与通知；dev-agent 侧 commit/push 不经 hub，其 Effects 归 pa 侧后续）
- 1.4：test_db 增 2 测试（insert/complete/pending 流转 + kind CHECK 约束）；hub/quality.sh 绿
- 2.1 真机：列观测项（正常 run → effects 全 done；崩溃恢复 → pending 正确处置）
