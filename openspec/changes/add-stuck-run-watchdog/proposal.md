## Why

pa 对「卡住」的干预源目前全是 **turn / iteration 边界的 in-band 机制**，卡死时它们集体失效：

| 层 | 机制 | 为何卡死时失效 |
|---|---|---|
| dev agent loop 内 | STALLED 刹车（`dev-agent.py:613`，verifiedRed 后连续 N_STALL=100 turn 无写类 tool_use → exit 12） | 数的是 turn；卡死 = AssistantMessage 停止到达，`async for` 悬置，刹车**永不被评估** |
| dev agent loop 内 | semantic_gate 每 K turn 方向抽查（`dev-agent.py:588`） | 同上，turn 计数驱动 |
| iteration 层 | STALLED / 连续 revise 无进展终态（`loop_state.py:52,107`） | 需要 SDK 会话正常返回才推进；挂起的会话永远回不到 ResultMessage |
| hub 层 | `reconcile_running`（`lifecycle.py:597`）只处理 exited / gone；`state == "running"` 直接 continue | 容器活着但内部停摆 = 永远 running |

后果：**容器活着但内部停摆（SDK 会话挂起 / LLM 调用不返回 / 无超时子进程挂死）将永远占住该 project 的串行名额与全局峰值名额**，等不来任何超时——全库唯一 run 级墙钟上界是确认环的 72h（`lifecycle.py:515`），那是「等人超时」不是「干活超时」。turn 上界也不可依赖：`dev-agent.py:1265` ⚠ 注释自认「max_turns/max_budget_usd 在 SDK 0.2.121 似被绕过」。

这是 DeepSeek Super-agent Watchdog 模块（外部控制环、带外、定时驱动）的 pa 翻译，与路线图既有的机制翻译先例同型（§2.3 test-gate-auto-triage 即 dsh 投机执行的迷你版）。核心判断：**in-band 机制在循环停摆时必然失效，带外墙钟监督恰是补这个象限的唯一手段**——所有既有干预源依赖循环推进，没有一个在墙钟轴上工作。

## What Changes

- **带外墙钟判据**：run 的监督面 = hub 可见卷上证据面 `max(mtime)`——**主面 stdout tee `<stamp>.stdout.log`**（run_daily 启动即写全程增量，stamp = run_dir.name 前缀 hub 自知）+ 辅面平铺 events（`locate_pa_artifact` 寻址，拼音 slug 形态缺席）；静默超过 `HUB_STUCK_AFTER_MINUTES`（默认 120）即判 stuck。全机械零 LLM。**真机实证更正（2026-11-02）**：hub 形态平铺 journal 不存在（flag 生产关闭，124+ run 零 journal），journal 面移除；worktree 遥测与摘要命名面不入面。**推论：hub 可见面的合法静默 = 单次 dev SDK 会话全长（小时量级）**——阈值语义是可见性非精确判定，kill 阈值带标定前置（见下）
- **检测落点 = daemon 第八低频槽**（现状已有七个：stale/probe/drift/digest/events/relay/observer；同型 N-tick 计数，`HUB_STUCK_TICKS` 默认 36 ≈ 3 分钟——stat 便宜可较密），逻辑归新模块 `hub/stuck_check.py` + `Lifecycle.check_stuck_runs()` 薄挂点（`probe_runners` 同构）；只监督 DB `status=running` 且容器事实 running 的 run（对账按 per-run 快照路由既有语义）
- **动作阶梯两级**：
  - **告警（默认唯一动作）**：`notify_admin` MM 私信（含 run 号 / 项目 / 停写时长 / 名额占用提示 / 人工 kill 指针），进程内去抖一次、恢复消解回执一次、自然终态终态回执一次（drift-watchdog 去抖/消解先例）
  - **watchdog 侧 kill（flag 默认关 + 标定前置）**：`HUB_STUCK_KILL=1` 且标定键 `HUB_STUCK_KILL_FLOOR_MINUTES` 已配（**未配而 kill=1 → ConfigError fail fast**——kill 阈值必须显著高于实测 dev 会话时长上限，由 7.1 标定任务产出，未标定被配置面机械禁止）且 mtime 龄 ≥ max(AFTER+GRACE, FLOOR) → 开工前 mtime 复测（赛跑安全）→ `Runner.stop`（per-run 快照路由；**该方法首个生产调用点**，`DockerRunner.stop` 幂等预检 + timeout 路径 7.2 首次真机覆盖）→ 容器 exited → **终态完全归既有 reconcile 对账**（failed + `on_exit` 重派状态机接管，14 permanent 语义不变）——watchdog 不新开任何终态；kill 成功回执一条，stop 失败进闩不重试
- **部署与文档**：`hub.env.example` 五键样例；RUNBOOK 新增「卡死 run 看门狗」节（判据 / 告警样例 / kill 启用步骤与回滚 / 现行人工 `docker stop` 路径降级为兜底说明 / 跨机 NFS attribute cache 盲窗边界）

## Non-Goals

- **不做 LLM 判卡死**——判据全机械（mtime 差值），语义判断（「在干嘛」「要不要救」）永远归人
- **不做运行中注入 / nudge**——in-band 通道此刻已死（卡死的定义），注入无处可达；运行中对话属 Path A（ClaudeSDKClient + interrupt）另行 change
- **kill 不默认开**——破坏性 flag 默认关（`PA_SINGLE_FLIGHT_AUTO_MERGE` 先例），部署机按需在 hub.env 开
- **不动确认环 72h 语义**、不动 pa 侧 iteration 内部刹车（STALLED / semantic_gate）、不动 reconcile 与 `on_exit` 状态机
- **不建日报/面板新面**——告警走 notify_admin 既有通道；日报聚合是否加一行属 add-hub-daily-digest 的后续增量，不在本 change

## Capabilities

### New Capabilities

- `hub-stuck-run-watchdog`：卡死 run 的带外墙钟监督契约——监督面构成（平铺兄弟文件 + 前缀面）、停写判据与阈值标定门（kill 的 FLOOR 前置）、动作阶梯（告警唯一动作 + flag 默认关的 kill 双窗 + kill/消解/终态回执）、kill 失败闩、观察面 fail-open 纪律、与 reconcile 对账语义的边界（watchdog 不产终态）
  - 归属理由：与 hub-config-drift-watchdog 同型——周期检测 + 去抖/消解 + 三态精神的纯观察面独立成 capability（镜像陈旧告警归 hub-notifications 同理）；排度/容器生命周期的机械面归 execution-hub，本 change 对二者零改动只有消费

### Modified Capabilities

（无——监督与 kill 全走既有读面与 `Runner.stop` 既有接缝（本 change 为其首个生产调用点，测试替身对齐属代码面非 spec 面），不改任何既有 requirement）

## Impact

- **代码**：新 `hub/stuck_check.py`（判据 + 去抖/消解/闩 + 动作阶梯 + clock/stat/runner 注入接缝）、`hub/config.py` 五键（`stuck_ticks` / `stuck_after_minutes` / `stuck_kill` / `stuck_kill_grace_minutes` / `stuck_kill_floor_minutes`，stale/probe 同型校验 + kill=1 无 FLOOR 拒起）、`hub/daemon.py` 第八低频槽 + try/except 兜底、`hub/lifecycle.py` `check_stuck_runs` 薄挂点、`hub/runner.py` FakeRunner.stop 测试替身语义对齐（→exited + exit_code 137，与 DockerRunner 一致——r1 评审更正：`Runner.stop` 生产代码零调用史，本 change 为其首个生产调用点）、新 `hub/test_stuck_check.py` + `test_daemon.py` / `test_lifecycle.py` 增量
- **契约**：排度与对账语义零改动（watchdog 消费既有 `runs_in_status` / `Runner.status` / `Runner.stop`，不新开终态、不进派发路径）；无 db 面（去抖进程内持有，重启重告警可接受——stale_check spec 已定同款）；runner 接缝 Protocol 零改动（stop 签名既在，仅消费）
- **部署**：`hub/deploy/hub.env.example` 五键注释样例（kill 键粗体声明破坏性默认关 + FLOOR 标定前置）；RUNBOOK 新节
- **风险**：误报预算见 design D2——hub 可见合法静默 = 单 dev 会话全长（小时量级），告警档默认 120min 的语义是可见性（误报 = 一条私信）；kill 档误杀防线 = FLOOR 标定门（配置面机械强制未标定不开）+ 双窗 + 复测，跨机 NFS attribute cache ≤60s 盲窗声明为 kill 语义边界（D7，7.2 验证项）；真机 7.1 标定任务是 kill 放量的唯一依据——观察先行的既有纪律（proactive-observer 同型）
