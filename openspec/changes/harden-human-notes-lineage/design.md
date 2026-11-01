## Decisions

### D1 谱系键 = queue_item_id（不是 PRD stem）

P1-2 的本体论诊断是「归属实体（run 实例，会换）≠ 消费谱系（iteration 链，连续）」。修正 = 把归属键换成与消费谱系同寿的实体。候选两个：

- **queue_item_id**（选它）：① runs 行既有列（db.py:40 `queue_item_id INTEGER NOT NULL REFERENCES queue_items(id)`，行级 NOT NULL + idx_runs_item 索引）——relay 侧 `runs_in_status` 的行直接携带，零新查询；② hub 派发侧 queue item 是重派（attempt+1）的不变锚——engine.retry_item 只 bump attempt 列，id 不变；③ 它是确认环的授权实体（`/pa confirm` 入队建行）——人话的语义受众「这个已归属的需求」与授权链上的实体同一，不是文件名巧合。
- **PRD stem**（弃）：① stem 含 stamp（`{stamp}_{slug}`），同 queue item 跨 attempt 天然异名（attempt 2 的 PRD 文件 stem 换 stamp），要连续就得先剥 stamp——剥离规则 = 重新发明一个「PRD 身份」函数，而且 hub `_slugify`（pinyin 转写）与 pa `dev_slugify`（ADR-0006 单一源头）是两套转写，跨面同 stem 无保证（lifecycle.py:414 注记正是此教训：hub run 目录 slug 与 pa stem 可能不同）；② 同一 PRD 文件可被再次入队（api.py:485 重跑路径 `engine.enqueue` 建新 item）——stem 相同而谱系应分（两次入队是两次独立确认），queue_item_id 天然分开；③ PRD stem 是「内容文件名」不是「谱系实体」，值不入面纪律下拿文件名当身份键是弱键。

结论：queue_item_id 是唯一同时满足「重派不变 / 两侧既有 / 与授权实体同一」的键。

### D2 落点目录 = `<state_root>/human_notes/qi-<id>/`（零新挂载面）

hub 侧 = `cfg.data_root/<project>/state/human_notes/qi-<id>/`（与 lifecycle.py:316 `state_root = data_root / project / "state"` 同拼法）；pa 侧容器内 = `STATE_DIR / "human_notes" / "qi-<id>"`（runner.py:184 state 写卷挂载，同树）。hub 写 pa读同树，**零新挂载面、零新 env 键**（与 add-event-sources 的 events 目录同构：hub 写共享卷、容器只读消费——此处容器同样只读不写该目录）。目录带 `qi-` 前缀防与未来其它谱系键空间混淆；queue_items.id 全局自增（单 db），跨项目不撞。

### D3 pa 读面取键 = hub 署名 meta.json 回链（不经 entry、不改 lifecycle）

要求「dispatch_one 的 entry 有 queue item id」实勘不成立：entry 由 stage_inject 组装（run_daily.py:2291 `{project, slug, path, source_path, title}`），hub→容器只有 `--inject-prd`/`--stamp` 两参 + env（lifecycle.py:361），queue item id 不在载荷里。而 hub 早已把该键署进卷上：派发时 `sign_meta(..., queue_item_id=item["id"])`（lifecycle.py:346 → meta_sign.py:76）落 `<state_root>/runs/<proj>/<stamp>_<slug>/<stamp>_<slug>.meta.json`——F10 署名面既有产物，恰好是「hub 写、pa 可读」的卷上回链。pa 侧 `_queue_item_id_for_stamp(project, stamp)`：在 `STATE_DIR/runs/<project>/` 下取**名字以 `<stamp>_` 开头**的目录（hub 建目录、pa 只写平铺文件——lifecycle.py:111 布局事实；前缀钉 `<stamp>_` 而非 `<stamp>` 防撞 `20261101m` 同日重派树），读其 `*.meta.json` 的 `queue_item_id`。stamp 收敛性：hub `allocate_fresh_stamp` 与 pa inject bump 查同一判据（`prd_manifest_<stamp>.json` 全局命名空间），single-flight 串行下两侧收敛同 stamp；万一发散（部署异常）→ 取不到 meta → None → 零参数 baseline（fail-open 不拦派发）。**不改 hub/lifecycle.py**：写谱系目录在 thread_relay 内自足推导（cfg.data_root + run 行 queue_item_id），挂点 `relay_human_notes`（lifecycle.py:567）零变化。

### D4 回执 / 幂等 / 水位 / 注入语义零变化

- 回执：文案「已记入 run #N …下一轮上下文：X 条」与去抖（每频道每轮至多一条汇总）不动——run 编号是**采集窗口**的事实陈述（哪棵在飞树的窗口里说的），不是归属声明。
- 幂等：文件名 `<ms>_<message_id>.md`（message_id 在文件名）在谱系目录内同一判据 `path.exists()`；frontmatter `run_id` 保留（采集 provenance），新增不动。
- 水位：`thread_relay:{channel_id}` 键与只进不退语义不动。
- 注入：`--human-notes` 合并 artifact 的文件名公式、dev prompt 独立标记块「仅参考非判据」、每轮现扫现并（消费不删盘）全部不动。
- **重派连续性机理**（本案修复目标）：attempt 1 窗口内人话落 `qi-N/`；attempt+1 重派后 relay 按新 run 行（同 `queue_item_id=N`）继续写同一 `qi-N/`；pa 侧 attempt 2 经自己的 meta（同 N）读同一目录 → 旧 notes 不死信。同一 queue item **再次入队**（新 item id）则开新谱系目录——两代需求的上下文不串味，与确认环实体边界一致。

### D5 旧树不迁移不兼容（直接切换，无回落 glob）

`<run_dir>/human_notes/` 旧语义：不迁移、relay 不再写、pa 不回落读。理由：① add-thread-subscription 未归档、真机 showcase（其 tasks 2.2）未完成——在飞 change 的自身修正，无存量交付承诺（审核处置方向同款口径）；② 回落读会保留「最新树 glob」路径，而该路径自上线即死码（`_slugify` NameError，Proposal 已证）——兼容一个从未工作过的读面没有意义；③ 双写（新旧两树）违反单一真相，读侧需裁决优先级，制造新的本体歧义。切换后旧树静默留存卷上（不删），仅不再被消费。

### D6 pa 合并文件名 slug 换 `dev_slugify`（修死码）

合并 artifact 文件名 `human_notes_merged_<project>_<slug>.md` 的 slug 由不存在的 `_slugify` 换为已 import 的 `dev_slugify`（ADR-0006 #5 单一源头，run_daily.py:50）——同一修正内的死码修复，不另开 change（未归档 change 自身修正同 D5 口径）。

## 依赖

无外部依赖；与 add-thread-subscription（未归档）的 delta 关系：本 change MODIFIED 其「订阅直通」requirement（基线逐字取其在飞 delta 文本），归档顺序须在其后（先归并 add-thread-subscription 入主 spec，再归并本 MODIFIED——否则 archive 冲突）。
