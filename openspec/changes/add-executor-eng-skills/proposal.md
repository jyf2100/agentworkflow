# Proposal: add-executor-eng-skills

## Why

executor 容器内的 dev loop（dev-agent.py SDK 会话）目前**零 skills 裸奔**——`/home/pa/.claude/` 只有 9 个 pa-* persona agents，skills 目录不存在。主机会话长期验证有效的工程技能包（mattpocock-skills 插件 engineering 面：tdd / code-review / diagnosing-bugs / codebase-design / domain-modeling 等 20 个）对 dev 干活质量有直接增益（本 vault 的 change 流程本身就在用同源的 domain-modeling / to-spec）。

用户 2026-10-27 点名：走 openspec change，接 mattpocock-skills。

## What Changes

1. **技能快照进仓库**：mattpocock-skills 1.3.1 的 `skills/engineering/`（20 个 skill，520K）快照到 `hub/deploy/executor-skills/`，附 provenance（版本 / 来源 / MIT 许可）——镜像内容随 git 钉定，可复现可审计。
2. **镜像内置**：`pa-executor.Dockerfile` 加 COPY 层 → `/home/pa/.claude/skills/<name>/`（拍平分类层）。
3. **tag 升级**：镜像 build 为 `v0.1.8`（当前 v0.1.7，走 patch 增量通道），`hub.env` 与 `hub.env.example`（模板真理源）同步升 `HUB_IMAGE_TAG_DEFAULT`。
4. **spec**：execution-hub 加 ADDED requirement「executor 镜像工程技能面」。

## Non-Goals

- **persona 工具面不动**：pa-* agents 的 tools 收窄（critic 仅 Read 等）原样；skills 对 persona 只是上下文列表可见、调不动（design D3 已知代价）。
- **不进插件全量**：node_modules（24M）/ in-progress / misc / deprecated / productivity 一律不进（51M → 520K）。
- **不做运行时挂载**：不 `-v ~/.claude/skills`（破坏镜像自包含、仅本机有效）。
- **不做项目级隔离**：skills 挂用户级（全容器会话共享）；目标仓 worktree 级隔离列 follow-up。
- **不改 skills 内容**：快照原样，不汉化不裁剪。
