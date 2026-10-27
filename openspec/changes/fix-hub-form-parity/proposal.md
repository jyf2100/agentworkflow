# Proposal: fix-hub-form-parity

## Why

2026-10-27 hub 形态能力对齐审核（本体论方法：对照声明本体与实现）发现三处声明-实现偏差。全部属「hub 形态（服务器集中式跑 pa 后半段）」下的能力面，前两处已在真机验证中实锤：

1. **report 出口断链（真差距）**：`pa-event-outlet`（T2a 裁决：report 段经 `PA_REPORT_DIR` 双写 state 卷）与 `hub-panel`（报告落卷时只读渲染、缺失显「无报告」）两侧声明与实现均闭环，hub `lifecycle.py` 也已注入 `PA_REPORT_DIR`——但派发命令只有 `--from-stage inject`，`--to-stage` 落默认 `dispatch`，**report 段从不执行**：`state/reports/` 无产物（47 个历史 run 零报告），面板 run 详情恒「无报告」。三方声明就差触发侧一个参数。
2. **per-agent 模型路由主配置点在容器形态失效（声明半支持）**：`per-agent-model-routing` 声明「配置点（主）= config/model-routing.json」，但 executor 镜像不含 `config/` 目录（patch.Dockerfile 只 COPY scripts/ 等）——容器内主点恒缺失，仅 env 点（`PA_*_MODEL`，lifecycle `PA_*` 透传）可用。裁决（2026-10-27 用户）：**hub 形态 env-only 是有意选择**（路由属部署面配置，随 hub.env 差异化、即刻生效，烤进镜像反而钉死在发布时点）——spec 补限定使声明与实现一致。
3. **profile 样例滞后（教学缺口）**：`bootstrap/samples/profile.example.yaml` 仍是 github 本机 clone 形态（`repo: /abs/path/...`），无 forge 字段教学——gitlab/gitcode 用户（add-forge-adapter 已交付的能力面）没样例可抄，且两种 `repo` 语义（本地 clone 路径 vs forge 全路径字符串）易混。
4. **面板建项目表单只覆盖 github 形态（界面缺口，用户指出 2026-10-27）**：`/panel/projects/new` 表单仅 name/repo/goal/surface 四字段，repo 提示语钉死「owner/repo 或 GitHub URL」，`create_project` 落盘五键无 forge/gitlab_host——gitlab/gitcode 用户经界面**没处填** forge 三键，被迫 ssh 后端手写 yaml，面板的产品化意义（团队自助接入）落空。且建项目面在主 specs 零声明（顺带补声明）。

## What Changes

- **①report 出口接通**：hub 派发命令补 `--to-stage report`（一行）；容器内执行链从 `inject→dispatch` 变 `inject→report`。report 段纯机械（读 state JSON 落 md + 双写 `PA_REPORT_DIR`）+ SMTP fail-open（失败退化为告警不阻塞，容器内 SMTP env 不透传 → 恒一条告警日志，**容忍**——hub 形态通知面已是 MM bot（hub-notifications），不引 SMTP 透传，YAGNI）。
- **②路由配置点 spec 限定**：`per-agent-model-routing` 的「配置点（主）」requirement 加容器形态限定（文件主点不进 executor 镜像 = 有意的部署面选择；容器形态经 env 点配置）；RUNBOOK 补 hub.env 配置示例。零代码变更。
- **③profile 样例补 forge 教学**：`profile.example.yaml` 补 forge 三键（`forge` / `gitlab_host` / `repo` 全路径）注释段 + 两种 repo 语义对照说明。零行为变更（纯注释/样例）。
- **④面板建项目补 forge 形态**：建项目表单补 forge 选择（github 缺省/gitlab/gitcode）+ `gitlab_host` 条件字段 + repo 提示语双形态说明；`create_project` 落盘 forge/gitlab_host 键（gitlab 校验 host 必填 + repo 全路径形态；github 缺省行为零变化）。gitlab/gitcode 用户经界面自助建档，不再 ssh 后端。

### 非 Goal

- SMTP 容器内透传（通知面归属 hub-notifications，已裁决容忍 fail-open 告警）
- 镜像 COPY `config/`（方案 A 已否——路由变更不应要求重发镜像）
- mac/vault 本机形态任何行为变化（①只改 hub 派发命令；②③纯文档/样例）

## Capabilities

### Modified Capabilities

- `execution-hub`：新增 requirement——执行链段范围（派发命令 SHALL `--from-stage inject --to-stage report`，report 双写产物落 state 卷供面板渲染）
- `per-agent-model-routing`：「配置点（主）」requirement 加容器形态限定（文件主点 vault 本机形态专属；容器形态 env-only）
- `prd-delivery`：容器内执行链描述中「lifecycle 既有机制零改动」措辞同步（cmd 增补 `--to-stage report`）
- `template-bootstrap`：sample profile 覆盖 forge 形态字段教学
- `hub-panel`：新增 requirement——建项目表单（forge 三形态支持，github 缺省零变化；建项目面首入主 specs）

