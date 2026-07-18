---
project: orchestrator（控制面）
type: impl
date: 2026-07-16
target_file: Projects/项目推进流水线/scripts/run_daily.py
spec_ref: SPEC #30（Phase 4，2026-07-16 grill 共识）
also_touches: SPEC.md（决策 #30）/ docs/adr/0004（§4 陈旧规则增补）
base_branch: main（vault 无 remote，commit 即终态）
---

# Phase 4：并行 + 限量 + run 级互斥 + 幂等前置闸（run_daily.py）

## For future Claude

本 PRD 落地 Phase 4——`stage_dispatch` 顺序 for → **并行 + per-project 串行 + run 级锁 + 幂等前置闸**。核心矛盾：①并行让既有 per-project PR 限量（`count_inflight_prs` check-then-act）被同仓多 PRD 击穿→per-`owner_repo` 锁修；②ADR-0004 §4 声称"投递前去重"但代码没做→补 per-PRD 前置闸；③`.run.lock` 补 concrete 陈旧规则；④`log()` 非线程安全→加锁。SPEC #30 是源决策。

## 背景

- `stage_dispatch`(637-665)：**纯顺序** for，零并行原语。
- `count_inflight_prs`(433，数 GitHub open PR) + per-project 限量（`dispatch_one:489`，默认 2）已接好，顺序下无竞态。
- `dispatch_one` **无 per-PRD 投递前去重**（ADR-0004 §4 声称的"`gh pr list --head auto/*` 命中即跳"未实现；现状靠 `dispatch_<stamp>.json` 文件复用兜**完成态**重跑，**不兜崩溃中断态**）。
- `main`(669)：**无 `.run.lock`**，无 try/finally。
- `log()`(72) = `print(msg, flush=True)`，**非线程安全**（多 worker 并发交错）。
- `DEV_LOOP_TIMEOUT = 3600`(66)。

## grill 共识（5 决策 + 7 默认，2026-07-16）

1. **per-project 串行**（修 TOCTOU）：`dispatch_one` 按 `owner_repo` 持 `threading.Lock`(size=1)，包整个函数→同仓串行（`count_inflight_prs` 仓内恒新鲜）、跨仓并行。
2. **并行原语**：`_run_capture` 同步 `subprocess.run`(GIL 释放)→`ThreadPoolExecutor` + `threading.Lock`；全局上限 `--max-concurrent`(默认 4)；records 写盘前按 `project+slug` 排序；per-future `future.result()` 异常隔离。
3. **`.run.lock` 陈旧规则**：陈旧 = PID 失活 OR 锁龄 > `MAX_RUN_WALL`；陈旧自动接管，活锁拒绝 + exit 2 + `--break-lock`。
4. **per-PRD 幂等前置闸**（兑现 ADR-0004 §4）：`dispatch_one` 投递前查 PR 或分支已存在→`skip`，不起 dev loop。
5. **`log()` 线程安全**：加模块级 `threading.Lock`。

**7 默认**：①去重判定 = PR 或分支存在（branch-or-PR，`git ls-remote` 兜无 PR 的 branch）；②锁包整个 `dispatch_one`（含 verify）；③锁文件 JSON `{pid,started}`；④`--break-lock` ⟂ `--force`；⑤SIGTERM 不装 handler，靠 PID 失活 + 90min 锁龄；⑥执行优先级 FIFO；⑦锁获取在 `STATE_DIR.mkdir` 后。

## 任务

改 `scripts/run_daily.py`：并行化 `stage_dispatch` + per-project 锁 + 幂等前置闸 + `.run.lock` 获取/释放 + `LOG_LOCK` + `--max-concurrent`/`--break-lock` CLI。到「`py_compile` 过 + `--max-concurrent 1` 顺序等价 smoke + run-lock 陈旧/活锁手验 + 幂等闸手验」。

## 验收标准（可验证行为）

1. **per-project 串行（修 TOCTOU）** — 同仓 2 PRD 并发：第 2 份 `count_inflight_prs` 看到第 1 份已开 PR（不击穿 cap）；不同仓仍并行。在途数统计该仓全部未关闭 PR（人工 + 流水线）；GitHub 查询失败时返回「未知」并 fail closed，该 PRD 记 `skip`，不得返回 0 放行。
2. **并行原语 + 全局上限** — `ThreadPoolExecutor(max_workers=args.max_concurrent)`；`--max-concurrent` 默认 4；`=1` 等价旧顺序（**回归基准**）；per-future 异常隔离（#26 不变）；records 按 `project+slug` 排序。
3. **run 级互斥 `.run.lock`** — `O_CREAT+O_EXCL` 原子获取、包整个 main、PID+ISO ts；陈旧（PID 失活 OR 龄 > `DEV_LOOP_TIMEOUT+1800`=90min）→自动接管；活锁→exit 2 + `--break-lock`；try/finally 全路径释放。
4. **per-PRD 幂等前置闸（选型 ii，slug 子串）** — `dispatch_one` 投递前复刻 dev-agent.mjs:259 slugify 算 `devslug`，按 slug 子串查 `gh pr list --state all`（`headRefName` 含 devslug）+ `git ls-remote --heads origin` 的 `auto/*` 分支；命中→`status=skip, reason="已投递（PR #n / 分支 auto/..）"`，**不起 dev loop**（已对真 PR #31 `auto/20260715-1816-20260715-web-skill-prese` 验 hit=True）。用 slug 子串因 dev-agent.mjs `stamp()=YYYYMMDD-HHMM`（含时分）run_daily.py 不可预测；选型 i（给 dev-agent 加 `--stamp`）拖一个 cc-web-control PR、把控制面改动拖成跨面，已否。⚠️残留耦合：slugify 须与 dev-agent.mjs:259 同步漂移（算法漂移→匹配静默失效）。崩溃中断态重跑：已开 PR/branch 的 entry 被前置闸跳过（不重投、不烧钱）。
5. **`log()` 线程安全** — 并行 dispatch 下 stdout 不交错乱码（`LOG_LOCK` 包 print）。
6. **顺序等价 + 不回归** — `--max-concurrent 1` 除「在途 PR 查询失败由容忍放行改为 fail closed」外，其余输出/行为与 Phase 3 一致（仅可能排序）；`python -m py_compile` 过。
7. **手验** — run-lock：造活锁→exit 2，`--break-lock`→正常；造陈旧锁→自动接管。幂等：手工造 `auto/<stamp>-<slug>` branch/PR→再跑→该 entry skip。
8. **待投递状态** — 过闸 PRD 因在途上限、GitHub/网络或 branch-protection 查询暂时失败而跳过时，持久为待投递；后续日跑不自动拾取。仅 roc 明确要求重试时处理；同项目一次重试多份时按过闸时间 FIFO（旧→新）。重试前重新检查准入、在途上限和幂等。

## 技术约定（默认值）

| 参数 | 值 |
|---|---|
| 并行原语 | `ThreadPoolExecutor` + `threading.Lock`（sync subprocess.run） |
| per-project 锁 | `defaultdict(threading.Lock)` keyed by `owner_repo`，包整个 `dispatch_one` |
| `--max-concurrent` | 默认 4（ThreadPool size = 全局并发上限） |
| 幂等前置闸（选型 ii） | 复刻 dev-agent.mjs:259 slugify→`devslug`；`gh pr list --state all`（headRefName 含 devslug）OR `git ls-remote --heads origin` 的 `auto/*` 含 devslug → skip（纯控制面，不动 dev-agent.mjs） |
| `MAX_RUN_WALL` | `DEV_LOOP_TIMEOUT + 1800`（=90min） |
| 陈旧判定 | PID 失活 OR 锁龄 > MAX_RUN_WALL |
| 活锁处置 | 拒绝 + exit 2；`--break-lock` 强拆（⟂ `--force`） |
| 锁获取 | `os.open(O_CREAT+O_EXCL)` 原子；失败读内容判陈旧 |
| 锁内容 | JSON `{"pid":N,"started":"ISO8601"}`（R1 单机） |
| 锁范围 | 整个 `main()`（`STATE_DIR.mkdir` 后获取） |
| 锁释放 | try/finally 全 exit 路径（success / RuntimeError / Ctrl-C） |
| `log()` | `LOG_LOCK = threading.Lock()` 包 print |
| 输出稳定性 | records 按 `project+slug` 排序后写盘 |
| 执行优先级 | FIFO（passed 顺序） |

## 范围

- **涵盖**：上述 1–7（并行 + 锁 + 幂等闸 + run-lock + log 锁 + 不回归 + 手验）+ SPEC #30 + ADR-0004 §4 增补。
- **不涵盖**：报告聚合/可视化（Phase 5）；wka 控制台（Phase 6）；in-process PR 计数器（方案 B）；跨机 host；per-stamp 锁细分；同仓多 PR 并发（锁显式排除）。

## 关联

- SPEC：#26（故障隔离）/ #30（源）/ §6 并行(269) / run 锁(270) / R1 限量(285) / ADR-0004（§4 lockfile + 幂等前置闸 + 陈旧增补）。
- 代码：`stage_dispatch`(637) / `dispatch_one`(459) / `count_inflight_prs`(433) / `main`(669) / `log`(72) / `DEV_LOOP_TIMEOUT`(66)。
- L15：run_daily.py 在 vault 无 remote，commit 即终态。
