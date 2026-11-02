# hub-stuck-run-watchdog — 卡死 run 带外墙钟监督

## ADDED Requirements

### Requirement: 停摆判据（带外墙钟）

hub SHALL 以 hub 可见的卷上证据面 `max(mtime)` 作为 run 进度的带外信号。监督面构成：平铺 journal `<stamp>_<slug>.journal.jsonl`、平铺 events `<stamp>_<slug>.events.jsonl`（二者均为 run_dir 的平铺兄弟文件，寻址复用 `events.locate_pa_artifact` 既有拼法）、`state/artifacts/<stamp>_<slug>/` 前缀树、`state/reports/` 同前缀文件（跨 run 共享树 MUST 按 stamp_slug 前缀过滤后取 max）。监督面静默超过 `HUB_STUCK_AFTER_MINUTES`（默认 120）且容器事实为 running 的 run MUST 被判 stuck。判据全机械零 LLM。journal 与 events 均缺失或 run_dir 不存在 MUST NOT 判 stuck（该形态归 execution-hub 既有 failed_to_start 对账范畴）。检测周期 = daemon 低频槽 `HUB_STUCK_TICKS`（默认 36 tick ≈ 3 分钟）。worktree 侧执行遥测（`run_log.jsonl` / `handoff-latest.json`）hub 不可见，MUST NOT 入监督面。

#### Scenario: 静默超阈值检出

- **WHEN** run #N 容器事实 running，监督面（平铺 journal + events + 前缀 artifacts）mtime 距今 150 分钟（阈值 120）
- **THEN** 判 stuck，`notify_admin` 告警一条（run 号 / 项目 / 停写时长 / 名额占用提示 / 人工处置指针）

#### Scenario: 合法静默窗内零误报

- **WHEN** run 处于单次 dev SDK 会话中段（hub 可见面静默 100 分钟 < 阈值 120）
- **THEN** 零告警零动作

#### Scenario: 监督面缺失不误判

- **WHEN** run_dir 存在但平铺 journal 与 events 文件均未落盘（启动窗口）
- **THEN** 该 run 不进 stuck 判定（failed_to_start 归 reconcile）

#### Scenario: 共享树前缀过滤不串扰

- **WHEN** `state/artifacts/` 下其他 run 的工件刚写入，本 run 前缀树静默超阈
- **THEN** 本 run 判 stuck（max(mtime) 仅取本 stamp_slug 前缀面）

### Requirement: 动作阶梯与 flag 闸

v1 告警是默认唯一动作：检出 stuck MUST 经 `notify_admin` 告警（进程内去抖一次），监督面恢复 MUST 发消解回执并清键，已告警 run 自然终态 MUST 附终态回执并清键。watchdog 侧 kill MUST 同时满足：`HUB_STUCK_KILL=1`（默认 0）、标定键 `HUB_STUCK_KILL_FLOOR_MINUTES` 已配置（**未配而 kill=1 MUST ConfigError fail fast**——未标定会话时长上限前 kill 被配置面机械禁止）、mtime 龄 ≥ max(AFTER + GRACE, FLOOR)。kill 动作 = `Runner.stop`（per-run 快照路由；该方法首个生产调用点）且开工前 MUST 复测监督面 mtime（已恢复则放弃 kill）。kill 执行成功 MUST 回执一条（含「终态归对账」），stop 失败 MUST 升级告警一条并进闩（进程生命周期内不再重试 stop）。watchdog MUST NOT 直接写 run 终态或调 `on_exit`——stop 后终态归 execution-hub 既有 reconcile 对账与重派状态机。

#### Scenario: kill 默认关零破坏

- **WHEN** 未配置 `HUB_STUCK_KILL`，run 停摆 5 小时
- **THEN** 仅告警（去抖一次），零 stop 调用，run 保持 running 等人工处置

#### Scenario: 未标定禁开 kill

- **WHEN** `HUB_STUCK_KILL=1` 而 `HUB_STUCK_KILL_FLOOR_MINUTES` 未配置
- **THEN** hub 启动 ConfigError fail fast（配置错必须响）

#### Scenario: kill 双窗 + 复测后执行

- **WHEN** kill 开启且 FLOOR 已配，run mtime 龄达 max(AFTER+GRACE, FLOOR)，复测仍停写
- **THEN** 恰一次 `Runner.stop`（docker_host = 该 run 行快照）+ kill 回执一条；容器 exited 后由 reconcile 归档 failed 并走 on_exit 重派状态机

#### Scenario: 复测发现恢复则放弃 kill

- **WHEN** kill 候选进入复测，发现监督面 mtime 已前进
- **THEN** 零 stop，发消解回执，回监督态

#### Scenario: stop 失败进闩不轰炸

- **WHEN** kill 的 `Runner.stop` 抛异常（daemon 不可达），此后两个检测槽（≈6 分钟）条件仍满足
- **THEN** 升级告警恰一条，零再次 stop（kill_failed 闩），stuck 告警保持

#### Scenario: 恢复消解与自然终态闭环

- **WHEN** 已告警的 run 监督面 mtime 前进（长会话自然推进）或 run 自然到达终态
- **THEN** 分别发消解回执 / 终态回执一条，去抖键清空

### Requirement: 观察面纪律

检测 MUST fail-open：检查器任何异常记日志续跑绝不崩排度循环；单 run 的 stat/status 异常 MUST NOT 连坐其他 run；告警与回执文本 MUST NOT 含凭据或 env 值（值不入面）；通知通道未配置 = stderr 留痕的可见丢失。去抖状态进程内持有，hub 重启后最多重告一次（spec 显式接受，stale_check 同款）。

#### Scenario: 检测异常不崩排度

- **WHEN** stuck 检测槽内抛出异常（如 data_root 不可达）
- **THEN** 记日志续跑，tick_dispatch / reconcile_running 照常

#### Scenario: 单 run 异常不连坐

- **WHEN** run #7 的监督面 stat 抛 OSError，同轮 run #8 正常
- **THEN** run #8 照常判定，仅 run #7 本轮跳过

### Requirement: 监督对象边界

只监督 DB `status=running` 且 `Runner.status` 返回 running 的 run（双重事实）；容器已 exited/gone 的 run MUST 交 reconcile 处理，watchdog MUST NOT 代劳终态。跨机（remote runner）形态下共享面 mtime 受 reader 端 attribute cache 延迟（≤60s 盲窗），kill 复测语义在该形态下解释为「≥ 检测周期前仍停写」——此边界 MUST 落入受控真机验证项。本 capability 与确认环超时（等人）及 runner 健康探针（机器健康）正交。

#### Scenario: DB running 但容器已退出

- **WHEN** run 行 status=running 而 `Runner.status` 返回 exited
- **THEN** watchdog 零行为（reconcile 下轮归档终态）

#### Scenario: 非 running 状态零接触

- **WHEN** run 行 status ∈ {pending, succeeded, failed, …}
- **THEN** watchdog 零行为零读面
