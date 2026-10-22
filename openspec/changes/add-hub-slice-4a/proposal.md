# add-hub-slice-4a：hub 观察面与通知通道三件套

## Why

hub 主体（切片 1/2a/2b/3）已全部落地，部署在即，但观察面有三个口子：①读端点（GET /queue、GET /runs/{run_id}）与回退原语（DELETE /queue/{item_id}）零守卫——内网现状可容忍，但 F2 已点名收紧归切片 4，部署后第一个暴露的就是它们；②run 终态只有主动查询可见——cron 03:17 无人值守跑，失败要到次日人查才知道（design doc §3「bot 通知」的动机）；③镜像钉 tag 咬合（F13）无陈旧告警——template repo 升级后服务器镜像停在旧 tag，无声漂移。三件全部是纯 hub 侧机械活，离线可开发验证（TestClient / Fake 接缝现成），配合部署尾巴即阶段 2 第一波价值兑现。

## What Changes

- **读面收紧（F2）**：`GET /queue`、`GET /runs/{run_id}`、`DELETE /queue/{item_id}`（写面回退原语，同敏感级一并收）挂 OAuth 会话守卫——配置 MM（auth 已配置态）时强制会话，未配置 = 2a 回退态保持现状无守卫（「不配 = 零变化」既有哲学）。F2 完整口径（公网暴露 = token + socket proxy 硬门槛）仍是部署期门，本切片收的是内网读面。
- **MM notify bot（F14）**：新增 `hub/notify.py`——bot token 推通知（独立凭据实体，与 OAuth app 分离：身份与通道两范畴）。事件面 = **hub 自有事实**：run 终态（succeeded / failed / failed_to_start）。hub 从自身 run 终态行读事实机械转发，不产生语义判定；**语义事件（critic pass/drop、PR 开出、verify verdict）与无 run 行的队列项放弃（派发前失败）推迟切片 4b**——前者需要 pa 容器侧事件出口契约，hub 不解析 pa journal 内部格式（范畴边界）；后者多为一次性部署配置错（stderr + 读面可见）。
- **通知路由**：per-user 私信默认——收件人按队列项发起人值形态三代分流（mm_user_id 直发 / username 解析 mm id 后发 / `vault-deliver` 回落配置单用户）；per-project 频道广播可选（env 配置映射，仅失败类终态）。
- **fail-open**：通知失败记日志绝不阻断流水线（观察面性质——通知丢失的兜底是 GET 面查询）。
- **镜像陈旧检测（F13 推迟项）**：周期比对 remote template repo 最新 release tag vs `cfg.image_tag_default`，不一致经 bot 通道告警；remote 查询三态分流——FOUND 比对 / 404（repo 配错，确定性配置错）高声告警一次 / 瞬时 UNKNOWN（网络错、5xx）静默下轮再查。
- **新 env**：`HUB_MM_BOT_TOKEN`（bot 凭据）、`HUB_IMAGE_TEMPLATE_REPO`（陈旧检测比对源，不配 = 检测关闭）、`HUB_MM_PROJECT_CHANNELS`（可选频道映射）、`HUB_STALE_CHECK_TICKS`（检测周期，可选）；`deploy/hub.env.example` 同步。

## Capabilities

### New Capabilities

- `hub-notifications`: hub→人的通知通道——bot 凭据与推递接缝、run 终态/队列项放弃事件面、per-user 私信 + 可选频道路由、fail-open 语义、镜像陈旧告警。

### Modified Capabilities

- `execution-hub`: 「最小 API 面」requirement 变化——读端点与回退原语在 auth 已配置态强制会话（未配置回退态不变），新增守卫 scenario。

## Impact

- **代码**：`hub/`（api.py 守卫、notify.py 新模块、lifecycle.py 终态挂点、serve.py 装配、config.py env 面、daemon.py 陈旧检测挂点）；零 pa 侧改动（范畴边界：hub 不依赖 pa 内部格式）。
- **测试**：test_api.py（守卫）、test_notify.py 新、test_lifecycle.py（终态触发通知 + fail-open）、陈旧检测 fake remote 注入。全部离线（mock MM webhook，design doc §7 接缝）。
- **部署**：MM 管理员创建 bot + 填 env（roc 侧动作，RUNBOOK 记）；`bash hub/quality.sh` 质量门不变。
- **兼容**：未配 bot token = 通知零行为（None 注入，流水线零变化）；未配 MM = 读面守卫零变化——两 flag-off 形态均零扰动。
