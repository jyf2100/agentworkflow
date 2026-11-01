# Tasks: add-helix-target-harness

## 1. 快照入库

- [x] 1.1 从参考克隆 `/mnt/disk01/workspaces/worksummary/helixharness`（main @ `44ffc53`）拷 `helixharness/skills/` 8 个 skill 目录（原名）到 `hub/deploy/helix-skills/<name>/`，核对每个 `<name>/SKILL.md` 存在、无 symlink / .git 残留（1.3M）；**vendor 插件层 notice 三件**（LICENSE / NOTICE / THIRD_PARTY_NOTICES.md——Apache-2.0 再分发保留义务，THIRD_PARTY_NOTICES 含 superpowers/mattpocock MIT 条款详版）
- [x] 1.2 写 `hub/deploy/helix-skills/PROVENANCE.md`（插件 helix / 来源 github.com/didi/helixharness / Apache-2.0 / commit `44ffc536f73d8af525b4795ad10384c516898576` / 快照日期 2026-10-31 / 范围与 6-8 同名让位 / 随包 notice 行 / **d-d2c hooks 块中和偏差记录（含文件头 §4.1 修改标注）** / agents 16 个实数 / 刷新流程含 vendor 步骤、排除表与守卫维护义务；随包进投影，容器内可溯源）
- [x] 1.3 `release/manifest.txt`（已勘察确认在仓内：`Projects/项目推进流水线/release/manifest.txt`）纳管 `hub/deploy/helix-skills/`——与 executor-skills 同法注释行 + pathspec，不纳管则后续 tag 的投影内容契约缺该路径（fail loud）

## 2. 镜像（全量版 Dockerfile + rsync 投影副本 build context——RUNBOOK §15 演进注记：v0.1.19 起实际路线，patch.Dockerfile 未再走）

- [x] 2.1 全量版 `hub/deploy/pa-executor.Dockerfile`：随 `COPY .` 进投影后加 RUN 拷贝层——`case` 排除 tdd/code-review（同名让位 incumbent）+ **碰撞守卫**（比对 executor-skills 快照源目录，表外新撞名 build 即 exit 1 fail loud）+ 逐目录拷至 `/home/pa/.claude/skills/` + chown pa:pa（按目录遍历不拷顶层 PROVENANCE.md，避免覆盖 executor-skills 的同名溯源文件）
- [x] 2.2 增量版 `hub/deploy/pa-executor.patch.Dockerfile`：`COPY hub/deploy/helix-skills` 进投影 + 同守卫拷贝 + chown；头部补 v0.1.35 注记（`FROM pa-executor:v0.1.17` 不动——注释链停在 v0.1.18 是既知状态；**本文件仅作层序真理源，实际 build 不走此通道**）
- [x] 2.3 变更先打进 build context（rsync 投影副本，RUNBOOK §15 演进注记命令），`docker build -f hub/deploy/pa-executor.Dockerfile -t pa-executor:v0.1.35 /tmp/pa-ctx-<N>`（**全量版**——patch 版 FROM 链停在 v0.1.18，叠上去缺 playwright harness 与现行 pip 层，产出内容倒退；rsync context 下网络层缓存命中秒级）；冒烟一：`docker run --rm --entrypoint sh pa-executor:v0.1.35 -c 'ls /home/pa/.claude/skills'` = executor 20 + helix 6 目录 + executor PROVENANCE.md，且 `grep -c HelixHarness /home/pa/.claude/skills/tdd/SKILL.md` 为 0（tdd 仍 mattpocock 版——让位实证）+ `cat /opt/pa/hub/deploy/helix-skills/PROVENANCE.md` 可读 + `grep -n playwright` 镜像层在（防通道倒退回归）——已实施 2026-10-31：rsync 投影副本 `/tmp/pa-ctx-b14`（§15 命令原样 + 追加排除 `hub/deploy/hub.env.bak-*`：0600 机密备份不可读且不应入镜像）；md5 抽验 helix-skills 全目录 + Dockerfile checkout↔context 逐字节一致；build 成功 `pa-executor:v0.1.35` sha256:362b30b8…；冒烟一四断言全过（ls=27 项=20+6+PROVENANCE.md；tdd HelixHarness=0 且 head 为 mattpocock 版；PROVENANCE 35 行可读；docker history 第 12 行 playwright 层在 + `/opt/ms-playwright/chromium-1248` 运行时在 + `playwright --version` 1.64.1）
- [x] 2.4 冒烟二：容器内 `claude -p --max-turns 1` 单轮问答确认会话能报出 helix 技能名（headless 列表可见性）——已实施 2026-10-31：凭据经 `--env-file hub/deploy/hub.env` + 0600 临时桥接文件（HUB_USER_ANTHROPIC_*→ANTHROPIC_*，值零回显、用后 shred）注入一次性容器，`claude -p --max-turns 1` 技能清点报出 helix 名 **d-d2c / d2c-fidelity-evaluator / feedback**——恰为 D2 预言的 3 个 model-invocable 面（brainstorming/git-diff-impact/systematic-debugging 为 disable-model-invocation 不列，符合预期）；同名 tdd/code-review 列出的是 mattpocock 版（让位一致）
- [x] 2.5 冒烟三（hook 面验证，评审 R3）：`grep -c "^hooks:" /opt/pa/hub/deploy/helix-skills/d-d2c/SKILL.md` 为 0（中和生效）；再起一次带凭据 SDK 会话 load d-d2c 后调 AskUserQuestion，确认不被 hook deny（fastpath：会话日志 grep「d-d2c bound guard」零命中）——若实证容器 CLI 2.1.220 不解析 skill frontmatter hooks，把证据记回 design D2 后本项降级为可选——已实施 2026-10-31：grep 条文断言投影面=0（rc=1）、已装面 `/home/pa/.claude/skills/d-d2c/SKILL.md` 同为 0；带凭据 SDK 会话（claude_agent_sdk.query，形态对齐 dev-agent：cwd/max_turns/permission_mode）Skill 工具载入 d-d2c（tool_result「Launching skill: d-d2c」，标注块随文入上下文）后令调 AskUserQuestion——**无任何 hook deny**，决定性 run 日志 grep「d-d2c bound guard」零命中（一次非决定性 run 的单命中经查原文为模型自身 paraphrase prose、非 hook 输出；hook 脚本 d2c_hook_entry.cjs 本就不在镜像）；该 headless 会话工具 schema 本无 AskUserQuestion（模型自述可用面清单无此项），deny 面不存在；**反向探针实证 CLI 2.1.220 会解析并触发 SKILL.md frontmatter hooks**（--rm 一次性容器内置临时探针 skill：PreToolUse(Bash) 真触发、marker 落盘 `fired`）→ 降级分支不适用，D2 中和为必要措施而非冗余保险（探针 skill 随容器销毁，镜像零污染）

## 3. 部署切换（hub.env 改前知会用户）

- [x] 3.1 `hub.env` 与 `hub.env.example` 同步升 `HUB_IMAGE_TAG_DEFAULT=pa-executor:v0.1.35`（example 已在仓内升那一行；hub.env 是 0600 gitignore 部署机文件）；`cd hub/deploy && docker compose up -d` 重启 hub；`docker exec deploy-pa-hub-1 printenv HUB_IMAGE_TAG_DEFAULT` 确认——已实施 2026-10-31：重启前查队列 sqlite `queue_items` = done 82 / failed 26、**无 running/pending**（docker logs 尾部静默一致；hub durable 设计，重启后同值无恙）；sed 锚定单行 `^HUB_IMAGE_TAG_DEFAULT=pa-executor:v0.1.34$`→v0.1.35（全文件 v0.1.35 仅 1 处、权限仍 600、他行未动）；`cd hub/deploy && docker compose up -d` → Recreated+Started；`docker exec deploy-pa-hub-1 printenv HUB_IMAGE_TAG_DEFAULT` = **pa-executor:v0.1.35** ✓；`docker ps` = Up（compose 未定义 healthcheck，以状态+启动日志「Application startup complete」计）
- [ ] 3.2 真机软验证：下一次派发的容器内 dev 会话日志观察 helix 技能引用（不 gate）——待下一次派发观测

## 4. 收尾

- [ ] 4.1 质量门：仓库根 `bash hub/quality.sh` 全绿（change 收官时跑；本次起草任务明确不跑）
- [x] 4.2 RUNBOOK §15 镜像注记节补「executor helix 技能面」注记（来源与钉定 commit / 6-8 同名让位与碰撞守卫 / hooks 中和偏差 / 无插件运行时无 consent 面 / 升级路径），紧随 executor 工程技能面注记
