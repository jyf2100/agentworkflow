# Design: add-executor-eng-skills

## D1 通道：镜像内置快照（否决运行时挂载）

- **选**：skills 入库快照（`hub/deploy/executor-skills/`，与 Dockerfile/compose/example 同属部署资产区），Dockerfile COPY 进镜像。
- **否**：`docker run -v ~/.claude/skills:…` 运行时挂载。理由：① 镜像内容随 tag 钉定是 F13 语义的地基（同 tag = 同行为），挂载使「同一 tag 在不同机器/不同时刻行为漂移」；② 挂载路径只在装有该插件的机器有效，executor 到别机部署即断；③ COPY 层对镜像体积影响 520K，可忽略。
- **为什么不放 vault `.claude/skills/`**（与 agents 先例对称的位置）：vault `.claude/skills/` 已有 7 个本仓自有技能，是**主机所有 vault 会话**的技能注入源——放这里会把 20 个技能灌进主机 vault 会话（需求只给容器 dev loop）且与自有技能混淆。executor 专属资产归部署资产区更准。
- **build context 语义**（RUNBOOK §15 部署顺序第 2 步）：build 在投影 checkout（服务器 `pa-instance`）根上跑，`-f hub/deploy/pa-executor.Dockerfile` 只是文件引用；COPY 源路径须写投影根相对全路径 `hub/deploy/executor-skills/`。投影 `.dockerignore` 只排除 `.project-auto/`、`.venv/` 等，hub/ 在投影内（现状 `COPY . /opt/pa` 本就把 hub/ 打进镜像），技能目录可达。**实际 build 走 `pa-executor.patch.Dockerfile` 增量通道**（`FROM` 上一 tag + COPY 变更文件，不重跑 apt/npm/pip 网络层——v0.1.4/v0.1.5 同法先例）；全量 Dockerfile 同步加层作真理源。

## D2 范围：engineering 全目录拍平

mattpocock-skills 1.3.1 分类目录中取 `engineering/`（20 skill / 520K）：ask-matt、codebase-design、code-review、diagnosing-bugs、domain-modeling、grill-with-docs、implement、implement-spec、improve-codebase-architecture、pr、prototype、research、retro、setup-matt-pocock-skills、tdd、to-spec、to-tickets、triage、wayfinder、wizard。

- 排除：`in-progress`（未完成）、`misc`、`deprecated`、`productivity`（interview/问人型，headless dev 用不上）、`node_modules`（24M 插件运行依赖，容器内无插件运行时）。
- 不逐个精选、整目录进：升级插件时零清单维护，噪声可控（engineering 面本就全工程向）。
- **可见性成色（2026-10-27 实施审计实证）**：20 个中 9 个模型可自主调用（code-review / codebase-design / diagnosing-bugs / domain-modeling / pr / prototype / research / tdd / wizard），11 个 frontmatter `disable-model-invocation: true`（ask-matt / grill-with-docs / implement / implement-spec / improve-codebase-architecture / retro / setup-matt-pocock-skills / to-spec / to-tickets / triage / wayfinder）——headless dev loop 无人工 slash 通道，这 11 个实际惰性。保留不裁（改 frontmatter 违反「不改 skills 内容」非目标；裁清单违反零清单维护），按 9 个活跃记账。
- 形态：`skills/engineering/<name>/SKILL.md` → 拍平为 `~/.claude/skills/<name>/SKILL.md`（用户级 skills 扫描形态；保留分类层不识别）。`engineering/README.md` 非 skill 目录，不拷。

## D3 位置与 persona 共享代价

挂容器用户级 `/home/pa/.claude/skills/`（HOME=/home/pa，uid 1001，实测镜像内该目录已存在父级）。**已知代价**：pa-* persona 同容器同 HOME，headless 会话的 system prompt 会收到 skills 列表注入（20 个 × 描述行 ≈ 40 行上下文噪声）。判定可接受：

- persona 的可调用工具由 agent 定义 `tools:` 收窄（critic 仅 Read），Skill tool 不在面内 → 列表可见但**调不动**，对抗面能力无实际扩大；
- 输出契约（一行 JSON）不受 system 侧列表影响；
- 干净隔离要走目标仓项目级 skills（worktree `.claude/skills`）→ 需 dev-agent.py 改动注入，YAGNI，列 follow-up。

## D4 版本与升级

- 快照 provenance 落 `hub/deploy/executor-skills/PROVENANCE.md`：插件名 / 版本 1.3.1 / 来源 github.com/mattpocock/skills / MIT / 快照日期 / 拷贝命令。
- 升级 = 手动刷新快照（重跑拷贝命令）→ rebuild 新 tag → hub.env 同步。无自动同步（插件升级频率低，且镜像内容变化必须走新 tag 的显式路径）。

## D5 tag：v0.1.8

镜像 build `pa-executor:v0.1.8`（当前 v0.1.7）；`hub.env` **与 `hub.env.example` 同步**升 `HUB_IMAGE_TAG_DEFAULT`（example 入库是模板真理源——RUNBOOK §15：漂移会让照抄者静默回退 tag；hub.env 是 0600 部署机文件，改前知会用户）。旧队列项钉旧 tag 不受影响（F13：入队时固化）。

## 验证口径

- build 后冒烟：`docker run --entrypoint sh pa-executor:v0.1.8 -c 'ls /home/pa/.claude/skills | head'` 目录齐 + `claude -p` 单轮问答确认会话能报出 skills 名（headless 列表可见性）。
- 真机：下一次派发的容器内 dev 会话按需引用（软验证，观察不 gate）。
