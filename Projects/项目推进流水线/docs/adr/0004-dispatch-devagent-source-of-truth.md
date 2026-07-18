# 0004 — dispatch 以 GitHub 为真源：部分失败对账恢复 + 去重/幂等

## 决定

1. **GitHub 是 PR 存在的真源**，dev 脚本 stdout JSON 只是加速 hint，不是权威记录。dispatch 在脚本返回后（含 wall-clock kill / 缺 JSON）统一做对账，而非"读到 JSON 才算数"。
2. **对账流程**（`gh pr list --head auto/<slug> --state all` 查 PR + `git log` 查分支 commit）：
   - **有 PR**：按 PR 录入（哪怕 JSON 缺失），照常跑独立验证；
   - **无 PR 但有 commit**：**dispatch 自己补开 PR**，正文标"⏸ dev loop 中断，待人 review"，进报告"⚠️ 异常"区；
   - **无 commit**：删孤儿 `auto/*` 分支，报告记"超时-无产出"。
3. dispatch 对 dev loop 的 PR 生命周期负责（查/补开/清理孤儿分支），不把这部分权威让给脚本 stdout。
4. **去重 / 幂等也以 GitHub 为真源**（延伸 1–3）：date-marker（`consumed_wechat_date`）只作跳旧日的**快路径**，不扛幂等。真源 = dispatch 投递前对每项目 `gh pr list --head auto/* --state all`（含 open + 未合并分支），命中即跳过；崩溃重跑产生的重复候选被 GitHub 在途分支/PR 折叠，**不产生重复 PR**。并发由编排器 run 级 lockfile（`state/.run.lock`，PID + 陈旧检测）保证，cron 与 wka 互斥。**陈旧检测具体规则（2026-07-16 增补，SPEC #30 / Phase 4）**：陈旧 = PID 失活（`os.kill(pid,0)` 抛 OSError）**或** 锁龄 > `MAX_RUN_WALL`（= `DEV_LOOP_TIMEOUT` + 30min，绑常量、≈90min）→ 启动方自动接管（删旧锁 + 重建 + log 标记）；活锁（PID 活且未超龄）→ 拒绝启动 + exit 2，需 `--break-lock` 显式强拆（不复用 `--force`——语义不同）；获取用 `os.open(O_CREAT+O_EXCL)` 原子避竞态；锁包整个 `main()`、`try/finally` 全 exit 路径释放。同 Phase 并行由 per-`owner_repo` `threading.Lock`（size=1）保 `count_inflight_prs` 在仓内诚实（修并行下 check-then-act TOCTOU），跨仓仍并行，全局上限 `--max-concurrent`（默认 4）。**幂等前置闸（④）落地选型 ii（2026-07-16）**：上文本点说的 `gh pr list --head auto/*` 是理想精确查；实际 dispatch 复刻 dev-agent.mjs slugify（`scripts/dev-agent.mjs:259`）算 `devslug`，按 **slug 子串**查 `gh pr list --state all`（headRefName）+ `git ls-remote --heads origin` 的 `auto/*` 分支——因 dev-agent.mjs `stamp()=YYYYMMDD-HHMM`（含时分）run_daily.py 不可预测，精确 `--head <branch>` 做不到；选型 i（给 dev-agent 加 `--stamp`）拖一个 cc-web-control PR、把控制面改动拖成跨面，已否。R1 slug=date+24 字描述够特异、子串误命中可忽略（已对真 PR #31 验 hit=True）；⚠️残留耦合：slugify 算法须与 dev-agent.mjs:259 同步漂移。

## 背景

ADR-0003 #5 定了 dispatch↔dev-agent 的 JSON 契约，但 full-auto 每日跑必然撞上三种部分失败：① wall-clock kill 在 dev loop 中途（无 JSON 但分支已有 commit）；② 脚本 `gh pr create` 开完 PR 后崩溃（JSON 没吐，PR 成孤儿）；③ 脚本自中止 `success:false`（有无 PR 模糊）。若 dispatch 只信 JSON，"报告说没有、GitHub 上其实有"会**静默漂移**——full-auto 最致命的失败模式。

## 考虑过的替代

- **只信 stdout JSON**：拒绝。脚本崩溃 / 被 kill 时 JSON 缺失，PR 存在与否无从得知，孤儿 PR/分支静默堆积。
- **无 PR 即删分支（不补开）**：拒绝。dev loop 中途产出的 commit 可能是有效工作，直接删等于丢弃，且人无从知晓曾发生什么。
- **脚本保证只在最后原子地"开 PR + 吐 JSON"**：拒绝。`gh pr create` 与 print 跨进程崩溃不可能原子化，不可依赖。

## 后果

- dispatch 多承担"补开中断 PR / 清理孤儿分支"的职责，逻辑变重，但换来 full-auto 下无静默漂移。
- 报告会出现"⏸ 中断 PR"——人需理解这是机器未跑完但已有产出的 PR，照常 review。
- 与 ADR-0003 #5 的 JSON 契约**不冲突**：JSON 仍是 happy path 的首选信息源，对账是其失败兜底。
- **幂等不靠 date-marker**：marker 降为快路径，去重真源是 GitHub 状态 + run 锁；崩溃/重跑/并发都不产生重复 PR，只可能产生被折叠的重复候选（report 可忽略）。
