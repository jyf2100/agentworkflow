# 2026-11-01 P0+P1 批次 — 本体论审核 R1（实现后全量回归）

- **范围**: 今日全批 10 需求 / 13 commits / v1.2.0 + 追加——add-helix-{signal-source,target-harness}、六层漂移修复 + add-config-drift-watchdog、add-runner-image-sync、add-test-gate-auto-triage、add-hub-daily-digest、verify 档位修复（f37d6b4）、add-event-sources、add-thread-subscription、演进路线文档。
- **方法**: 按本体论维度审——范畴归属 / 同一性 / 时态 / 部分学与归属 / 真相源权威 / 被拒概念合规。全部 finding 带代码或真机证据。
- **裁定**: **无 P0**；**P1 × 2**（可立后续 change）；**P2 × 4**（注记级）。

## 一、本体承诺盘点（今日新增承诺与兑现状态）

| 承诺 | 出处 | 状态 |
|---|---|---|
| 漂移项 = 规则+层对+键名（值不入面） | hub-config-drift-watchdog | ✅ 单测断言 + 真机报文核验 |
| 探针是唯一绿灯权威（分发不自判绿） | add-runner-image-sync D6 | ✅ 分发后走 probe_and_store 复测 |
| 重验 = 同一条已识别测试命令的再次执行（fresh 按构造） | add-test-gate-auto-triage D1 | ✅ identity 由 evidence.command 保持 |
| 事件 = 语料增量（occurrent 物化为 continuant 文件） | add-event-sources D2 | ✅ event_id→文件名同一性转移 |
| 人话 = 上下文非判据（与 verify 反馈/lessons 三档范畴分明） | add-thread-subscription D2 | ✅ 独立标记块 + spec 明文 |
| hub 形态判据 = spec 担保的必挂点（/input） | verify 档位修复 | ✅ 回归测试三形态 |
| 镜像内容同一性 = image ID（内容寻址） | image-sync D5 | ✅ 不一致删目标侧 |

**被拒概念合规**（今日未引入任何自拒概念）：无常驻 LLM Router（六个低频槽全确定性）；无跨信道身份映射（events 作者 = 人工配置的 login 白名单；直通 Gate = MM 身份域）；无 DSH 宿主迁移；无事件短路派发。dsh 概念翻译的降级（Trigger→语料、直通→排队面）均**显式声明**而非静默弱化。

## 二、Findings

### P1-1 L5 的「存在」≠「可用」——漂移看门狗放过死链注册

**证据**: roc 名册至今仍含 `qwen3.6-35b`（真机复核 `在名册: True`）而其上游 8896 已死——这正是 2026-10-31 事故链的一环。R3 的 membership 断言只查名册（`/v1/models` 含名即 ok），**对「注册名在、上游死」形态零告警**。

**本体论诊断**: 六层模型把「网关注册」当一层，实则捆绑了两个判据——存在判据（名字在册）与可用判据（上游可达）。L5 的健康 = 两者合取，R3 只断言了前者。层的内部结构未展开是模型盲点（同构先例：三态远程查询把 UNKNOWN 从 NOT_FOUND 里拆出来，正是同一课）。

**处置建议**: 立后续 change——R3 增补「消费面模型轻探活」（每模型一次 max_tokens=1 补全，三态分流 UNKNOWN 不告警）；或最低限度在本 spec/RUNBOOK 显式声明「R3 断言范围 = 名册非健康」并把上游健康归 ops 面。**不阻塞本批**。

### P1-2 human_notes 的归属本体 ≠ 消费本体——重派即断链

**证据**: notes 归 **run 实例**（`<run_dir>/human_notes/`），而 run_dir 每次 dispatch 新建（`allocate_fresh_stamp`，lifecycle.py:30 引入派发即新树——#109/#110/#111 三个 run 三棵树真机可核）。队列层重派（attempt+1）后：`_merge_human_notes` glob 取「最新 run 目录」（run_daily），旧树的 notes 成死信；而人话在语义上是对**需求**（PRD/queue_item 谱系）说的，不是对某个 run 实例说的。

**本体论诊断**: 归属实体（run 实例，会换）与消费谱系（iteration 链，连续）错位——这与 handoff 机制的本体承诺（「同 PRD 上一 iteration」跨 run 连续）直接冲突。

**处置建议**: 后续把 notes 归属上移到 queue_item 或 PRD 谱系（如 `state/human_notes/<project>/<prd_stem>/`）；v1 语义内窗口判据（只对 running run 采集）限制了暴露面，**记录不阻塞**。真机 showcase 时若恰好发生重派会直接复现此缝隙。

### P2-1 L3 的化身术语不一致（R1 vs R2）

R1 正确地把 hub.env 表述为同层双态（「L3 意图→L3 快照」）；R2 报「L4→L3 兜底源」——实际比对物是 `user_env()`，是 hub.env→进程 env→HubConfig→user_env() **两级物化后的派生投影**，非原始 L3。「兜底源」掩盖了派生链。低危（值一致时无行为差异）；术语统一建议：R2 层对改「L4→L3 派生投影」或 design 注明派生链。

### P2-2 事件文件名无严格时序承诺

`YYYYMMDD_<event_id>.md`——同日多事件的顺序隐式依赖 GitHub event_id 单调；radar glob 消费无顺序承诺。低危注记（当前消费面是 LLM 打分，顺序不构成判据）。

### P2-3 六个低频槽 = 同一概念的六次重复实现

stale/probe/drift/digest/events/relay 六处独立 `if tick % N` + try/except 同型块。「周期观察任务」已是稳定概念，实现未抽象（无注册表）。非缺陷，是重构信号（follow-up 候选，不动今日代码）。

### P2-4 「今日新」与「24h 窗口」双时间基准并置

fetch 的 date-marker 按自然日、digest 的终态计数按滚动 24h（ended_at）——各自正确，并置时读者需辨析。RUNBOOK 已各节自洽；建议 digest 报文标注口径（已带「24h」字样，视为已注记）。

## 三、通过项（本体声明与实现一致的典型，抽录）

1. **auto-triage 的同一性论证成立**：「新的绿色测试」的 spec 判据是 *recognized test command*——重验执行的是 `evidence.command` 原文（同一条已识别命令的再次执行，identity 保持）；fresh 不是豁免而是**时态正确性**（采集时点晚于全部候选写，dev loop 已终止期间零写）。
2. **image-sync 双状态实体边界清晰**：`probe_ok`（调度资格，唯一权威）与 `image_sync_json`（操作历史，记账）——范畴不混，分发成功后仍须经复测探针转绿。
3. **verify 档位判据换锚更接近本质**：从「数据面标记存在」（/data/pa，挂载布局的偶然）到「输入面标记存在」（/input，spec 担保的必然）——形态判据从偶然属性升为本质属性。
4. **三种注入物范畴分明**：human notes（仅参考）/ verify feedback（必须落实）/ lessons（经验）——三个独立标记块，语义分级不被 prompt 拼接稀释。
5. **模型名的名称/实体分离被正确处理**：`qwen3.8-27b-fast` 两通道（roc 路由 vs 9001 直连）同名不同径，实体同一由 image ID 内容寻址兜底（image-sync D5 同款思想）。

## 四、总裁定

本批 10 需求的本体纪律**整体良好**：每个 change 有显式范畴声明与被拒清单；两处 P1 均为「模型的层/归属切分未展开到底」而非概念混用——立后续 change 处置（建议名：`add-drift-l5-health-probe` / `harden-human-notes-lineage`）。P2 四项注记在案，不阻归档路径。

---

## 附录：P2-①② 补审（2026-11-01 实施后追加）

**背景**：P2-①（observer）与 P2-②（attention）实装未走对抗评审/冷评审（时间压力直实施）——用户指出后补审。

### P1-3（当场修复）：attention 页 dispatch 路径推导 bug

**证据**：代码用子目录 `state/dispatch/YYYYMMDD.json` 但实际文件是**平级** `state/dispatch_YYYYMMDD.json`（真机 ls 实证），且 stamp 有 m 后缀形态（dispatch_20261028m11.json）——`started_at[:10].replace('-','')` 推导恒落空 → 阻断 triage 节**名存实亡**（恒空）。
**修法**：改为 `glob("dispatch_*.json")` 平级全扫 + run_dir slug 段匹配行（commit 已落）。

### P2-5：observer 事件页 100 条可能不足 7d 基线

活跃仓 events 端点一页 100 条可能只覆盖数小时——基线偏高 → 激增检测偏保守（少报不误报）。observe v1 取保守侧可接受；design Open Questions 已有自适应基线预告。**注记在案不阻塞**。

### 通过项

- streak 锚逻辑正确（API 新→旧序，streak[-1]=最老 ✓；增长锚不变幂等 ✓；恢复后新 streak 新锚 ✓）
- observer token 全为参数名/注释——提案文件零 token 值（值不入面 ✓）
- 提案仅日报不即时推送（三档报告面分离 ✓）；auto 降级 warn ✓
- attention 纯投影零新真相源（四节各链回源实体 ✓）；空节隐藏/全空安静 ✓

**总裁定**：P1 一处已当场修复；P2 一处注记。两 change 可进 P2-③。

---

## 附录二：P3 三件补审（2026-11-01 实施后追加）

### P1-4（当场修复）：context_loader stub 路径静默清空验收测试注入

**证据**：`acceptance_injection = ""` 在 stub 分支中被清空，但 spec 场景明确「verify 面注入 MUST NOT stub 化（考卷不可截断）」——清空比截断更严重：dev 的**完成判据**（红→绿是完成标准）被静默丢弃，dev 会以为没有验收测试。
**修法**：`acceptance_injection` 保留不清空（stub 只影响 PRD/lessons/handoff/feedback/human_notes 五个上下文通道，验收测试是考卷不是上下文）。

### P1-5（当场修复）：effects 表缺既有库迁移

**证据**：真机 `/data/pa/hub.sqlite3` 无 effects 表（`no such table: effects`）——CREATE TABLE 只在新库建表脚本里，旧库 `_migrate()` 没有补建，`insert_effect` 直接 OperationalError 崩。
**修法**：`_migrate()` 增 `CREATE TABLE IF NOT EXISTS effects`（幂等，先例 = intake_watermarks）。

### P2-6：effects 仅包裹 2/6 副作用类型（范围缺口）

hub 侧只包裹容器启动（state_write）+ MM 通知（mm_notify）；commit/push/pr_open 发生在 executor 容器（pa 侧），不经 hub db。tasks.md 已声明 follow-up，但 spec「每个副作用」的范围承诺与实装面有差距。**注记在案**。

### P2-7：memory_graph 模块就绪但零数据

无 lessons catalog 含 refs 字段——模块是纯代码就绪（6 测试绿），flag 开也是空图 no-op。预期行为（新模块），列观测项等 lessons 积累 refs 后接线。

### 通过项

- stub 预算截断优先级正确（高→低分配，PRD 永不截——单测覆盖）
- effects 状态机单向正确（pending→done/failed，无回滚——CHECK 约束 + complete 单向）
- memory_graph Top-K k=0 全量 baseline、dormant 修剪/复活、冲突对边界（等效不冲突）全绿

**总裁定**：2 P1 已当场修复；2 P2 注记。P3 三件可进部署/观测。
