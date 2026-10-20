# template-release

## Purpose

Define the versioned read-only projection from the maintainer vault to the `pa-pipeline-template` repository: all template content SHALL flow exclusively through `release_template.sh` (extract + transform + self-check + version tag), gated by a release checklist with quality evidence, with mechanical doc-layer conversion, a manifest-fixed content contract, and release-note obligations to disclose upstream state (the vault commit the snapshot is anchored to).

## Requirements
### Requirement: 版本化只读投影发布
pa-pipeline-template SHALL 只经 vault 侧 `release_template.sh` 生成与更新（抽取 + 转换 + 自检 + 打 tag v0.x），MUST NOT 存在任何绕过 release 的内容来源。vault 是唯一开发真源；template 的本体身份是版本化只读投影，不是平行真源。

#### Scenario: release 成功产出投影
- **WHEN** release 脚本在 vault 完成抽取、转换与自检且全部门禁通过
- **THEN** template 工作副本内容与 vault 转换输出一致，并以 tag（v0.x）标记本次发布

#### Scenario: template 内容偏离转换输出
- **WHEN** release 自检发现 template 工作副本中规约层文件与 vault 源经转换后存在 diff
- **THEN** 发布失败并输出差异摘要，不产出新 tag

### Requirement: 发布门禁
release SHALL 在 template 副本内执行 `quality.sh`（compileall + pytest + ruff），并执行去个人化 grep 门禁（个人路径、个人 SMTP 域、Keychain 等模式）；任一失败 MUST 阻断发布。

#### Scenario: 全绿通过
- **WHEN** template 副本内 quality.sh 退出码为 0 且去个人化 grep 零命中
- **THEN** 发布流程继续到打 tag 步骤

#### Scenario: 命中个人痕迹
- **WHEN** grep 门禁在 template 副本中命中个人路径（如 `/Users/<user>` 形态的用户家目录）或个人 SMTP 配置等模式
- **THEN** 发布失败并列出命中文件与行

### Requirement: 文档分层与转换机械化
规约层（`openspec/specs/`、`docs/adr/`、CONTEXT.md 术语语义）SHALL 保持单版本随行、永不 fork；叙事层（CLAUDE.md）允许 vault 开发视角与 template 使用视角双版本，但 MUST 只引用规约层、不得自创行为声明。去个人化转换 MUST 由机械脚本完成（可 diff、可审计），转换逻辑纳入 release 自检。

#### Scenario: 规约层单版本校验
- **WHEN** release 比对 template 副本规约层文件与 vault 源文件
- **THEN** 二者逐字节一致；任何差异导致发布失败

#### Scenario: 术语范畴迁移
- **WHEN** 转换脚本处理 CONTEXT.md「大脑」词条
- **THEN** 输出为「控制面仓（每实例一个）」多实例范畴，且 vault 源在发布前置文档修复后与该语义一致

### Requirement: 模板内容契约
template SHALL 且仅 SHALL 包含：`scripts/` 全量、`.claude/agents/` persona、`openspec/specs/`、`docs/adr/`、转换后 CONTEXT.md、新写使用视角 CLAUDE.md（首节回流约定 + 任务通道指导）、修订 RUNBOOK、generic 演示 profile、`webapp/`、`bootstrap.sh`、`scripts/admit_repo.py`。SPEC.md、`docs/reviews/`、个人报告与评审历史、`Knowledge/`、`.project-auto/` MUST NOT 进入 template。

#### Scenario: 内容清单自检
- **WHEN** release 校验 template 副本的路径清单
- **THEN** 仅上述路径存在；出现 SPEC.md 或 docs/reviews/ 即发布失败

#### Scenario: 通道指导必备
- **WHEN** release 校验 template 的使用视角 CLAUDE.md
- **THEN** 其包含任务通道指导节（「需要新的绿色测试证据才值得发 PR 的走流水线；不需要测试的直接用 claude CLI」+ 上下文准入两问），缺失即发布失败

### Requirement: release note 上游状态义务
每个 template tag SHALL 附 release note，注明所截取的上游状态（含 in-progress change 的中间态，如 LangGraph 双轨 flag-off），防止同事对照 vault 文档产生困惑。

#### Scenario: 注明中间态
- **WHEN** 发布时 vault 存在未完成的 openspec change（其产物已随 template 发布）
- **THEN** release note 列出该状态说明；缺失则发布门禁提示补全

