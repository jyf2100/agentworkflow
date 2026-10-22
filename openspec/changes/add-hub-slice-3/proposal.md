## Why

vault（mac cron）与服务器 hub 双机形态下，PRD 的 state ownership 尚无交接协议——执行链跑在 mac 是本机遗留形态，服务器执行链（切片 1-2 已落地）缺合法喂入口。F11 裁决（design doc §9）：投递 API = state ownership 交接协议，执行链分家用 `--to-stage` 物理分家（最小新概念：既有断点续跑机制升格为部署边界），不变式「一个 project 的执行链运行权威必须唯一」由机制物理保证而非纪律保证。

## What Changes

- **pa 编排器 `--to-stage prd`（既有参数复用，零代码改动）**：`--to-stage` 自初始 commit 即有（`default="dispatch"`），部署 delta = roc cron 取值 `--to-stage report` → `--to-stage prd`——mac cron 只跑 fetch→radar→prd 前半段即停，执行链（critic 起）不进 mac。参数默认值与既有行为零变化。
- **独立投递器 `scripts/deliver_prd.py`**（vault 侧）：扫描 `produced` 未 `delivered` 的 PRD → 逐个 POST hub 投递端点 → 收到确定性成功回执才写 delivered 标记。PRD 生命周期在 vault 视角 `produced → delivered` 止步（critic pass/drop/revise 结果在服务器 state，vault 不知道也不需要知道）。
- **hub 投递端点 `POST /deliver`**：接收 PRD 内容（project + 文件名 + sha256 + 正文），幂等键 `(project, 文件名, sha256)` 去重（重复投递返回既有队列项且校验落盘完整），hub 落盘 PRD 到 `<卷根>/<project>/prd/`（file-first：先落盘再入队）后入队，容器内经**既有 inject 桥**执行（挂载 `/input/` + `--from-stage inject --inject-prd`，inject 段纯机械零 LLM 规范化产 manifest，critic 消费；lifecycle 零改动）。
- **投递三态（fail-safe 三态不变式推广）**：确定性成功（2xx）才写 delivered；UNKNOWN（超时/连接断/未收响应/5xx）绝不当成功处理——不写标记，靠 vault 下轮重扫重投 + hub db/文件双层幂等去重收敛 exactly-once（UNKNOWN 重投超 K=5 次转死信防无界循环）；确定性 4xx 死信（带 error_class 区分坏数据/配置错）不再自动重投。崩溃窗口 ①POST前 ②POST后未收响应 ③收 200 未写标记 ④hub 收到未落盘 ⑤落队未响应 ⑥落盘成功未入队，全部由此闭环收敛。
- **critic「有据」单标准推广（F12）**：pa-prd-critic persona prompt 适配——「有据」= 可追溯依据（自动 PRD 的 source_path **或** PRD 自带依据链接/背景），不看出身、不按来源分流两套标准。

不做（切片 4）：手写 PRD 面板/Mattermost 提交入口、evidence 字段入口机械必填校验、报告可见性张力（T2a 未裁）。

## Capabilities

### New Capabilities
- `prd-delivery`: PRD state ownership 交接协议——`--to-stage` 物理分家、投递器与 delivered 标记、hub 幂等交接端点、投递三态与崩溃窗口收敛、critic 有据单标准。

### Modified Capabilities
- `execution-hub`: 「最小 API 面」requirement 增加 `POST /deliver` 投递端点，解除「MUST NOT 引入投递 API（切片 3）」限制（面板 UI 的 MUST NOT 保留，归切片 4）。

## Impact

- **pa（`Projects/项目推进流水线/scripts/`）**：`run_daily.py` 零改动（`--to-stage` 既有机制复用）；新增 `deliver_prd.py`（含 delivered 标记写入面，落 `.project-auto/state`）；`.claude/agents/pa-prd-critic.md` prompt 一处适配 + pa-executor 镜像重建（persona prompt 打进镜像，不重建则容器内不生效）。
- **hub（`hub/`）**：`api.py` 新端点；`db.py` 幂等去重（幂等键索引）；`queue_engine.py` 入队路径带 PRD 内容来源；清队列原语（回退序列用）。容器内执行链经既有 inject 桥（lifecycle 零改动，pa 侧零新增概念）。
- **无破坏性变更**：`--to-stage` 取值变更属 roc 侧部署动作（本仓代码零改动）、投递器不跑不影响 mac 现行形态；hub 端点纯新增。
- **凭据/安全**：投递端点为 vault→hub 机间通道，暴露面与守卫边界在 design 定（对齐 F2「公网暴露硬门槛」口径）。
