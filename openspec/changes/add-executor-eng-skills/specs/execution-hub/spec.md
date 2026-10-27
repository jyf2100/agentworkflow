## ADDED Requirements

### Requirement: executor 镜像工程技能面

pa-executor 镜像 SHALL 内置工程技能包（当前快照 = mattpocock-skills 插件 engineering 面 20 个 skill），挂载于容器用户级 skills 目录 `/home/pa/.claude/skills/<name>/SKILL.md`，供容器内 dev loop 会话发现并按需调用。技能包 MUST 以仓库快照形态随 build context 进镜像（provenance 钉插件版本与来源），MUST NOT 依赖运行时挂载——同一 tag 镜像在任何机器 build/拉取，技能内容恒与仓库快照一致。技能包的内置 MUST NOT 扩大 pa-* persona 的可调用工具面：persona 工具面仍由各自 agent 定义的 `tools:` 收窄（技能列表仅作为上下文可见，不可经 Skill tool 调用）。

#### Scenario: dev loop 会话可见工程技能

- **WHEN** executor 容器内的 dev loop 在目标仓 worktree 起 SDK 会话
- **THEN** 工程技能包（tdd / code-review / diagnosing-bugs 等）出现在会话可用技能列表，dev 可按需调用

#### Scenario: persona 工具面不因技能包扩大

- **WHEN** pa-* persona（如 pa-prd-critic）经 `claude --agent` headless 调用
- **THEN** 其可调用工具仍由 agent 定义收窄（critic 仅 Read），不因镜像内置技能包获得 Skill tool 调用能力

#### Scenario: 镜像内容随 tag 确定

- **WHEN** 同一 tag 的 pa-executor 镜像在任意机器 build 或拉取
- **THEN** 技能包内容与仓库快照（`hub/deploy/executor-skills/`）一致，运行时无外部技能依赖

#### Scenario: 技能包升级走显式新 tag

- **WHEN** 刷新仓库技能快照（插件版本升级）
- **THEN** 镜像 MUST build 为新 tag 并同步 hub 默认 tag 配置后方可生效；既有队列项按入队时固化的 tag 继续用旧内容（不静默漂移）
