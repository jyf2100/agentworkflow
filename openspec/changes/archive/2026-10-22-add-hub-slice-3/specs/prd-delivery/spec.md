## ADDED Requirements

### Requirement: 执行链物理分家（--to-stage 部署取值变更）
`run_daily.py` 既有 `--to-stage` 参数（自初始 commit 存在，`default="dispatch"`——不传时本就不跑 report，cron 经 `run_cron.sh` 显式 `--to-stage report`）SHALL 被复用为部署边界，本 change MUST NOT 改动 run_daily 阶段控制代码。mac cron 服务器形态 SHALL 将 cron 调用取值从 `--to-stage report` 变更为 `--to-stage prd`（fetch→radar→prd 产出 PRD 后停，执行链 critic 起不进 mac）——不变式「一个 project 的执行链运行权威必须唯一」由命令行层面物理保证（mac 侧根本没有后半段可跑），非纪律保证。参数默认值（dispatch）与既有行为 SHALL 零变化。

#### Scenario: to-stage prd 前半段产出即停（既有行为钉死）
- **WHEN** 以 `--to-stage prd` 运行流水线且 prd 段产出 PRD
- **THEN** 流水线正常退出（journal 记录完整前半段），critic/dispatch/report 段不执行、不产生其产物

#### Scenario: 默认值零变化
- **WHEN** 不传 `--to-stage` 运行流水线
- **THEN** 行为与既有形态逐字节一致（跑到 dispatch 止，report 不跑——默认值本就是 dispatch，非完整 7 段）

### Requirement: 投递器与 delivered 标记（vault 侧）
独立投递器 `deliver_prd.py` SHALL 扫描已产出但未投递的 PRD（`produced` 状态），逐个 POST 至 hub 投递端点，仅在收到确定性成功回执（2xx + 队列项标识）后写 delivered 标记。PRD 生命周期在 vault 视角 SHALL 为 `produced → delivered` 止步——critic pass/drop/revise 结果在服务器 state，vault 不知道也不需要知道。投递器 SHALL 幂等可重跑：已 delivered 的 PRD 不再投递。死信行 SHALL 带 `error_class`（`data_4xx` 坏数据 / `config_4xx` 部署或凭据配错），`config_4xx`（401/403/404/405——token 失配/无权限、URL 错、hub 未升级）SHALL 高声告警（stderr + 非零退出），区别于单份坏数据。

#### Scenario: 成功投递写标记
- **WHEN** 投递器 POST 某未 delivered 的 PRD 且收到 2xx 回执
- **THEN** 该 PRD 标记 delivered；下轮扫描不再投递

#### Scenario: 扫描范围止于 delivered
- **WHEN** 投递器运行而某 PRD 已 delivered
- **THEN** 该 PRD 被扫描跳过，不产生重复 POST

### Requirement: hub 幂等交接端点
hub SHALL 提供 `POST /deliver` 投递端点：接收 project、PRD 文件名、sha256 与 PRD 正文，以 `(project, 文件名, sha256)` 为幂等键去重——重复投递 SHALL 返回既有队列项标识而不重复入队（响应可区分首次/重复）。首次投递 SHALL 将 PRD 落盘到 `<卷根>/<project>/prd/<文件名>`（写面约束见 execution-hub F10 ②），落盘成功并经 sha256 校验后创建队列项（队列项 PRD 路径 = 落盘位）——**落盘 MUST 先于队列项持久化**（file-first 顺序钉死：若先入队后落盘，中间崩溃将产生「队列项存在但 PRD 永不补写」的不可恢复态）。重复投递命中幂等键时 SHALL 先校验落盘文件存在且 sha256 一致（缺失或异容则补写/改写落盘）再返回重复响应——文件层与 db 层双层幂等。同文件名已存在且 sha 相同（同名同容）SHALL 幂等复用既有文件、不重写。容器内执行链 SHALL 经既有 inject 桥起跑：队列项 PRD 路径即投递落盘位，派发挂载 `/input/` + `--from-stage inject --inject-prd`（lifecycle 既有机制零改动），inject 段（纯机械、零 LLM）读挂载 PRD、规范化落 `state/prd/<project>/` 标准位并产 `prd_manifest_<stamp>.json`，critic 段经既有 manifest 拾取消费。投递入队的队列项 SHALL 以 `enqueued_by="vault-deliver"`（机机通道来源字面量，非人身份）标识来源、`initiator` 留空——派发回落 enqueued_by，故其 meta 署名 initiator 为 `vault-deliver`（机机通道标识，审计面可辨识 vault 投递发起的 run，属 initiator 范畴「人或机机通道标识」的有意扩展）。

#### Scenario: 首次投递落盘入队
- **WHEN** vault 投递一份新 PRD（project/文件名/sha256/正文）至 `POST /deliver`
- **THEN** hub 先落盘 PRD（sha 校验通过）再创建队列项并返回其标识；该 run 在容器内经 inject 桥起跑（inject 产 manifest，critic 消费）

#### Scenario: 重复投递幂等返回且校验落盘完整
- **WHEN** 同一 `(project, 文件名, sha256)` 再次 POST /deliver
- **THEN** 返回既有队列项标识（标记为重复）；返回前校验落盘文件存在且 sha 一致，缺失/异容则先补写

#### Scenario: 落盘先于入队（file-first 崩溃窗口）
- **WHEN** hub 落盘成功后、队列项持久化前崩溃
- **THEN** 重投走首次路径：落盘命中同名同容幂等复用，队列项恰好创建一次

#### Scenario: 同名异容以 sha256 区分
- **WHEN** 同 project 同文件名但内容不同（sha256 不同）的 PRD 投递
- **THEN** 视为不同 PRD：落盘名追加 `-<sha8>` 后缀（见 execution-hub F10 ② 撞名规则），各建独立队列项

### Requirement: 投递三态与崩溃窗口收敛
投递响应 SHALL 遵循三态不变式（fail-safe-dispatch 三态的推广）：**确定性成功**（2xx + 回执）才写 delivered 标记；**UNKNOWN**（超时、连接断、未收响应、5xx 类不可判态）绝不当成功处理——不写标记，下轮重扫重投；**确定性 4xx 拒绝**（sha 不匹配、body 超限、格式坏、401/404/405）SHALL 写死信行不再自动重投（详见投递器 requirement 的 error_class）。UNKNOWN 重投 SHALL 带尝试计数：同一 PRD 连续 UNKNOWN 达 K 次（K=5）SHALL 转 failed 死信行并告警——防确定性 5xx（如 handler bug）无界重投且零持久痕迹（无静默失败）。全部崩溃窗口（①POST 前 ②POST 后未收响应 ③收 200 未写标记 ④hub 收到未落盘 ⑤落队未响应 ⑥落盘成功未入队）SHALL 不需要任何额外协调机制，仅靠「vault 重扫重投 + hub 幂等去重（db 层 deliver_key + 文件层同名同容复用）」收敛至 exactly-once 入队。

#### Scenario: UNKNOWN 不标 delivered 下轮重投
- **WHEN** POST 超时、连接中断或收到 5xx（响应 UNKNOWN）
- **THEN** 该 PRD 不写 delivered 标记；下轮投递器重投；hub 幂等键保证不重复入队

#### Scenario: 收 200 未写标记崩溃后重投收敛
- **WHEN** 投递器收到 2xx 回执后、写 delivered 标记前崩溃
- **THEN** 下轮扫描视为未投递而重投；hub 端幂等去重返回既有队列项，最终恰好一个队列项

#### Scenario: 确定性 4xx 死信不再自动重投
- **WHEN** POST 收到确定性 4xx（sha 不匹配 / body 超限 / 格式坏 / 401 token 失配）
- **THEN** 写 failed 死信行（带 error_class）且不再自动重投；stderr 告警；人工修后经 `--retry` 显式重投

#### Scenario: UNKNOWN 重投超限转死信
- **WHEN** 同一 PRD 连续 UNKNOWN 重投达 K=5 次（如 hub handler 确定性 5xx）
- **THEN** 写 failed 死信行（error_class=unknown_exhausted）并告警，不再无限重投

### Requirement: critic 有据单标准（可追溯依据）
pa-prd-critic SHALL 以「可追溯依据」为唯一有据标准：自动 PRD 的信号出处（source_path）**或** PRD 自带依据链接/背景，二者任一满足即为有据。critic MUST NOT 按出身（自动/手写）分流两套标准——同一「有据」概念、两种满足方式（单标准 + 入口双层把守的闸前半）。

#### Scenario: 自带依据的 PRD 不因无 source_path 拒
- **WHEN** PRD 无 source_path 但自带可追溯依据链接/背景，进 critic 审查
- **THEN** critic 按「有据」正常评估，不因缺 source_path 一票 revise/drop

#### Scenario: 无任何可追溯依据仍拦截
- **WHEN** PRD 既无 source_path 也无可追溯依据
- **THEN** critic 按「无据」处理（revise/drop），闸口水位不降
