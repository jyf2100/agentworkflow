## ADDED Requirements

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
