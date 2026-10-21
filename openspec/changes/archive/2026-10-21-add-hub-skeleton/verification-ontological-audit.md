# add-hub-skeleton 本体论审核记录（artifacts 四件，2026-10-21）

审核对象：proposal / design / specs（execution-hub 新建 + durable-loop-state delta）/ tasks——apply 前置审核。
裁决：用户选「全修 A1-A8」。全部已修入 artifacts。

## 契约对照事实（审核中验证）

- **stamp 真实形态**：`YYYYMMDD`（8 位日期无横线）+ 撞名自增后缀 `m`/`m2`/`m3`（`_bump_stamp_suffix`，run_daily.py:1343）
- **inject 段 bump 判据**：`state/prd_manifest_<stamp>.json` 文件存在才 bump（while 循环），且为**全局 stamp 命名空间**（跨 project）——非 run 目录（run_daily.py:1392-1395）
- **pypinyin 已入镜像依赖**：pyproject.toml:21 主依赖（注释明说 inject 段用）→ 容器内 inject 段可跑
- **run_daily 有 `--stamp` 参数**（run_daily.py:3741）——hub 预分配传递通道成立

## 发现与处置（A1-A8 全修）

| # | severity | 发现 | 处置落点 |
|---|---|---|---|
| A1 | 高 | meta 正则 `^\d{8}-\d{4}_...` 与 stamp 事实不符（实际 `\d{8}(m\d*)?`）——写面钉死判据错则合法 meta 全被拒 | spec + design D3 + tasks 4.1 正则改 `^\d{8}(m\d*)?_[a-z0-9-]+\.meta\.json$` |
| A2 | 高 | 写面钉死「仅 meta」vs 容器需要卷内 PRD 自相矛盾（PRD 谁放进卷无答案） | 切片 1 统一 `--from-stage inject --inject-prd` 入口：PRD 经只读输入卷挂载，写卷的是容器内 pa 非 hub；proposal/D5/tasks 6.2 已改；切片 3 投递链就绪后自动 PRD 才走 critic 快路径 |
| A2b | 高（深挖连带） | inject 撞名 bump 会使 hub 预写 meta 与实际 run 目录错位 | hub 预分配 fresh stamp（扫 `prd_manifest_*.json` 撞名自增——**实际判据是 manifest 文件非 run 目录**）经 `--stamp` 传入；依赖契约「撞名才 bump」写入 durable-loop-state delta scenario |
| A3 | 高 | 队列项重派状态机有洞（run failed 后队列项何态未定义、重派 vs 新项顺序未定义） | design D2 + spec scenario：失败回 pending + attempt 计数，FIFO 天然重派优先，达上限才 failed；run failed（容器事实）与队列项 failed（放弃决定）两生命周期分离 |
| A4 | 中 | 同一 PRD 重复入队撞 run_dir 未定义 | 重复入队 = 预分配新 run（独立 run 各自 meta）不拒绝；幂等防重归切片 3 投递 API（F11）；spec scenario 已补 |
| A5 | 中 | 与 isolated-observable-execution 范畴分工未声明（隔离语义两处各写） | Runner requirement 补「隔离语义契约归 isolated-observable-execution，hub 只传参与施加」 |
| A6 | 低 | D8 可达性检查「接口位（恒 FOUND）」与 tasks 脱节 + YAGNI | design D8 收回，随切片 3 投递链引入 |
| A7 | 低 | failed_to_start 审计粒度（未启动 vs 容器闪退 journal 未及写）不分 | 初判「实现层以 container_ref 有无区分，spec 不动」；实现复审（B1）改判为执行证据统一判据，spec 已补明 |
| A8 | 低 | tasks 9.2 若改 quality.sh 与 proposal「pa 零改动」措辞冲突 | proposal 措辞收紧为「接线选并列命令，pa scripts/ 零改动」 |

## 根因归纳

- A1/A2b：契约声明未对照事实（正则凭印象写、bump 判据凭假设写）——修正过程中第二次撞上（delta 首版又假设了「run 目录判据」），验证后修正。**教训：跨 capability 契约声明的每一处判据都必须 grep 源码坐实**
- A2：切片 3 的 critic 快路径提前渗入切片 1 spec，与 F10 写面钉死相撞——切片边界的本体依赖要逐实体过
- A3/A4：状态机与身份的 stress test（「容器死了队列项何态」「同一 PRD 再来一次何态」）抓出两个未定义行为

## 实现复审（B1-B6，apply 后本体论审核，2026-10-21）

审核对象：21/21 任务落地后的 hub/ 实现代码——与本体（execution-hub spec 裁决、F10-F14、A1-A8 修正）的一致性。
裁决：用户选 B1 执行证据统一（spec 措辞改写）/ B2 修首写保真+退避 / B3-B5 顺手修 / B6 登记。全部落地（hub quality：64 passed + 1 skipped + ruff 零告警）。

### 正面确认（机制真保证，grep 坐实）

- **F10 写面钉死**：非测试写路径全集 = state_root.mkdir + run_dir.mkdir + meta write_text + env unlink（/tmp 非卷）——成立
- **F13 三点**：enqueue 固化 / start 前置 image_exists（Fake/Docker 同判）/ 无回退字面
- **A3 两生命周期分离**：fail_item（放弃决定）与 set_run_finished（容器事实）两方法两表域
- **stamp 三方对齐**：allocate 判据与容器内 inject 判据同卷同文件（prd_manifest）；A2b 预分配闭环
- **A5 范畴**：仅 --cpus/--memory/--env-file/-v 传参，不加 --user；**孤儿归档幂等**；**挂载路径 parents[3] 数学正确**；**hub→pa 零 import**

### 发现与处置（B1-B6）

| # | severity | 发现 | 处置落点 |
|---|---|---|---|
| B1 | 中 | failed_to_start 判据双轨：ImageMissing 路径按 container_ref=None、gone 路径按卷 journal（ref 非 None 也标 failed_to_start）——严格读 A7 初判措辞属违约 | 改判「执行证据」统一判据（无 docker 终态且无卷 journal → failed_to_start；有任一 → failed）——逐路径验算（ImageMissing/exited/gone 两分支/孤儿）现实现已天然符合，仅 spec 正文补明判据 + A7 行更新，代码零改 |
| B2 | 中 | daemon-down 无界记账：每 tick 新 run 行（膨胀）+ sign_meta 重写（written_at 失真——F10 首派署名时刻被刷新） | sign_meta 首写保真（已存在不重写）+ Lifecycle `dispatch_backoff` 冷却计数（仅 Exception 分支进退避，ImageMissing 终态不进；恢复成功清零；实例内存态，重启即新开始）。测试：test_sign_meta_first_write_wins / test_daemon_failure_records_once_per_retry_with_backoff / test_retry_dispatch_preserves_first_meta_written_at |
| B3 | 低 | stamp 命名空间形态迁移未登记：pa 单机全局跨 project → hub 部署 per-project 卷局部，与 delta「global namespace, across projects」措辞不同时为真 | durable-loop-state delta 场景行内注记（host `<data_root>/<project>/state` 卷局部；容器内 pa 视角与契约不变） |
| B4 | 低 | api enqueued_by 缺省 `"hub-api"` 第三身份字面量（F10 单用户形态 initiator 身份应唯一） | 端点兜底 `lc.cfg.user_id`（Lifecycle 公开 cfg）；validate_and_enqueue 的 enqueued_by 改必传，字面量消除 |
| B5 | 低 | api.validate_and_enqueue 穿透 `engine._db`（noqa: SLF001） | queue_engine 补 get_item 公共读面，穿透消除 |
| B6 | 低 | SQLite `check_same_thread=False` 并发边界靠注释声明（单写者纪律由调用方保证） | 登记：切片 1 端点顺序请求成立；真并发写须上层串行化，归切片 2 排度服务实装时处理（db.py 注释已声明） |

### 根因归纳（B 轮）

- B1：A7 初判把「实现可区分」写成了 container_ref 判据，而实现 gone 分支实际按卷证据判——本体（卷是真相，F10）与登记措辞漂移；改判方向与本体对齐而非迁就措辞
- B2：记账无界面——每 tick 记账在手动驱动下可忍，机制上无界；退避 + 首写保真把「重试」与「首派事实」分开（时间线真相仍在 runs 索引各行的 started_at）
