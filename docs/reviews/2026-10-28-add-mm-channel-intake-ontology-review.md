# add-mm-channel-intake — 本体论审核（规划态，tasks 0/19）

日期：2026-10-28｜对象：四工件声明本体 vs 代码现实｜方式：声明提取 → 代码逐点验证。

## 声明本体摘要

- **实体**：绑定（project_channels db 实体，1:1 双向，env seed 后废止）；凭据三实体
  （OAuth 登录 / bot token 出站 / slash token 入站）；入站需求（确定性 wrap → deliver_key
  幂等键）；频道成员授权（三态 FOUND/UNKNOWN/404）；事件归属路由（频道优先 / DM 兜底）。
- **不变式**：未配置零行为；UNKNOWN ≠ 授权结论（403=判据不可用）；确定性 wrap（零随机
  成分）是幂等前提；双回执后置；拒绝不广播；频道 post 失败 fail-open 不回退 DM；通知零持久写面。

## 代码现实验证矩阵（工件引用质量：高——14 项先例/就绪声明全部属实）

| 声明 | 代码落点 | 判定 |
|---|---|---|
| deliver_key db 幂等先例 | api.py:695（撞键回既有项 duplicate） | ✓ |
| `_DELIVER_MAX_BYTES` 护栏 | api.py:60（1MB） | ✓ |
| mm_user_id 26-hex 判据先例 | notify.py:24 `[0-9a-f]{26}` | ✓ |
| `users/username/{name}` 解析先例 | notify.py:82 | ✓ |
| bot `me_id` 契约（bind 探活前提） | notify.py:47/73 | ✓ |
| `HUB_PANEL_ADMINS` 白名单 | api.py:195-202 | ✓（但见 F2） |
| `_panel_projects()` 必读面 | api.py:321 | ✓ |
| per-project 串行位 | db.py:201 `oldest_pending_excluding_running_projects` | ✓ |
| per-user vault 凭据 → env 兜底 | lifecycle.py:146（vault 优先→env warn→双缺报错） | ✓ |
| 容器峰值上限 | config.py:87 max_containers | ✓ |
| enqueued_by 署名审计 | api.py:104-116 | ✓ |
| db ts ISO8601 UTC 先例 | db.py:55 | ✓ |
| 面板 wrap 纯机械拼接（mm 确定性差异点声明准确） | api.py:66-76/390 | ✓ |
| RENAME 基线（「per-project 频道广播（可选）」现状=失败广播+成功不进） | 主 spec L50-60 | ✓ |

## Findings

### F1 [高] hub-notifications「语义事件通知」delta 基线漂移——归档 sync 会冲掉 acceptance_suite 契约

add-mm delta 该段 **0 处** `acceptance_suite`；主 spec（今晨 fix-event-outlet-verify-parity
归档 sync 后）**3 处**。delta 基于旧主 spec 写，MODIFIED 整段替换 → 归档 sync 时
acceptance_suite 转发契约（进 KNOWN_EVENTS、invalid/absent 广播判读、stored 仅私信）从
主 spec 蒸发。两 change 并行动同一 requirement，后写者不知前者已改基线。

不止文本丢失——**语义口径交叉未调和**：add-mm「绑定频道项目语义事件全量进频道（pass 类
也进）」与 fix-event-outlet 的 per-event 广播判读（invalid/absent 广播、stored/critic
pass/pr_opened 仅私信——噪音控制口径）交叉处未定义：绑定频道的项目收到
`acceptance_suite stored` / `critic pass` 时进频道还是仅私信？

本体诊断：两 change 的本体维度本应正交——fix-event-outlet 是「**事件类型 × 噪音判读**」
（broadcast 判定），add-mm 是「**事件归属 × 通道路由**」（DM→频道升维）。可调和形态：
频道绑定不改变 per-event broadcast 判定，只把「私信」替换为「频道 + @发起人」（私信号
码不变、通道升维）；「全事件进频道」限定为 broadcast 判定为 true 的事件类 + run 终态面。
但 add-mm delta L63 现文是「pass 类皆进频道——取代原『失败类才进频道』的噪音控制口径」
——把噪音判读维度也覆盖了，与 fix-event-outlet 判读直接冲突。需在实施前显式合并裁决并
改写该 delta 段（并入 acceptance_suite 转发契约）。

### F2 [中] HUB_PANEL_ADMINS 空白名单语义矛盾（同判据两面语义相反且未声明）

既有实现：**空 = 任何有效会话放行**（api.py:195-199 `_require_admin` docstring 明说；
config.py:37 同——hub-panel-console D4 定案「单用户回退态」）。add-mm delta L108：bind
「未配白名单 = **全拒**，防误开放」。而 proposal L11 与 spec 都声称「与面板 retry/abandon
同一判据——一个判据管所有管理面操作」——「一个判据」但空白名单语义相反：部署者配空
白名单得到「面板 retry 全开 + bind 全拒」的分裂行为。

分歧本身有正当理由（面板有 OAuth 会话这层弱授权兜底所以空可放行；slash 通道零会话概念，
空白名单时无任何附加判据，fail-closed 全拒正确）——但工件把它**掩盖**在「一个判据」措辞
下。修法：spec/design 显式声明差异及理由（「同键不同空态语义：面板=会话回退态放行，
slash=无会话判据 fail-closed 全拒」），而非让实施者从矛盾文本里猜。

### F3 [低] PRD 文件名形态偏离既有 date 前缀约定，偏离理由未声明

既有两入口文件名均带日期前缀（面板 `{yyyymmdd}_hand-{uuid8}.md`）；mm 入站
`mm-<sha[:8]>.md` 零日期。已验证无机械消费冲突（`_run_events_tail` 的 stamp 取自 run
目录名非 PRD 文件名；store_prd 不假设前缀）——确定性（幂等前提）理由成立，但「为什么
偏离」宜在 spec 一句话声明，防后人「顺手统一」加回 uuid/日期破坏确定性。

### F4 [低] 「与 /panel/prd 同判」措辞歧义

面板 evidence 闸是**表单字段**判据（evidence: str 空 422，api.py:352）；mm 入站是
**正文文本标记**机械解析（`出处:` 全/半角）。语义同（必填）实现异——spec「同判『空依据
闸前拒』」易误读为实现复用。措辞宜改「同语义判据，机械形态各自适配（表单字段 vs 文本标记）」。

## 结论

规划质量高（14 项先例引用零失实，三态授权/幂等/双回执/1:1 换绑的本体设计自洽）。
**F1 必须在实施前修**（否则归档 sync 冲掉 acceptance_suite 契约 + 语义口径冲突带入实现）；
F2 同期修（一句话显式声明）；F3/F4 措辞级。执行面零改动声明与代码现实相符。
