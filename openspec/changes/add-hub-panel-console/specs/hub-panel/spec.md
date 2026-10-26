# hub-panel（delta：面板控制台操作）

## MODIFIED Requirements

### Requirement: 面板提交入口（会话强制 + evidence 机械必填）

hub 面板提交入口在原有守卫与 evidence 必填之上，落盘契约修订为**包装态**：
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

## ADDED Requirements

### Requirement: 面板项目建档

hub 面板应提供项目建档入口：已登录的 admin 会话经表单提交项目档案
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

hub 面板应在队列页提供人工操作：对 `failed` 队列项**重跑**（以原 PRD 与原 project
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
