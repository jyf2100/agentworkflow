## Context

阶段 2 设计已批准（issue #3 / design doc F1-F9 裁决齐备）：执行形态 = 每 run 一个即弃容器，镜像内容源 = template 投影（tag 即 build context）。本 change 是阶段 2 依赖链最底层的切片 0——只做「Dockerfile 进投影 + build 脚手」，不做 hub、不做 compose。前置已验证：本服务器 docker 28.3.2 + compose v2.38.2（用户在 docker 组）；per-user 凭据 = 纯 env 三值（F9），镜像本体对凭据零依赖。

## Goals / Non-Goals

**Goals:**
- template 投影新增 Dockerfile + .dockerignore，随 tag 发布、受 release 静态自检管
- `pa-executor` 镜像一条命令从 template tag build 到自检全绿
- 镜像满足隔离基线（非 root、凭据零入镜像、首跑状态预置）
- 阶段 1 本机直跑行为零变化

**Non-Goals:**
- pa-hub（队列/OAuth/保险箱）、docker-compose 编排、数据卷约定——后续切片
- ghcr.io 推送（方向已定，机制随部署切片）
- 运行时限额/网络白名单的实施（镜像只保证「可被约束」，参数由 hub/compose 给）

## Decisions

- **D1 Dockerfile 源位置与投影**：vault 侧真源 = `Projects/项目推进流水线/release/Dockerfile`（与 manifest.txt 同级），经 manifest 纳管投影到 template **根目录**（build context 惯例：Dockerfile 在 context 根）。.dockerignore 同源同投影。
- **D2 基底镜像**：`node:22-slim`（bookworm）+ apt `python3.11/python3-pip`（bookworm 原生 3.11）+ npm 全局 `@anthropic-ai/claude-code` + gh CLI（github 官方 apt 源）。**关键组件版本钉扎**：claude CLI 版本经 Dockerfile `ARG` 锁定（先例：SDK 钉 `>=0.2.128,<0.2.130`；gh 锁 apt 源版本或下载钉版二进制）——否则同一 tag 两次 build 产物漂移，与「可复现」自相矛盾；实际组件版本记入镜像 label（可追溯）。单基底不搞多阶段——镜像简洁性优先，体积优化（thin 化）留 ghcr.io 切片。
- **D3 build 脚手位置**：`Projects/项目推进流水线/release/build_executor_image.sh`——维护者工具，**不进投影**（manifest 不纳管；template 用户拿到 Dockerfile 可自行 build，脚手是 vault 侧的自动化封装）。流程：checkout/复用 template tag → `docker build` → label（template tag + upstream commit（取自 RELEASE_NOTE 锚定）+ 组件实际版本清单）→ 容器内自检。术语约定：**build context = template tag checkout**（投影的物化形态；「镜像内容源」为同义口语，规范用 build context）。
- **D4 镜像内自检清单**（docker run 逐项，任一非 0 即失败）：`id -u`（非 root）、`python3 --version`、`claude --version`、`gh --version`、pa scripts `pytest --collect-only -q`（收集非空）、compileall；密钥模式扫描（`ghp_`/`github_pat_`/`sk-`/`AKIA` 等前缀 + Dockerfile 字面量检查）。不引 trivy 等重工具——模式 grep 足够（thin 工具链）。
- **D5 首跑状态预置**：Dockerfile 内以镜像用户执行 `claude --version` + 写入已知就绪 config（非交互 `claude config set` 或预置 settings JSON）；验收 = 全新容器最小 `claude -p --output-format json` 不因 onboarding/trust 挂起（spec scenario）。实测发现的额外状态项在实现时补齐并记录。
- **D6 非 root**：`useradd` 专用用户（`pa`），WORKDIR `/workspace`（目标仓 worktree 的挂载落点），entrypoint 收敛流水线入口。**WORKDIR 内容不随镜像带目标仓代码**（运行时 clone——F6：容器内 worktree 是目标面投影）。
- **D7 release 静态自检扩展**：现有 release 自检（python）加 Dockerfile 四项静态检查：存在、`FROM` 显式 tag（非 latest）、有 `USER` 非 root 声明、无凭据字面量模式。**release 不跑 docker build**（完整 build 验证属 build 脚手职责；release 在 mac 上跑，不假设 docker 可用）。
- **D8 测试接缝**：Dockerfile 静态自检 = 纯 python 函数 → pytest 直测（先例：release 自检既有测试）；build 脚手 `--dry-run` 模式打印计划序列 → pytest 断言参数与顺序；镜像真实验收 = 本服务器 E2E（v0.1.2 tag 起 build 全绿，记 verification）。

- **D9 镜像实体同一性（本体论审核裁决，2026-10-20）——pa-executor 正式化替代 pa-sandbox**：`container_sandbox.py` 现有默认 `image="pa-sandbox:latest"`（两处：181/282）是无 Dockerfile、无内容契约、无版本锚定的**隐式**沙箱镜像；`isolated-observable-execution` 的 `isolated-container` tier 正拿它当执行载体。本 change 的 `pa-executor` 即它的**正式化**（补内容契约 + tag/版本锚定），非并存的新实体——实现时迁移默认镜像名到 `pa-executor`（含相关测试引用），术语收敛为一个镜像实体。
- **D10 范畴分工声明（本体论审核发现 5）**：executor-image 只规约**镜像本体契约**（内容全集、凭据零入镜像、非 root 基线、build 自检）；**运行时隔离语义**（tier 声明、资源限额、网络策略、telemetry）归既有 capability `isolated-observable-execution`——两者互补不重叠。凭据语义相容性：F9 的 per-user 三值经 env 注入**容器**，而主 spec「credentials never copied into the target **worktree**」约束的是目标面文件——env 注入 ≠ 拷进 worktree，两不变式各管一层、无冲突。

## Risks / Trade-offs

- **镜像体积**（node+python+claude+gh 全家桶约 1-2GB）：接受——体积优化是 ghcr.io/thin 切片的活，先正确后优化。
- **claude CLI 在容器内的未知首跑状态项**（D5 实测可能发现新状态）：验收 scenario 已锁行为（不挂起）；发现项当场补 Dockerfile。
- **bookworm python3.11 与 pyproject 要求漂移**（未来 debian 升版）：锁定 node:22-slim 的 bookworm 代际，升级是显式变更。
- **本机模式用户误以为必须容器化**：CLAUDE.template.md 补「可选容器化」一节，明确直跑仍是默认。
