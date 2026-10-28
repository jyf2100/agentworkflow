# add-mm-channel-intake — tasks

## 1. db 层：绑定表与 seed 迁移

- [x] 1.1 `hub/db.py`：新表 `project_channels`（project TEXT UNIQUE NOT NULL、channel_id TEXT UNIQUE NOT NULL（26 字符 id 机械判据）、bound_by TEXT NOT NULL（username）、bound_at TEXT NOT NULL（ISO8601 UTC））+ CRUD 原语（get_by_channel / get_by_project / upsert_bind（双向换绑对称覆盖，单事务原子——旧绑定必清，返回被替换旧绑定）/ unbind_by_channel）；建表挂既有 schema 初始化路径（重启幂等）
- [x] 1.2 `hub/db.py`：`HUB_MM_PROJECT_CHANNELS` env 一次性 seed 迁移——启动时表空则整批 seed（bound_by 记 `seed-migration`、bound_at = seed 执行时刻 ISO8601 UTC；channel_id 形态校验不过的条目拒入并 stderr 可见），表非空跳过；seed 后该 env 键不再被任何读点消费（`hub/notify.py` 既有 `cfg.project_channels` 消费点改读绑定表——本任务只做迁移写侧，读侧切换在 6.x）
- [x] 1.3 `hub/test_db.py`：绑定 CRUD + 1:1 约束（双 UNIQUE 撞库拒绝）+ 双向换绑覆盖（project 换频道 / 频道顶绑，事务原子——旧绑定必清）+ seed 幂等（表空 seed / 表非空跳过 / env 缺失零行为 / 非法 channel_id 形态拒入）全覆盖

## 2. config 与凭据

- [x] 2.1 `hub/config.py`：新增 `HUB_MM_SLASH_TOKEN` 配置项（缺省 None = 入站关闭）；`hub/deploy/hub.env.example` 补注释段（MM 管理台建 slash command 时下发；不配 = 入站关闭零行为；三凭据实体注记：OAuth 登录 / bot token 出站 / slash token 入站）+ **`HUB_MM_PROJECT_CHANNELS` 旧键废止注记**（该 env 已废止——首次启动 seed 迁移进绑定表后不再读取，继续配置静默无效；防部署者配 env 以为生效）
- [x] 2.2 `hub/test_api.py` 或新增：未配 slash token 端点 503 / 配了但 token 不匹配 401 的最小契约测试（先行锁行为，端点本体 3.x 落地）

## 3. MM 频道成员查询（授权面）

- [x] 3.1 `hub/notify.py`（或新 `hub/mm_intake.py`）：MMClient 契约扩一个 `channel_member_ids(channel_id) -> list[str] | None`（None = UNKNOWN——网络错/5xx/**403 bot 无权限（判据不可用，非授权结论）**；404 频道不存在抛专用异常）；HttpMMClient 实现 `GET /channels/{id}/members` 分页取全（per_page=200 翻页）；FakeMMClient 同步扩可编程面（成员集 / crash_on_members / forbidden 403 / not_found 404）
- [x] 3.2 成员查询记忆化缓存（`(channel_id, user_id)` 在成员集判定级或成员集级缓存，TTL 60s 进程内存，仅 FOUND 可缓存——零持久写面不变式）；三态语义单测：在集→授权 / 不在集→拒绝 / None 或 403（UNKNOWN）→ fail-closed 拒绝且回执「校验暂不可用」（绝不旁路、绝不回执「非成员」）/ 404→「检查绑定」回执

## 4. 入站端点 POST /mm/intake

- [x] 4.1 `hub/api.py`：slash command 解析（x-www-form-urlencoded：token/channel_id/user_id/user_name/text）+ token 恒时校验 + 频道反查绑定推断 project（未绑定 ephemeral 拒绝含 bind 用法）
- [x] 4.2 授权 + evidence 闸：频道成员校验（3.x 三态）；text 含 `出处:` 或 `出处：`（半/全角冒号两形态，前后空白容忍）机械判据（缺则 ephemeral 拒绝含格式示例；全角形态测试必须覆盖——中文输入法高频）；超长拒绝（`_DELIVER_MAX_BYTES` 同源护栏，不截断，提示走面板）
- [x] 4.3 确定性 wrap + 落盘入队：wrap frontmatter（project / source: mm-channel / 发起人 user_id+username / 频道 / evidence——**零时间戳零随机成分**）；文件名 `mm-<sha256(wrapped)[:8]>.md` 确定性；`deliver_key = mm:{project}:{user_id}:{sha[:16]}:{yyyymmddhh}` 撞键回既有项幂等回执；入队 `enqueued_by = user_id`（26-hex 直发路由）
- [x] 4.4 双回执：HTTP 响应 JSON（response_type: ephemeral——成功 #id / 各拒绝态原因+可操作信息）；成功后 bot post 非 ephemeral 进频道（@发起人 + #id + 首行摘要）；拒绝不进频道；频道 post fail-open
- [x] 4.5 `hub/test_api.py`：入站全路径测试（token 两态 / 未绑定 / 非成员 / UNKNOWN fail-closed / 缺出处 / 超长 / 成功入队 enqueued_by 直发形态 / 同键重试幂等回执 #id / 跨小时窗新入队 / 双回执与拒绝不广播）——FakeMMClient + tmp data_root，全离线

## 5. bind/unbind 管理命令

- [x] 5.1 `hub/api.py`：`/pa bind <project>` / `/pa unbind` 经 `/mm/intake` 同端点分流（text 首 token 判别 run/bind/unbind，未知子命令 ephemeral 用法清单）：HUB_PANEL_ADMINS 白名单判据（username；未配白名单 = 全拒——防误开放；**管理面判据不串联频道成员校验**）；project 必读面校验（`_panel_projects()` 同源）；1:1 双向约束 + 双向换绑覆盖回执明示旧绑定（channel_id 落库前 26 字符形态机械判据——值虽来自 MM POST，判据为防回归）；bind 前 bot 可达频道校验（`channel_member_ids` 含 bot 自身 me_id 或等价探活，403/不可达回执「先拉 bot 进频道」）
- [x] 5.2 `hub/test_api.py`：bind 命令测试（非 admin 拒 / project 不在必读面拒 / 成功绑定 / 换绑覆盖明示两方向（project 换频道 + 频道侧顶绑）/ bot 不在频道拒 / unbind 后入站拒绝出站回落 DM / unbind 未绑定频道教育回执）

## 6. 出站通知路由升级（归属维度）

- [x] 6.1 `hub/notify.py`：Notifier 消费绑定表（替代 `cfg.project_channels` env 读点）——按 run 的 project 查绑定路由：绑定→频道（含 `<@发起人>` 提及——26-hex 直拼，非 26-hex 经 username 解析，失败降级纯文本）；未绑定→既有 DM 三代分流；频道 post 失败 fail-open **不回退 DM**
- [x] 6.2 run 终态面：绑定频道项目终态进频道不私信（挂点单次执行处不变）；语义事件转发面（events.py 消费点）同路由升级（绑定频道四类全量进——含 acceptance_suite；**per-event 广播判定 `_event_broadcast` 废止**，绑定=全事件进频道，文本拼接 `_event_text` 契约零变化——本体论审核 F1 裁决）；无 run 放弃告警按归属路由（绑定→频道；未绑定→ notify_admin DM 不变）
- [x] 6.3 `hub/test_notify.py` + `hub/test_events.py`：路由升级全覆盖（绑定终态进频道+提及+不私信 / 未绑定 DM 零变化 / 提及解析失败降级 / 频道失败不回退 / 无 run 放弃两路由 / env project_channels 读点已死——配置 env 无效行为锁定）

## 7. 真机联调与文档

- [x] 7.1 `hub/cli.py` check-notify 扩步入或新子命令 `check-intake`：slash token 配置态 / 绑定表内容 / bot 可达各绑定频道（成员查询连通）一键自检；RUNBOOK §16/新节补：MM 管理台建 slash command 步骤、bind 操作流程、入站回执语义、PAT 单段形态事实引用（§16 已记）
- [ ] 7.2 真机联调：MM 管理台建 `/pa` slash command → env 配 token 重建 → bot 拉进项目频道 → admin bind → 成员发 `/pa run <需求+出处>` 全链坐实（ephemeral 回执 / 频道时间线 / run 终态回频道含 @）；未配项回落形态复核（出站迁移前后行为对齐）
  - 2026-10-28 真机进度：token 两态（401/503）/ 未知子命令用法 / 未绑定教育回执 / 非成员真 MM 成员查询拒绝 / 成员缺出处 evidence 拒 / bind 非管理员白名单拒 / admin bind-unbind-绑回 / bot 频道 post 201 全部真机绿（cc-web-control ↔ txnt3p8iejb1p8ix6qw1wwe87c 经 seed 迁移落表后 bind 复验）；剩：MM 管理台正式注册 slash command 换真 token + 首个真 `/pa run` 全链（含 run 终态回频道 @）
- [x] 7.3 `bash hub/quality.sh` 全绿 + 全量测试回归（既有 hub-notifications 语义被 MODIFIED 的四 requirement 场景全部翻绿新口径）
