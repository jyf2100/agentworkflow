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
| A7 | 低 | failed_to_start 审计粒度（未启动 vs 容器闪退 journal 未及写）不分 | 登记实现层以 container_ref 有无区分，spec 不动 |
| A8 | 低 | tasks 9.2 若改 quality.sh 与 proposal「pa 零改动」措辞冲突 | proposal 措辞收紧为「接线选并列命令，pa scripts/ 零改动」 |

## 根因归纳

- A1/A2b：契约声明未对照事实（正则凭印象写、bump 判据凭假设写）——修正过程中第二次撞上（delta 首版又假设了「run 目录判据」），验证后修正。**教训：跨 capability 契约声明的每一处判据都必须 grep 源码坐实**
- A2：切片 3 的 critic 快路径提前渗入切片 1 spec，与 F10 写面钉死相撞——切片边界的本体依赖要逐实体过
- A3/A4：状态机与身份的 stress test（「容器死了队列项何态」「同一 PRD 再来一次何态」）抓出两个未定义行为
