## ADDED Requirements

### Requirement: executor 镜像 helix 技能面

pa-executor 镜像 SHALL 在工程技能包之外内置 HelixHarness 插件 skills 面快照（当前快照 = github.com/didi/helixharness commit `44ffc53` 的 8 个 skill，容器安装 6 个），挂载于容器用户级技能目录 `/home/pa/.claude/skills/<name>/SKILL.md`，供容器内 dev loop 会话发现并按需调用。快照 MUST 以仓库 git 快照形态随 build context 进镜像（PROVENANCE 钉上游 commit hash 与来源），MUST NOT 依赖运行时插件安装（marketplace add）或网络取件——同一 tag 镜像在任何机器 build/拉取，技能内容恒与仓库快照一致。与既有工程技能包（executor-skills）同名的 skill MUST 让位 incumbent（容器保留既有版本，不静默覆盖）。技能面内置 MUST NOT 扩大 pa-* persona 的可调用工具面（persona 工具面仍由各自 agent 定义 `tools:` 收窄），MUST NOT 引入 HelixHarness 插件运行时（commands / hooks / scripts / agents）与遥测 consent 面。

#### Scenario: dev loop 会话可见 helix 技能

- **WHEN** executor 容器内的 dev loop 在目标仓 worktree 起 SDK 会话
- **THEN** helix 技能面（d-d2c / d2c-fidelity-evaluator / feedback / brainstorming / git-diff-impact / systematic-debugging）出现在会话可用技能列表（按各自 frontmatter 可见性规则，惰性项仅上下文可见），dev 可按需调用

#### Scenario: 同名让位不静默覆盖

- **WHEN** 镜像内置 helix 技能面，且快照中存在与 executor-skills 同名的 skill（当前为 tdd / code-review）
- **THEN** 容器用户级技能目录保留 incumbent（executor-skills）版本，helix 同名版不进容器；让位清单与理由 MUST 在 PROVENANCE 与 Dockerfile 拷贝层注记中可见。对排除表外的**新增**撞名（上游刷新引入），build 侧碰撞守卫 SHALL 比对 executor-skills 快照源目录并 fail loud（build 失败、列名），处置 = 扩排除表（让位决定）或上游更名后重跑——MUST NOT 静默覆盖 incumbent

#### Scenario: 快照不含插件 hook 面

- **WHEN** 上游 skill 的 SKILL.md frontmatter 内嵌插件宿主 hook 声明（如 d-d2c 的 PreToolUse AskUserQuestion 守卫与 Stop 守卫，引用 `${CLAUDE_PLUGIN_ROOT}`，悬空时按上游 fallback 确定性 deny/block）
- **THEN** 入库快照 MUST 中和该 hook 声明（其余内容与上游一致）、在被修改文件面置修改标注（Apache-2.0 §4.1）并在 PROVENANCE 记偏差（依据 + 刷新时重复中和义务）；快照包 MUST 随包 vendor 上游 LICENSE / NOTICE / THIRD_PARTY_NOTICES（再分发保留义务，含 superpowers/mattpocock 等 MIT 保留条款）；容器内任何会话不因 helix 技能快照注册 hook

#### Scenario: persona 工具面不因技能面扩大

- **WHEN** pa-* persona（如 pa-prd-critic）经 `claude --agent` headless 调用
- **THEN** 其可调用工具仍由 agent 定义收窄（critic 仅 Read），不因镜像内置 helix 技能面获得 Skill tool 调用能力

#### Scenario: 镜像内容随 tag 确定（git 钉定）

- **WHEN** 同一 tag 的 pa-executor 镜像在任意机器 build 或拉取
- **THEN** helix 技能内容与仓库快照（`hub/deploy/helix-skills/`，PROVENANCE 钉上游 commit）一致，运行时无 marketplace / 网络取件依赖

#### Scenario: 技能面升级走显式新 tag

- **WHEN** 刷新仓库 helix 技能快照（上游 commit 更新）
- **THEN** 镜像 MUST build 为新 tag 并同步 hub 默认 tag 配置后方可生效；既有队列项按入队时固化的 tag 继续用旧内容（不静默漂移）

#### Scenario: 遥测零面

- **WHEN** 容器内 dev loop 会话加载 helix 技能快照
- **THEN** 不引入遥测 consent 面：镜像不含插件运行时（无 consent 询问、无 eval-log 账本写入、无异步上报），不预配任何 `HELIXHARNESS_*` 遥测 env；consent 预授权与平台遥测整合属显式后续 change，不随技能面带进
