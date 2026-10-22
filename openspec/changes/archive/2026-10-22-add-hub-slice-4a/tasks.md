# tasks：add-hub-slice-4a

## 1. 读面收紧（D1，spec: execution-hub delta「读面会话守卫」）

- [x] 1.1 api.py 加 `_require_session(request)` helper（`mm_configured` 才检查，复用 `_session_identity`，无会话 401），挂到 `GET /queue`、`GET /runs/{run_id}`、`DELETE /queue/{item_id}` 三端点
- [x] 1.2 test_api.py 补守卫测试：auth 已配置态无会话 401 / 有效会话 200（session cookie 经 `auth.sign_token` 直造）；未配置回退态三端点无守卫照 200（零变化）

## 2. 通知模块（D3/D4/D5/D7，spec: hub-notifications「bot 通道」「run 终态通知」「fail-open」「频道广播」）

- [x] 2.1 新建 `hub/notify.py`：`Notifier` 类（cfg + 注入 mm client，DM 通道解析封装私有方法）+ `FakeMMClient`（按 `(recipient, text)` 记调用清单）+ 未配 bot token / base_url = 关闭态构造（None 语义）
- [x] 2.2 `Lifecycle.__init__` 加 `notifier=None` 参数；`set_run_finished` 五调用点收敛为 `_finish_run(...)` helper（落库 + 通知，通知 try/except 全捕获 fail-open + stderr 记日志）
- [x] 2.3 通知消息构造 + 收件人解析（D4 值形态三代分流：mm_user_id 26-hex 直发 / username 经 `users/username/{name}` 解析 mm id 后发 + 内存缓存、解析失败记日志 fail-open / `vault-deliver` 回落 `cfg.user_id` 走 username 路径；消息含 project / run 标识 / 终态 / exit code 或失败类别，`initiator` 进文本不参与路由）
- [x] 2.4 频道广播（D5：`HUB_MM_PROJECT_CHANNELS` 映射解析进 HubConfig；仅 failed / failed_to_start 广播，与私信并行互不依赖）
- [x] 2.5 test_notify.py 新建：关闭态零行为 / Fake 记推递 / fail-open（注入崩溃 notifier 不影响终态落库）/ 值形态三代分流（mm_user_id 直发、username 解析路径、解析失败可见丢）/ 机机字面量回落配置单用户 / 频道失败不影响私信
- [x] 2.6 test_lifecycle.py 扩展：容器 exited 非零 → failed 通知发起人；exited 0 → succeeded 通知；ImageMissingError → failed_to_start 通知（FakeRunner 全离线）

## 3. 镜像陈旧检测（D6，spec: hub-notifications「镜像陈旧告警」）

- [x] 3.1 新建 `hub/stale_check.py`：`latest_release_tag(repo, client=None) -> tuple[str | None, str | None]`（gh api；三态：(tag, None)=FOUND / (None, "config_4xx")=404 确定性配置错 / (None, None)=UNKNOWN）+ `check_stale(cfg, seen, client=None)`（不一致告警 + 404 高声告警 + 内存去抖）
- [x] 3.2 daemon.py `run_forever` 挂低频调用（每 `HUB_STALE_CHECK_TICKS` tick 一次，默认 720；告警经 notifier，检测异常记日志续跑不崩循环）
- [x] 3.3 test：fake remote 更新 → 告警一次 + 同组合去抖 / fake remote 404 → 配置错高声告警一次不进比对 / remote 网络错或 5xx → 无告警下轮再查 / 未配置 repo → 零行为；gh CLI 缺失 warn + 检测关闭

## 4. 配置与装配（D7/D8）

- [x] 4.1 config.py：`HUB_MM_BOT_TOKEN`、`HUB_IMAGE_TEMPLATE_REPO`、`HUB_MM_PROJECT_CHANNELS`、`HUB_STALE_CHECK_TICKS` 四 env 解析进 HubConfig（可选面 None 默认）+ test_config 补
- [x] 4.2 serve.py `build_app` 装配：bot token + base_url 齐 → 构造 Notifier 注入 Lifecycle（stale_check 复用）；缺 = None 注入零行为
- [x] 4.3 `deploy/hub.env.example` 补四项注释态（含 bot 创建前置说明与回退两级 unset 说明）

## 5. 收口

- [x] 5.1 全量质量门：`bash hub/quality.sh`（compileall + pytest + ruff 三级全绿）
- [x] 5.2 RUNBOOK 增「通知与陈旧检测部署节」（bot 创建步骤、env 填值、两级回退、MM 真机联调项）
- [x] 5.3 CONTEXT.md 术语表补词条（通知 (notify) 事件面 = hub 自有事实 / bot 通道独立凭据 / 陈旧检测三态）——对齐 deliver 词族先例格式
