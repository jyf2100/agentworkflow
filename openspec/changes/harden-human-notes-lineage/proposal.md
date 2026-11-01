# Proposal: harden-human-notes-lineage

## Why

本体论审核（docs/reviews/2026-11-01-p0-p1-batch-ontology-review-r1.md P1-2）：human_notes 的**归属本体 ≠ 消费本体**。notes 归 **run 实例**（`<run_dir>/human_notes/`，每次 dispatch `allocate_fresh_stamp` 新建树——#109/#110/#111 三棵树真机实证），而人话在语义上是对**需求谱系**说的。队列重派（attempt+1）后新 run 新树，`_merge_human_notes` glob「最新 run 目录」→ 旧树 notes 成死信——与 handoff「同 PRD 上一 iteration 跨 run 连续」的本体承诺直接冲突。

实施勘察另证（2026-11-01）：在飞读面 `_merge_human_notes`（run_daily.py:2577）引用的 `_slugify` 在 run_daily 模块内**根本不存在**（hub/lifecycle.py:117 同名函数未跨面共享；`hasattr(run_daily, '_slugify') == False` 实核）——runs 树存在即 NameError，读面自上线以来是死码。修正归属本体的同时直接切换读面，不保留这条死路径。

## What Changes

- **谱系键 = queue_item_id**：notes 归属从 run 实例上移到 queue_item 谱系（runs 行既有 `queue_item_id` 列，db.py:40；重派 attempt+1 不改键）。
- **落点 = `<state_root>/human_notes/qi-<id>/`**：hub relay 写面（thread_relay.py 从 run 行 `queue_item_id` 推导）、pa 合并读面（run_daily 经 hub 署名 meta.json 的 `queue_item_id` 回链）同树——经既有 state 挂载（runner.py:184）零新挂载面。
- **零变化面**：窗口判据、成员三态 Gate、`(message_id)` 幂等、水位（`thread_relay:{channel}`）、回执文案/去抖、`--human-notes` 注入语义（仅上下文非判据）、daemon 槽位节奏。
- **旧树不迁移不兼容**：`<run_dir>/human_notes/`（今日在飞语义）无迁移、无回落读取——add-thread-subscription 未归档（showcase task 2.2 未完成），属未交付 change 的自身修正，无存量承诺。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `hub-mm-intake`：MODIFIED「订阅直通（在飞 run 频道人话排队注入）」——落点从 run 伴生 `human_notes/` 改为 queue_item 谱系目录；补重派连续性 scenario（attempt+1 后旧 notes 仍可见）；其余授权/窗口/幂等/回执/fail-open 语义逐字保留（基线取自 add-thread-subscription 在飞 delta）。

## Impact

- **代码（改）**：`hub/thread_relay.py` 写面换谱系目录（relay 内自足，**不改 hub/lifecycle.py**——挂点 `relay_human_notes` 零变化）；`Projects/项目推进流水线/scripts/run_daily.py` `_merge_human_notes` 读面换谱系目录（经 `<stamp>_` 前缀定位 hub 署名 meta.json 取 `queue_item_id`；签名加 `stamp` 参数，dispatch_one 既有实参）。
- **测试**：`hub/test_thread_relay.py` 增量（谱系落盘路径断言 + 重派同键连续）+ 新建 `Projects/项目推进流水线/scripts/test_merge_human_notes.py`（紧凑风格）。
- **文档**：RUNBOOK §25 归属语义更新。
- **风险**：低——单树读写、键稳定（db NOT NULL 列）；pa 侧 meta 缺失/目录不存在 → None（`--human-notes` 零参数 baseline，fail-open 不拦派发）。
