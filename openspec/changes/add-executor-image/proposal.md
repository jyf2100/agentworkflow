## Why

阶段 2（issue #3 / `docs/superpowers/specs/2026-10-20-pa-server-deployment-design.md`）的执行形态是「每 run 一个即弃容器」，其镜像内容源 = 阶段 1 的 template 投影——设计已批准、落地裁决 F8-F9 已补（hub 位置、本服务器 compose、凭据 env 注入验证通过），三个前置全部清空。镜像产物是整个阶段 2 依赖链的最底层（hub 起容器之前必须先有镜像），且与 hub 开发完全解耦、零争议。

## What Changes

- release 链新增 **Dockerfile 产物**：`release_template.sh` 的投影内容新增 Dockerfile，随 template tag 一起发布——template tag 即镜像 build context（两阶段咬合点，零重复劳动）
- 新增 **执行镜像 build 脚手**：从 template tag（checkout/下载）build 出 `pa-executor` 镜像的脚本 + 镜像内容自检
- 镜像内容：Python ≥3.11 + pa（scripts/bootstrap，来自投影）+ node/claude CLI + gh CLI + 8 persona（`.claude/agents/` 投影既有）
- **凭据零进镜像**（thin 镜像基调：运行时经 `--env-file`/secrets 注入 per-user 三值，F9 结论）
- 镜像内预置 claude CLI 首跑状态（onboarding/config）——F9 登记的残留项，本切片验收
- template 仓的用户增值：本机模式用户 clone template 后可自行 `docker build`（容器化可选，不改变本机直跑行为）

### 非目标（本切片不做）

- pa-hub 本体（队列/OAuth/保险箱）——后续切片
- docker-compose 编排、数据卷挂载约定——随 hub 切片
- 镜像分发渠道（ghcr.io push）——方向已定，机制随部署切片细化；本切片产出本地 build 能力

## Capabilities

### New Capabilities

- `executor-image`: 执行镜像的构建契约——从 template tag 生成 `pa-executor` 镜像（内容全集、凭据零入镜像、隔离基线、build 脚手与自检）

### Modified Capabilities

- `template-release`: 模板内容契约新增 Dockerfile（manifest 纳管）；发布门禁增加镜像内容维度——投影必须包含可在 build context 内生效的 Dockerfile
