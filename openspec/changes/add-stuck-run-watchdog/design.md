# 设计：add-stuck-run-watchdog

## 本体论声明

**stuck 是「容器事实与卷上证据事实的背离」**：容器宣称 running（docker 事实），hub 可见的卷上证据说没动（监督面 mtime 停写）。watchdog 的产物只有三个：**告警记录**（观察面）、**kill 动作**（制造确定性 exited 事实）、**回执**（kill / 消解）。它**不产终态**——run 的终态永远归 reconcile 既有对账（`_finish_run` + `on_exit`），kill 只是制造一个 reconcile 能看见的容器事实。判据与处置全程机械零 LLM；语义判断归人。

与既有干预的关系：STALLED / semantic_gate（turn 级 in-band）、iteration STALLED（iteration 级 in-band）、reconcile（容器 exited/gone）——本 change 补**墙钟轴的带外监督**，四者互补成全谱。

## 监督面构成（r1 评审更正后的真实落点）

| 面 | 路径 | 写者 | 粒度 |
|---|---|---|---|
| 平铺 journal | `<stamp>_<slug>.journal.jsonl`（run_dir 的**平铺兄弟**，`_journal_exists` lifecycle.py:122 同款拼法） | run_daily 编排层 | iteration/stage 级 |
| 平铺 events | `<stamp>_<slug>.events.jsonl`（同为平铺兄弟，经 `events.locate_pa_artifact`（`events.py:55-67`）既有寻址，`rd.parent / f"{stamp}_{stem}.events.jsonl"`） | pa 事件出口（critic/verify 裁决、pr_opened 等） | 语义事件级 |
| artifacts | `state/artifacts/<stamp>_<slug>/`（跨 run 共享树，**按 stamp_slug 前缀过滤**后取 max） | verify feedback / handoff 工件（`artifact_store`，content-addressed） | iteration 级 |
| reports | `state/reports/`（同前缀过滤；PA_REPORT_DIR 落点） | report 段 | run 级 |

取 **max(mtime)**（任一面前进 = 活着）。

**明确不入面**（hub 可见性无保证）：worktree 侧 `state/runs/` 下的 `run_log.jsonl`（dev 每 turn，`dev-agent.py:1224` STATE_RUNS_DIR = worktree）与 `handoff-latest.json`（K-turn 快照，`dev-agent.py:584` 写 `state["run_log"].parent` = 同一 worktree 目录）——remote runner 上 worktree 路径不经 data_root，hub 看不见。**推论（D2 的基础）：hub 可见面的合法静默 = 单次 dev SDK 会话全长**（会话内 journal/events 均不写；canary 实证单会话 325 turn，小时量级）。

## D 决策

**D1 判据 = 卷上 mtime 带外墙钟，不用容器内观测**。docker stats / 容器内进程枚举：噪音大、跨 daemon 复杂、需新接缝。mtime 是既有证据面（journal 判据同源），零新挂载；stat 便宜允许较密检测周期。

**D2 阈值 = 标定门制，不拍脑袋**（r1 B1 更正）。hub 面合法静默 = 单 dev 会话全长（小时量级、无静态上界），因此：
- **告警阈值** `HUB_STUCK_AFTER_MINUTES` 默认 120——v1 告警档的语义是**可见性**不是精确判定：超长健康会话仍可能告警（误报 = 一条私信，人工一眼分辨）；真机标定任务 7.1 按实测会话时长分布校准。
- **kill 阈值带标定前置**：新增标定产物键 `HUB_STUCK_KILL_FLOOR_MINUTES`（实测会话时长上限，由 7.1 标定写入）。kill 条件 = 静默 ≥ max(AFTER + GRACE, FLOOR)；**`stuck_kill=1` 而 FLOOR 未配置 → ConfigError fail fast**（配置错必须响；未标定就开 kill 被配置面机械禁止）。这把「kill 阈值须显著高于实测会话上限」从口头纪律变成机器断言。
- mtime 复测对本观测面**无鉴别力**（健康活跃会话在 hub 面同样零前进——r1 B1 指出的要害）：复测只保留「赛跑安全」职责（恰在窗内恢复则不杀），防误杀的主力是 FLOOR 标定门，不是复测。

**D3 动作阶梯**。告警唯一动作 = stale_check「告警是唯一动作」先例（`stale_check.py:9`）；kill flag 默认关 = `PA_SINGLE_FLIGHT_AUTO_MERGE` 破坏性 flag 先例。kill 三重闸：flag 默认关 + FLOOR 标定前置 + 双窗（AFTER+GRACE）。kill 动作 = `Runner.stop`——**这是该接缝方法的首个生产调用点**（r1 B2 更正：Protocol/Fake/Docker 三定义俱在，生产代码零调用史；`DockerRunner.stop` 的幂等预检 + timeout 路径 7.2 首次真机覆盖）。stop 后容器 exited(rc 137/143) → 下轮 `reconcile_running` 既有 exited 分支归档 failed → `engine.on_exit` 重派状态机（14 permanent 语义不变）。**watchdog 不直接调 `_finish_run`/`on_exit`**——终态只经 reconcile 一个写者（单写者纪律）。kill 执行成功 MUST 回执一条（「watchdog 已 stop run #N，终态归对账」——破坏性动作必须留痕，且防人工并发 `docker stop` 双重干预）。

**D4 fail-open + kill 失败闩**（r1 B3 更正）。检测槽异常记日志续跑绝不崩排度循环；单 run 异常不连坐（event_poller 同型）。**stop 失败进闩**：去抖集合第三档 `(run_id, "kill_failed")`——进程生命周期内**不再重试 stop**（否则默认槽距 3min 下恰好构成 spec 禁止的重试轰炸），仅保留 alerted 告警；闩随 run 离开 running 集合或消解而清。通知通道未配置 = stderr 留痕的可见丢失（notify_admin 内部 fail-open）。

**D5 去抖与消解**（r1 更正先例与等价关系）。先例 = drift-watchdog 单先例（消解回执 `config_drift.py:397-398`；stale_check 无消解回执，不引）。去抖键 `(run_id, 档)` 三档：`alerted` / `kill_done` / `kill_failed`，进程内持有，重启丢失最多重告一次（stale_check spec 同款）。**grace 无需时间戳键**：kill 条件按「mtime 龄 ≥ max(AFTER+GRACE, FLOOR)」一次度量即蕴含双窗，等价关系在此写明，实现者不另加时间戳（避免重启语义分叉）。run 离开 running 集合（自然终态）→ 检测循环顺带清键；对已告警 run 的自然终态附终态回执一条（告警的闭环，防「告警后石沉大海」）。

**D6 监督对象与正交性**。只监督 DB `status=running` **且** `Runner.status` 返回 running 的 run；容器已 exited/gone 交 reconcile。与确认环 72h（等人）、runner 探针（机器健康）正交。

**D7 跨机 mtime 的解释边界（NFS attribute cache）**。remote runner 形态下 hub 读共享面 mtime 受 reader 端 attribute cache（actimeo 默认可达 60s）延迟——对告警档（120min）无害；对 kill 复测构成 ≤60s 盲窗（run 刚恢复但属性未过期 → 理论误杀刚复活数十秒的 run）。处置：本机/同机 bind mount 形态无此盲窗；跨机形态把盲窗声明为 kill 语义的一部分（复测「仍停写」在 NFS 下实为「≥ 检测周期前仍停写」），7.2 受控验证含跨机复测项；FLOOR 标定门吸收其余风险。

## 状态机

```
supervising（running run，静默 < AFTER）
  ├─ 静默 ≥ AFTER ──► stuck_alerted（告警一次 + 键 alerted）
  │      ├─ mtime 前进 ──► recovered（消解回执一次，清键）
  │      ├─ 自然终态离场 ──► 终态回执一条，清键
  │      └─ KILL=1（FLOOR 已配）且 mtime 龄 ≥ max(AFTER+GRACE, FLOOR)
  │             ──► 复测 mtime（赛跑安全）
  │             ├─ 已恢复 ──► recovered
  │             ├─ stop 成功 ──► kill 回执一条 + 键 kill_done ──► handed_to_reconcile
  │             │                （exited 事实落卷，终态归既有对账）
  │             └─ stop 失败 ──► 升级告警一条 + 键 kill_failed（闩：进程生命周期不再重试）
  └─ KILL=0（默认）──► 停留 stuck_alerted，人工经 RUNBOOK 处置
```

## 配置面（5 键，`config.py` stale/probe 同型）

| env | 字段 | 默认 | 校验 |
|---|---|---|---|
| `HUB_STUCK_TICKS` | `stuck_ticks` | 36（≈3min） | 正整数，否则 ConfigError |
| `HUB_STUCK_AFTER_MINUTES` | `stuck_after_minutes` | 120 | 正数 |
| `HUB_STUCK_KILL` | `stuck_kill` | 0 | 0/1 |
| `HUB_STUCK_KILL_GRACE_MINUTES` | `stuck_kill_grace_minutes` | 30 | 正数 |
| `HUB_STUCK_KILL_FLOOR_MINUTES` | `stuck_kill_floor_minutes` | None | kill=1 时 MUST 已配（ConfigError）；值 = 7.1 标定的会话时长上限 |

## 与既有 spec 的边界

- `execution-hub`「容器生命周期」：watchdog 是观察者——消费 `runs_in_status` / `Runner.status` / `Runner.stop`；`Runner.stop` 首获生产调用点属接缝**消费**而非修改（per-run 快照路由 requirement 文本本就覆盖 stop 路由）；唯一代码面新增 = FakeRunner.stop 测试替身语义对齐（→exited + exit_code 137，与 DockerRunner 行为一致）
- `hub-notifications`：复用 `notify_admin` 通道；告警/回执形态为新增消息种类，非新通道
- `durable-loop-state` / `in-loop-semantic-checkpoint`：pa 侧 in-band 刹车不动，本 change 是其带外对偶
