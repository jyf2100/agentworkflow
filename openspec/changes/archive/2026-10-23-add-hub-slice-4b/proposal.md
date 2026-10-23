# add-hub-slice-4b：手写 PRD 入口（面板）+ 语义事件出口

## Why

阶段 2 切片 1-4a 落完了执行主干：队列、容器生命周期、OAuth 身份、凭据保险箱、投递 API、通知通道。剩两个缺口让 hub 还不能交给同事用：①PRD 唯一的人类入口是 curl（`POST /queue` 要宿主机路径、`POST /deliver` 是机机通道）——同事浏览器零安装的入口（design doc §1「Web 面板：上传 PRD / 项目状态 / PR 列表」）不存在；②通知面只有 run 终态（4a 钉死 hub 自有事实）——critic drop、PR 开出、verify verdict 这些同事真正关心的语义事件仍不可见（4a D2 推迟项）。本切片闭合这两个缺口，完成 design doc 阶段 2 的最后一 slice。

## What Changes

- **面板 UI（新 capability `hub-panel`）**：极简服务端渲染 HTML 面（OAuth 会话强制）——①提交页：PRD 内容文本粘贴 + project 选择（data_root 扫描）+ **evidence 机械必填**（F12：入口把空依据挡在 critic 闸外）+ 会话发起人署名（复用 2b 会话身份）→ 落盘入队（复用 deliver 的幂等落盘面；粘贴 only——multipart 上传会破零新依赖面，浏览器复制粘贴即闭环）；②只读面：队列状态 / run 列表 / run 详情（journal 尾部 + 终态事实）+ **run 报告渲染（T2a 裁决落地）**。CLI/curl 双皮不变。
- **语义事件出口（新 capability `pa-event-outlet`，跨仓契约）**：pa 容器内 critic/dispatch/verify 段写结构化事件文件（`events.jsonl` 落 state 卷出口目录——**非 journal 内部格式**，4a D2「hub 不解析 pa journal」边界不动）；hub 机械读事件行转发 bot 通知（critic pass/drop、PR 开出、verify verdict）。hub 仍是零语义判定——只读契约文件机械转发。
- **通知面扩展（modified `hub-notifications`）**：①语义事件通知（事件行 → 私信发起人；critic drop/PR 开出进项目频道）；②**无 run 放弃告警**（4a D2 推迟项：`fail_item` 的 create_run 前调用点——project 非法、PRD 缺失、必读面缺失、凭据缺失、meta 署名失败——挂 notify_admin，一次性部署配置错可见）。
- **最小 API 面翻转（modified `execution-hub`）**：4a 钉死的「MUST NOT 引入面板 UI（归切片 4b）」到期翻转——面板端点群（HTML + 会话版内容提交）入端点枚举；REST 面（JSON）不动。
- **pa 侧 report 落卷（T2a 裁决落地）**：report 段产物服务器容器形态下落镜像层易失目录（`vault 根/项目推进/`）——加 state 卷双写（落盘路径 env 化 `PA_REPORT_DIR`，缺省行为零变化），面板从卷读渲染报告。roc 侧报告可见性 = 面板 + bot 摘要，vault 大脑沉淀职责不减（vault 侧 cron 链照旧）。
- **MM bot 提交（裁决：YAGNI 推迟）**：design doc §9 F10 附带设想的「同事 Mattermost 提交 PRD」不做——面板已是第一入口，bot 提交需 bot 消息监听/交互命令常驻面（新凭据面 + 新故障面），价值边际（免浏览器）撑不起成本。归档为已裁决不在范围，后续有真实需求再立 change。

## Capabilities

### New Capabilities
- `hub-panel`: 面板 UI——会话强制的手写 PRD 提交（evidence 必填）+ 队列/run/报告只读面
- `pa-event-outlet`: 容器内语义事件出口契约（pa 写 events.jsonl / hub 读转发）——跨仓接缝 spec

### Modified Capabilities
- `hub-notifications`: +语义事件通知（critic/PR/verify）+ 无 run 放弃告警
- `execution-hub`: 最小 API 面端点枚举 + 面板端点群；「MUST NOT 面板 UI」语句到期移除

## Impact

- 代码面：`hub/`（api.py 端点群 + panel 渲染模块 + notify 扩展 + lifecycle 挂点）、`pa/scripts/run_daily.py`（事件出口写 + report 双写，均 flag/缺省零变化）、`hub/deploy/hub.env.example`（如需）
- 跨仓契约：`events.jsonl` schema（pa 写 / hub 读）是本切片产出的唯一新接缝——版本字段内嵌，schema 演进向后兼容
- 风险：pa 侧改动触碰 add-stage-output-contract-repair（活跃 change）相邻区域——本切片 pa 侧改动钉最小面（事件出口函数 + report 落点 env 化），不动 stage 输出契约本体
- 测试：全离线（FakeRunner + FakeMMClient + 契约文件 fixtures）；面板用 FastAPI TestClient 会话态
