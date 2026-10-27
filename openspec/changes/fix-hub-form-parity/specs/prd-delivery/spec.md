## MODIFIED Requirements

### Requirement: hub 幂等交接端点
hub SHALL 提供 `POST /deliver` 投递端点：接收 project、PRD 文件名、sha256 与 PRD 正文，以 `(project, 文件名, sha256)` 为幂等键去重——重复投递 SHALL 返回既有队列项标识而不重复入队（响应可区分首次/重复）。首次投递 SHALL 将 PRD 落盘到 `<卷根>/<project>/prd/<文件名>`（写面约束见 execution-hub F10 ②），落盘成功并经 sha256 校验后创建队列项（队列项 PRD 路径 = 落盘位）——**落盘 MUST 先于队列项持久化**（file-first 顺序钉死：若先入队后落盘，中间崩溃将产生「队列项存在但 PRD 永不补写」的不可恢复态）。重复投递命中幂等键时 SHALL 先校验落盘文件存在且 sha256 一致（缺失或异容则补写/改写落盘）再返回重复响应——文件层与 db 层双层幂等。同文件名已存在且 sha 相同（同名同容）SHALL 幂等复用既有文件、不重写。容器内执行链 SHALL 经既有 inject 桥起跑：队列项 PRD 路径即投递落盘位，派发挂载 `/input/` + `--from-stage inject --inject-prd --to-stage report`（inject 桥与 inject→critic→dispatch 链路为 lifecycle 既有机制；`--to-stage report` 为 2026-10 段范围对齐增补，使执行链跑满后半段含 report 双写出口——段范围 requirement 见 execution-hub「执行链段范围（inject→report）」），inject 段（纯机械、零 LLM）读挂载 PRD、规范化落 `state/prd/<project>/` 标准位并产 `prd_manifest_<stamp>.json`，critic 段经既有 manifest 拾取消费。投递入队的队列项 SHALL 以 `enqueued_by="vault-deliver"`（机机通道来源字面量，非人身份）标识来源、`initiator` 留空——派发回落 enqueued_by，故其 meta 署名 initiator 为 `vault-deliver`（机机通道标识，审计面可辨识 vault 投递发起的 run，属 initiator 范畴「人或机机通道标识」的有意扩展）。

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
