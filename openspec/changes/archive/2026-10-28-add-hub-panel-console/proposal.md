# Proposal: add-hub-panel-console

## Why

服务器集中式部署（hub 切片 2a-4b）落地后，控制面的「手」没有跟着搬过来：vault 形态下
项目档案由人在 Obsidian 里手改（vault 即操作台），服务器形态下没有 Obsidian、hub 面板只读
项目清单——首次真机使用即撞「面板上没有建项目」。同时失败 run 无人工操作面（重跑须 CLI、
放弃须 curl DELETE），第一发子弹的闭环体验断在运营操作上。

**边界定性（本 change 的 spec 立场）**：项目档案（profiles/sources.yaml）是**运行时配置**
而非控制面设计资产——之前住 vault 只因没有别的家。服务器形态下其真相源就是
`HUB_DATA_ROOT`（/data/pa），面板写它不构成「hub 越界控制面」。vault 侧 spec/design 仍是
设计真相源，两者不同层、无双真相源问题。

## What Changes

- **面板项目建档**：`GET /panel/projects/new` 表单（name / repo / goal / match_surface
  one_liner）→ `POST /panel/projects` 机械落盘两文件（`<data_root>/<name>/profiles/<name>.yaml`
  + `sources.yaml`），建档后立即出现在提交页 project 清单（必读面扫描天然命中）。
- **面板 run 操作**：队列页对 `failed` 项提供**重跑**（新队列项同 PRD，旧行留审计），
  对 `pending` 项提供**放弃**（复用清队列语义：转终态 failed + last_error 记因，行保留审计）。
- **admin 白名单**：`HUB_PANEL_ADMINS`（MM username 逗号分隔）。上述三操作统一挂 admin
  守卫；空/不配 = 任何有效会话可操作（单用户回退态）。提交 PRD 守卫不变（任何登录用户）。

## Out of Scope

- 项目编辑/删除（低频，手改服务器文件可过渡，撞上再收）
- 信号源管理（手写 PRD 流不消费 fetch 源）
- 角色系统/多级权限（一行 env 白名单够用）
- MM 对话式操作（4b 已裁决 YAGNI，维持）
- spec/design 工件的面板化（设计真相源仍在 vault）
