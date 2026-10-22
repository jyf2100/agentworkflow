# design：add-hub-slice-4a（hub 观察面与通知通道三件套）

## Context

hub 切片 1/2a/2b/3 全落：队列 + 容器生命周期 + OAuth 会话 + 凭据保险箱 + 投递端点。现状三个观察面口子：读端点与回退原语零守卫（`api.py` 的 `GET /queue` / `GET /runs/{id}` / `DELETE /queue/{id}` 无任何检查）；run 终态只有主动查询可见；镜像钉 tag（F13）无陈旧告警。2b 已备会话基元（`auth.verify_session` / `_session_identity`，api.py 内已有守卫端点先例 `PUT /auth/credentials`）；run 终态的产生点集中在 `lifecycle.py` 的 `set_run_finished` 全部调用处（reconcile_running 两分支、tick_dispatch 两失败分支、orphan 归档）；`runner.image_exists` 接缝已备三态分流先例。design doc 本体依据：§2 hub 六职责含「bot 通知」、§3 MM 集成事件集、§9 F2/F13/F14。

## Goals / Non-Goals

**Goals:**
- 读面收紧：三端点（GET 两 + DELETE 回退原语）auth 已配置态强制会话，未配置回退态零变化
- MM notify bot：独立凭据实体 + 注入接缝 + run 终态 per-user 私信 + 可选频道广播 + fail-open
- 镜像陈旧检测：remote release tag vs 配置默认 tag 比对告警，三态精神
- 全部离线可开发验证（TestClient / Fake 注入），零 pa 侧改动

**Non-Goals:**
- 语义事件通知（critic pass/drop、PR 开出、verify verdict）——需容器侧事件出口契约，归切片 4b；hub 不解析 pa journal 内部格式（范畴边界）
- 面板 UI、手写 PRD 入口、T2a 报告可见性——切片 4b
- F2 完整口径（公网 token + socket proxy 硬门槛）——部署期门
- 自动拉取/重建镜像——告警是唯一动作，升级是人工决定
- 通知持久化/at-least-once——观察面 fail-open，丢失兜底是读面查询

## Decisions

### D1 读面守卫形态：配置态强制、回退态不变（复用 `_session_identity`）
守卫判据 = `auth.mm_configured(cfg)`：已配置（MM 四项齐）时三端点强制有效会话（401 拒绝），未配置时跳过守卫检查保持现状。理由：与 2b「不配 = 2a 回退态零变化」哲学同构——守卫强度跟随 auth 配置面，不引入独立开关（避免第四个 env 的状态组合爆炸）。备选否决：①独立 env `HUB_READ_GUARD=1`——多一个开关两处配置易漂移，且「配了 MM 却不守卫读面」无合理场景；②始终强制——杀死 2a 单用户内网形态（cli/curl 巡检全断）。实现为 api.py 内一个 `_require_session(request)` helper（`mm_configured` 才检查，复用 `_session_identity`），三端点各加一行——不引 FastAPI middleware（端点面小，显式守卫与 `/auth/me`、`PUT /auth/credentials` 既有先例一致）。
DELETE /queue/{item_id} 一并收紧：它是回退序列原语（写面，比读更敏感），且本次 delta 顺带把它补进「最小 API 面」端点枚举（slice-3 实装了端点但 sync 时漏进清单——spec 漂移修正）。

### D2 事件面裁剪：run 终态 only，语义事件与无 run 放弃推迟 4b
本切片通知事件 = run 终态（succeeded / failed / failed_to_start）——hub DB 自有 run 终态行，机械读行转发，零 pa 格式依赖。成功终态也通知（超出 design doc §3 原事件集只含失败）：对齐 PA_HEARTBEAT 心跳哲学（无头运行「通知断了 = 系统挂了」）+ 会话入队用户在等结果。**已知缺口（有意）**：无 run 行的队列项放弃（派发前失败——`fail_item` 的六个 `create_run` 前调用点：project 非法、PRD 缺失、必读面缺失、凭据缺失、meta 署名失败）不在挂点内——多为一次性部署配置错，stderr 日志与读面可见；与语义事件一并归 4b 事件出口统一设计（届时挂点统一到终态转移）。同族取舍：孤儿 run 归档（hub 崩溃窗口残留目录）的 `enqueued_by` 不可考——item 查不到时通知回落配置单用户（保证告警不丢；真实发起人经 run 目录与队列读面可查）。design doc §3 设想的 critic/PR/verify 事件需要 hub 消费容器内语义产物：要么解析 pa journal（耦合内部格式，journal schema 变更会静默破坏通知），要么定义容器出口事件文件（跨仓接缝契约）。两者都超出「hub 只做机械活」的舒适区，推迟 4b 与手写入口一起设计（届时事件出口契约可同时服务面板）。裁剪的代价：cron 03:17 投递链的 critic drop 通知要等 4b——短期兜底是 run 级通知（critic drop 的 run 最终 succeeded，PRD 未过闸在 run 报告里可见）。

### D3 notifier 挂点：Lifecycle 终态落库后单次调用（构造注入 None-able）
`Lifecycle.__init__` 加 `notifier=None` 参数；`set_run_finished` 的五个调用点后各跟一次 `self._notify(...)`（封装成 `_finish_run(...)` 私有 helper 统一落库+通知，防将来新调用点漏挂）。挂点在落库**之后**且 try/except 全捕获（fail-open）——通知异常绝不回滚终态。「至多一次」由挂点结构保证：这五个点每 run 恰执行一次（reconcile 幂等的前提是 run 行已离开 running；orphan 归档只对新发现目录执行），无轮询重发路径。备选否决：①daemon 轮询扫 db 新终态——引入「已通知」标记列（新持久写面）+ 轮询窗口，复杂度不值；②notifier 独立线程消费事件队列——过度设计。None-able 注入：未配 bot token = None = 分支跳过（零行为，对齐 vault 参数先例）。

### D4 收件人解析：值形态三代 + Notifier 内分流
`enqueued_by` 值形态三代（各代语义，非任意字符串）：**mm_user_id**（2b 会话入队——26 字符十六进制，Mattermost 公开事实）、**username**（2a env 兜底——`POST /queue` 无会话时 `enqueued_by = cfg.user_id`，如 `"roc"`，是 username **非** mm_user_id；机机回落目标同此形态）、**`vault-deliver`**（slice-3 机机字面量）。路由：mm_user_id 直发；username 经 `users/username/{name}` 解析 mm id 后发（解析结果内存缓存于 Notifier 生命周期内）；`vault-deliver` 回落 `cfg.user_id`（走 username 解析路径）。分流判据 = 26-hex 形态检查（分流用途，非拒收校验）。**解析失败记日志 fail-open**——可见的丢（stderr 留痕），不是静默吞；这修掉了初稿「白名单直发」会把 `"roc"` 当 mm_user_id 发送失败而无声丢失的洞。`initiator`（人读名）不参与路由只进消息文本。

### D5 频道广播：env 映射 + 仅失败类终态
`HUB_MM_PROJECT_CHANNELS` 形如 `proj-a:chan-id-1,proj-b:chan-id-2`（env 解析进 HubConfig，纯机械）。仅 failed / failed_to_start 广播——成功进频道是噪音（每天 cron 全绿推频道会训练人忽略频道）。频道与私信并行互不依赖（各自 try/except）。备选否决：per-project 配置文件落 data_root——配置面应集中在 env（compose env_file 单点，hub.env.example 即文档）。

### D6 陈旧检测：daemon 低频挂点 + 注入 remote 查询 + 三态分流 + 内存去抖
新模块 `stale_check.py`：`latest_release_tag(repo, client=None) -> tuple[str | None, str | None]`（(tag, None)=FOUND / (None, "config_4xx")=确定性 404 / (None, None)=UNKNOWN）+ `check_stale(cfg, state, client=None)`。挂点 = `daemon.run_forever` 每 N tick 调一次（N 可配 `HUB_STALE_CHECK_TICKS` 默认 720 ≈ 5s tick 下每小时一次）——挂 daemon 循环而非独立线程（单写者约束不动，daemon 已是常驻骨架）。remote 查询用 `gh api repos/{repo}/releases/latest`（gh CLI 已是 hub 运行时依赖面——容器内已装；备选 httpx 直打 GitHub API 需新鉴权面，否决）。查询三态分流（对齐 slice-3 `_CONFIG_4XX` 教训）：FOUND 参与比对；**404（repo 名配错/无 release）= 确定性配置错**，高声 stderr 告警一次（去抖）提示检查 `HUB_IMAGE_TEMPLATE_REPO`——绝不静默（初稿「非 200 一律 UNKNOWN」会让配错 repo 名的检测永远无声失效）；网络错/5xx = UNKNOWN 记日志下轮再查。去抖 = 进程内存 dict 记已告警组合（陈旧差异与配置错共用去抖面；重启重告可接受——告警重发好过漏发；不落 db，观察面无持久化）。

### D7 通知模块形态与测试接缝
`notify.py`：`Notifier` 类（cfg + 注入 mm client）+ `FakeMMClient`（测试记调用清单，对齐 auth.D8 / runner.FakeRunner 双实现先例）。MM 推递 API：`POST {base_url}/api/v4/posts`（bot token Bearer）。**实现期注意**：MM DM 不是「post 直达 user」——需先解析/创建 DM channel（direct channel create 端点）拿到 channel_id 再发 post。接缝策略：DM 通道解析封装进 Notifier 私有方法，Fake 盖住整个 send 面、按 `(recipient, text)` 记录——测试只锁 `(recipient, text)` 契约，不锁 API 调用序列（两步实现细节实现期对 MM 文档定，真机联调归部署尾巴）。env 三新项：`HUB_MM_BOT_TOKEN`、`HUB_IMAGE_TEMPLATE_REPO`、`HUB_MM_PROJECT_CHANNELS`（+ `HUB_STALE_CHECK_TICKS` 可选）——hub.env.example 同步四项注释态。

### D8 装配与回退
`serve.py build_app` 构造 `Notifier`（cfg 有 bot token + base_url 才造，否则 None）注入 Lifecycle 与 stale_check；api.py 不感知 notifier（守卫外零改动）。回退 = unset `HUB_MM_BOT_TOKEN` 重启——通知全静默、守卫仍随 MM 四项走（D1 独立）。两级 flag-off：只关通知（unset bot token）/ 全关回 2a（unset MM 四项）——两形态都零行为验证。

## Risks / Trade-offs

- [MM DM 发送 API 细节（channel 解析两步）实现期才坐实] → Fake 接缝按 (recipient, text) 契约盖住 send 面，测试不锁 API 序列；真机联调归部署尾巴（roc 侧），hub.env.example 注释写清前置（bot 创建 + 权限）
- [run 级通知的语义粒度粗（critic drop 不可见；无 run 放弃——派发前凭据/必读面缺失——同样不可见）] → 4b 事件出口契约统一补齐；短期 run succeeded + 读面报告 + stderr 日志兜底
- [gh CLI 在 hub 容器内未必可用（陈旧检测）] → ImageMissingError 先例：检测依赖缺失 = 启动时 warn + 检测静默关闭（不崩 serve）；镜像构建时确保 gh 在（部署期验证项）
- [守卫收紧破坏既有内网巡检脚本（若有人裸 curl GET）] → 内网 2a 回退态不变（D1）；配了 MM 的部署收 401 是预期行为变更，RUNBOOK 记（登录流程 = /auth/authorize）
- [去抖内存态重启丢失 → 重启后重复告警一条] → 接受（告警重发好过漏发）；若烦人再升 db 持久（YAGNI）
- [DELETE 端点进 spec 枚举是顺带修正（非本切片新端点）] → delta MODIFIED 整块携带，archive sync 后主 spec 与实现一致——漂移窗口关闭

## Migration Plan

1. 实施全离线（quality.sh 三级门）——无部署依赖
2. 部署期（roc 侧，RUNBOOK 增节）：MM 管理员创建 bot（token 进 hub.env）+ 填 `HUB_IMAGE_TEMPLATE_REPO` + 可选频道映射 → 重建镜像（含 gh CLI 验证）→ 滚动重启 hub
3. 回退一级：unset `HUB_MM_BOT_TOKEN`（通知静默，守卫不变）；回退二级：unset MM 四项（回 2a 形态，读面守卫也退）

## Open Questions

（实现期解决，不阻塞 propose）MM DM channel 解析的具体 API 序列（D7 已定接缝策略）；`HUB_STALE_CHECK_TICKS` 默认值随部署实测 tick 周期校准。
