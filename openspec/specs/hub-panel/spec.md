# hub-panel

## Purpose

hub 面板能力：浏览器提交入口（会话强制 + evidence 机械必填，F12）+ 只读面（队列 / run 列表 / run 详情 / 报告，零语义渲染）+ 服务端渲染形态（零前端构建链，hub 最小依赖面不动）。

## Requirements

### Requirement: 面板提交入口（会话强制 + evidence 机械必填 + 包装态落盘）
hub 面板提交入口在原有守卫与 evidence 必填之上，落盘契约 SHALL 修订为**包装态**：
面板通道落盘的 PRD 一律带 hub 机械生成的 YAML frontmatter（`project`（白名单值）、
`source_path: ''`（手写注入无源是 pa 预期态，不伪造路径——source_path 在 pa 侧被
critic/dispatch 当路径消费）、`evidence`（面板依据落盘可审计，单行化 + 转义，上限
2048 字节）），sha256 与入队均以包装后全文为对象。原因（2026-10-23 首跑 #4 实证）：
裸 markdown 会被容器内 pa `stage_inject` 的 `frontmatter.project` 校验确定性拒绝
（exit 1），且 evidence「收了又扔」不可审计。deliver 机机通道（pa-prd 产物自带
frontmatter）与 REST 直入队通道不包装。

#### Scenario: 会话提交闭环
- **WHEN** 已登录同事在提交页填 PRD 内容、选 project、填 evidence 链接并提交
- **THEN** PRD 以包装态落盘 `data_root/<project>/prd/`（frontmatter 含
  project / source_path 空串 / evidence 转义值 + 原文正文）、sha256 按包装后全文、
  队列项入队（enqueued_by = 该会话 mm_user_id），页面显示队列项标识
- **AND** 该文件被派发重试时原样复用（retry 幂等——包装发生在落盘点一次成型）

#### Scenario: evidence 空被拒
- **WHEN** 提交时 evidence 为空或纯空白
- **THEN** 422 拒绝入队，提示「evidence 必填（链接或出处说明）」；不产生队列项、不落盘

#### Scenario: evidence 超限被拒
- **WHEN** evidence 压缩单行后超 2048 字节
- **THEN** 422 拒绝，提示上限；不产生队列项、不落盘

#### Scenario: 未登录不可提交
- **WHEN** 无有效会话 cookie 访问面板提交端点
- **THEN** 401/302 到登录；auth 未配置态面板端点 503（对齐 auth 群行为）

#### Scenario: project 清单来自必读面扫描
- **WHEN** 渲染提交页 project 下拉
- **THEN** 清单 = data_root 下有启动必读面（profiles/ + sources.yaml）的子目录扫描，
  与派发 fail fast 同判据；面板建档成功后当次渲染即可见
### Requirement: 面板只读面（队列 / run / 报告，零语义渲染）
hub SHALL 提供面板只读页：队列状态列表、run 列表（含终态与 exit code）、run 详情（终态事实 + journal 尾部行机械展示）。run 报告 SHALL 在报告落卷存在时只读渲染（T2a 裁决：pa 侧 report 双写 state 卷，见 spec: pa-event-outlet；roc 侧报告可见性 = 面板渲染 + bot 摘要）。run 详情 SHALL 在 events.jsonl（伴生事件文件）存在时机械展示其尾部若干行（原样文本——acceptance_suite / verify invalid 等语义事件从此面板可见，展示非判定）；SHALL 在 verify-report.json 落卷（pa 侧 verify-report 双写，见 spec: pa-event-outlet）存在时机械表格渲染其关键节：判定（verdict + invalid 时 reason）、命令清单（cmd / source / exit code 逐行）、锚检查（名称 + 通过/违反 rc）、环境奇偶节原样字段——缺失时显示「无 verify-report」占位（非错误，旧 run / flag off 形态无此文件）。面板渲染 MUST 限于机械展示 hub DB 自有行与 state 卷落盘文件——不解析 pa journal 内部结构做语义判定（journal/events 尾部行原样展示除外——展示非判定）、不聚合统计语义结论。只读面同样强制会话（与提交页同守卫口径）。队列状态列表 SHALL 最新优先（id 降序）并展示每行 `created_at`（入队时间）与 `updated_at`（最后状态变化时间）两列（原样展示，不做语义格式化）。

#### Scenario: run 列表与详情
- **WHEN** 已登录同事打开 run 列表 / 某 run 详情
- **THEN** 见队列项状态、run 终态、exit code、last_error、journal 尾部若干行（原样文本）

#### Scenario: 报告渲染
- **WHEN** 某 run 的报告文件已落卷（report 双写生效）
- **THEN** run 详情页渲染报告内容（markdown 原文展示）；报告缺失时显示「无报告」（非错误）

#### Scenario: verify-report 机械渲染
- **WHEN** 某 run 的 verify-report.json 已落卷（verify-report 双写生效）
- **THEN** run 详情页渲染判定（含 invalid reason）、逐命令 cmd/source/exit 表格、锚检查结果、环境奇偶字段（原样展示）；文件缺失时显示「无 verify-report」占位

#### Scenario: 语义事件尾部行可见
- **WHEN** 某 run 的 events.jsonl 含 `acceptance_suite`（absent——工件状态二值无 defective，缺陷可见性经 invalid reason + supersedes 链承载）或 `verify_verdict`（invalid + reason）事件行
- **THEN** run 详情页 events 尾部行区原样展示该行文本（无 hub 侧解读）

#### Scenario: 只读面不产生语义判定
- **WHEN** 任何只读页渲染
- **THEN** 页面内容为 DB 行与卷文件的事实展示，无 hub 侧生成的质量/价值/结论性判断

#### Scenario: 队列最新优先且含时间列
- **WHEN** 已登录同事打开队列页
- **THEN** 行序为 id 降序（最新提交/重跑的队列项在最上），每行含入队时间与最后状态变化时间两列（「MM-DD HH:MM」机械截短显示，title 悬停见 ISO 全文；操作列（重跑/放弃）位于 status 列旁，不因时间列加入被推出首屏可视区）
### Requirement: 面板形态（服务端渲染，零前端构建链）
面板 SHALL 服务端渲染纯 HTML（单 HTMLResponse 渲染函数族），MUST NOT 引入前端构建链、JS 框架或额外模板引擎依赖（hub 最小依赖面不动；样式内联极简）。内联原生脚本 SHALL 限于表单控件显隐联动（如 forge 选择联动条件字段），MUST NOT 引入外部脚本资源。面板端点群挂在既有 FastAPI app（与 REST 面同进程同端口）。CLI 与 REST 端点行为 MUST NOT 因面板引入而变化。

**UX 底线**：面板表单（提交 PRD / 建项目 / 凭据）SHALL 在 422 拒绝回显时保留用户已提交的字段值（服务端回填，经既有唯一转义入口；凭据页 token/password 框除外——密码语义不回填）。表单控件 SHALL 使用 `<label>` 关联与 hint 说明（说明文字不挤占标题）。状态展示 SHALL 不只依赖颜色（符号 + 色双通道）。交互控件（输入框/button）SHALL 有 focus 可见态。长内容（journal、错误文本）SHALL 有展示上限与滚动，不撑爆页面。

#### Scenario: 零新依赖
- **WHEN** 面板落地后检查 hub 依赖面
- **THEN** 无新增 Python 包依赖（渲染用标准库 + 既有 FastAPI 栈）；无外部脚本/样式资源

#### Scenario: 422 拒绝不丢输入
- **WHEN** 用户在建项目表单填完全部字段但某字段非法（如 gitlab_host 缺 scheme）提交被 422 拒绝
- **THEN** 回显页各输入框保留用户已填值（name/repo/goal/surface 等），仅修正出错字段即可重交

#### Scenario: 状态非纯色可辨
- **WHEN** 队列/run 列表渲染状态列
- **THEN** 状态含符号通道（如 ✓ done / ✗ failed / … pending）与颜色并行，色觉障碍下仍可辨

#### Scenario: 长内容有界
- **WHEN** run 详情 journal 尾部很长或队列 last_error 很长
- **THEN** journal 区滚动展示（高度上限）、错误文本截断展示（悬停可见全文），页面布局不被撑爆
### Requirement: 建项目表单（forge 三形态，github 缺省零变化）
面板 SHALL 提供建项目表单（`GET /panel/projects/new` + `POST /panel/projects`，admin 守卫）：除既有 name/repo/goal/match_surface 四字段外，SHALL 含 forge 选择（github 缺省 / gitlab / gitcode）与 `gitlab_host` 输入（仅 gitlab 形态必填——gitcode 固定 SaaS 无 host 键）。建档 SHALL 经 `projects.create_project` 机械落盘：gitlab 形态 profile 补 `forge: gitlab` + `gitlab_host`（带 scheme 可带端口）两键、repo 为 forge 全路径字符串（组/子组/仓 多级，不走 GitHub URL 归一）；gitcode 形态补 `forge: gitcode`、repo 两级 owner/repo；github 缺省形态落盘键集与行为 SHALL 与既有逐字节一致（回归零变化）。非法 forge 值 / gitlab 缺 host / repo 形态与 forge 不匹配 SHALL 422 回显表单拒绝建档（fail-fast，不落半写目录）。

#### Scenario: gitlab 用户经界面自助建档
- **WHEN** 已登录 admin 在建项目表单选 forge=gitlab、填 `gitlab_host`（如 `http://172.32.153.183:8180`）与 repo 全路径（`组/子组/仓`）提交
- **THEN** 落盘 profile 含 `forge: gitlab` + `gitlab_host` + `repo` 全路径三键，提交页 project 下拉可见该项目；派发面按 gitlab 项目动态校验 `GITLAB_TOKEN`（execution-hub 凭据 requirement 既有语义）

#### Scenario: gitlab 缺 host 被 422 拒
- **WHEN** 选 forge=gitlab 而未填 `gitlab_host` 提交
- **THEN** 422 回显表单与错误信息（fail-fast），不产生项目目录

#### Scenario: github 缺省形态回归零变化
- **WHEN** 不动 forge 选择（github 缺省）按既有四字段提交
- **THEN** 落盘 profile 键集与归一行为（owner/repo 或 GitHub URL → `https://github.com/<owner>/<repo>`）与既有逐字节一致

#### Scenario: gitcode 两级路径
- **WHEN** 选 forge=gitcode、填两级 owner/repo（无 host 字段）提交
- **THEN** 落盘 profile 含 `forge: gitcode`（无 host 键）+ repo 两级全路径

### Requirement: 面板项目建档
hub 面板 SHALL 提供项目建档入口：已登录的 admin 会话经表单提交项目档案
（name / repo / goal / match_surface one_liner），hub 以机械落盘在
`HUB_DATA_ROOT/<name>/` 下创建启动必读面（`profiles/<name>.yaml` + `sources.yaml`），
建档后项目立即进入提交页 project 清单。项目档案定性为运行时配置（真相源 =
`HUB_DATA_ROOT`），面板写它不构成控制面越界。

#### Scenario: 建档 happy path 落两文件且立即可提交
- **WHEN** admin 会话 POST `/panel/projects`（name=demo、repo=owner/repo、goal 与
  surface 非空单行）
- **THEN** `<data_root>/demo/profiles/demo.yaml`（含 name/admission: true/repo 规范化
  为 `https://github.com/owner/repo`/goal/match_surface.one_liner）与
  `<data_root>/demo/sources.yaml`（`sources: []`）落盘
- **AND** 提交页 project 下拉出现 demo（必读面扫描当次命中）

#### Scenario: 非法输入 422 回显不落盘
- **WHEN** 提交 name 含非法字符（不匹配 PROJECT_RE）、或 name 与既有项目重名、
  或 repo/goal/surface 空、或任一字段含换行
- **THEN** 422 + 表单页错误回显，data_root 无新目录（半写目录已清理）

#### Scenario: 非 admin 会话 403
- **WHEN** `HUB_PANEL_ADMINS` 已配置且会话 username 不在名单
- **THEN** 403 明确报错（admins 未配置时空缺省=任何有效会话放行）

### Requirement: 面板 run 操作（重跑 / 放弃）
hub 面板 SHALL 在队列页提供人工操作：对 `failed` 队列项**重跑**（以原 PRD 与原 project
新建队列项，发起人=当前操作者；旧行留审计），对 `pending` 队列项**放弃**（转终态
failed + last_error 记因，行保留审计——与 REST `DELETE /queue/{id}` 同一语义同一实现）。
两操作均须 admin 会话。

#### Scenario: 重跑 failed 项
- **WHEN** admin 会话 POST `/panel/queue/{id}/retry` 且该项 status=failed、PRD 文件仍在
- **THEN** 新队列项入队（同 project/prd_path，enqueued_by=操作者 mm_user_id）
- **AND** 旧行状态与审计字段原样保留

#### Scenario: 重跑非 failed 项 409
- **WHEN** 目标项 status 为 pending/running/done
- **THEN** 409 明确报错（防双跑/防重复投递），不入队

#### Scenario: 放弃 pending 项与 REST 同源
- **WHEN** admin 会话 POST `/panel/queue/{id}/abandon` 且该项 status=pending
- **THEN** 转终态 failed、last_error 记人工放弃因；行为与 `DELETE /queue/{id}` 完全一致
  （共享实现）；running/done/failed 项 409 文案同源
