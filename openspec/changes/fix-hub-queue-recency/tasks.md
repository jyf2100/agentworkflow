# Tasks: fix-hub-queue-recency

- [x] 1.1 `hub/db.py list_items` 补 `ORDER BY id DESC`（REST /queue 与面板同源同序）
- [x] 1.2 `hub/panel.py render_queue` 行加「入队 / 更新」两列（created_at / updated_at 原样 ISO，经 _esc）
- [x] 1.3 测试（`hub/test_panel.py`）：多行队列渲染 id 降序 + 两时间列出现；`hub/test_api.py`：GET /queue 同降序断言
- [x] 1.4 `bash hub/quality.sh` 全绿（从仓库根）
- [x] 1.5 184 rebuild + force-recreate 后真机队列页验证（降序 + 时间列可见）
