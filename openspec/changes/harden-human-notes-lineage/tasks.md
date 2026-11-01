## 0. 勘察（已完成，2026-11-01）

- [x] 0.1 谱系键候选核勘：runs 行 `queue_item_id`（NOT NULL + 索引，db.py:40）；engine 重派只 bump attempt 不换 id；PRD stem 跨 attempt 异名且 hub/pa 两套转写不同（lifecycle.py:414 注记）
- [x] 0.2 pa 侧取键通道核勘：entry 无 queue item id（stage_inject 组装面无此键）；hub 署名 meta.json（meta_sign.py:76 `queue_item_id`）经 state 挂载 pa 可读——回链成立
- [x] 0.3 在飞读面死码实证：`_merge_human_notes` 引用的 `_slugify` 在 run_daily 模块不存在（`hasattr(run_daily,'_slugify')==False`）——直接切换（D5）而非回落兼容的定案依据

## 1. 实现

- [x] 1.1 hub/thread_relay.py 写面：`<data_root>/<project>/state/human_notes/qi-<id>/`（cfg.data_root + run 行 queue_item_id 推导，relay 内自足，不改 hub/lifecycle.py）
- [x] 1.2 run_daily.py `_merge_human_notes` 读面：`_queue_item_id_for_stamp`（`<stamp>_` 前缀定位 hub 署名 meta）→ 读谱系目录合并；签名加 `stamp`（dispatch_one 既有实参）；合并文件名 slug 换 `dev_slugify`（D6 死码修复）
- [x] 1.3 RUNBOOK §25 归属语义更新

## 2. 测试与验证

- [x] 2.1 hub/test_thread_relay.py 增量：谱系落盘路径断言（qi-<id> 树、非 run_dir 树）+ 重派同键连续（两 run 行同 queue_item_id → 同一谱系目录）+ 既有窗口/Gate/幂等/水位/回执回归
- [x] 2.2 新建 pa scripts/test_merge_human_notes.py（紧凑风格）：meta 回链取键、谱系合并、重派旧 notes 可见、meta 缺失/目录不存在 → None 零参数 baseline、stamp 前缀不撞同日重派树（`20261101m`）
- [ ] 2.3 真机：重派场景人话连续（下次真实 run 期间观察项——与 add-thread-subscription 2.2 showcase 同场完成）

## 实施证据（2026-11-01）

- 1.1：thread_relay.py `notes_dir = _lineage_notes_dir(cfg, project, run)`（`cfg.data_root/<project>/state/human_notes/qi-{run['queue_item_id']}`）；水位/幂等/回执/Gate 代码零改动
- 1.2：run_daily.py `_queue_item_id_for_stamp`（`d.name.startswith(stamp + "_")` 防同日 m 后缀撞前缀）+ `_merge_human_notes(project, prd_abs, stamp)` 读 `STATE_DIR/human_notes/qi-<id>`；旧 run_dir glob 路径删除（D5 直接切换）
- 2.1：test_thread_relay.py 4 测试全绿（含新增 test_relay_lineage_dir_and_redispatch_continuity）
- 2.2：test_merge_human_notes.py 5 测试全绿（scoped pytest 实跑）
