# Proposal: add-helix-target-harness

## Why

executor 容器技能面目前单一来源：mattpocock-skills 工程包（add-executor-eng-skills，v0.1.8 起 20 个）。HelixHarness（didi 开源双宿主研发工作流插件，vault 外参考克隆 `/mnt/disk01/workspaces/worksummary/helixharness`）的 skills 面提供方法论互补项——brainstorming / systematic-debugging / git-diff-impact（独立方法工具）+ d-d2c / d2c-fidelity-evaluator（设计稿还原套件，1.3M 主体）——其中独立方法三件与 dev loop 的排障/影响分析场景直接对口。

用户 2026-10-31 点名：走 openspec change，同 eng-skills 模式把 helix skills 快照进 pa-executor 镜像。

## What Changes

1. **技能快照进仓库**：HelixHarness（github.com/didi/helixharness，Apache-2.0）main @ `44ffc53` 的 `helixharness/skills/` 8 个 skill 全量（1.3M）快照到 `hub/deploy/helix-skills/`，附 PROVENANCE（插件名 / commit hash 钉定 / 许可 / 快照日期 / 刷新流程）——镜像内容 git 钉定，不做运行时 marketplace add。
2. **镜像内置（6/8）**：两个 Dockerfile 拷至容器用户级 `/home/pa/.claude/skills/`（与 executor-skills 同目录合并）；`tdd` / `code-review` 与 executor-skills 同名——incumbent 让位原则排除（见 design D2）。
3. **tag 升级**：镜像 build `v0.1.35`（当前 v0.1.34）；`hub.env.example` 的 `HUB_IMAGE_TAG_DEFAULT` 升一档（`hub.env` 部署机同步属运行时 task）；`release/manifest.txt` 纳管新目录。
4. **spec**：execution-hub 加 ADDED requirement「executor 镜像 helix 技能面」（含同名让位与遥测零面两条 scenario 约束）。

## Non-Goals

- **agents 16 个不进镜像**：3 个 init-*-bootstrap + 12 个 scan-* + unit-test-worker 服务 `/helix:init` 的目标仓初始化，属 admission 工具范畴（follow-up），且避免与 pa-* persona 命名空间混淆。
- **不进插件主体**：commands 9 个 / hooks / scripts 运行时 / reference/ 协议文档一律不进——纯技能快照。
- **遥测零面**：不装插件运行时即无 consent 面（上游 consent 默认 disabled）；consent 预授权与平台遥测整合显式列后续 change。
- **不改 dev-agent.py prompt、不动 SDK tools 白名单**：技能发现走 CLI 用户级 skills 扫描，工具面零变化（persona 面同样不扩大）。
- **PRD↔docs/specs 互译与四目录约定**（helix propose 工作流的目标仓约定）属目标面自决，后续 change。
