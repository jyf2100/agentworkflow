# Tasks: add-executor-eng-skills

## 1. 快照入库

- [x] 1.1 从 `~/.claude/plugins/cache/claude-plugins-official/mattpocock-skills/1.3.1/skills/engineering/` 拷 20 个 skill 目录（拍平分类层）到 `hub/deploy/executor-skills/<name>/`（排除 `README.md`），核对每个 `<name>/SKILL.md` 存在
- [x] 1.2 写 `hub/deploy/executor-skills/PROVENANCE.md`（插件名 / 版本 1.3.1 / 来源 github.com/mattpocock/skills / MIT / 快照日期 / 拷贝命令；随包进镜像，容器内可溯源）
- [x] 1.3 （实施审计发现）`release/manifest.txt` 纳管 `hub/deploy/executor-skills/`——投影内容契约原本不含 hub/，不纳管则后续 tag 的 patch build context 缺 COPY 源（fail loud）

## 2. 镜像（patch 增量通道，RUNBOOK §15 第 2 步惯例）

- [ ] 2.1 全量版 `hub/deploy/pa-executor.Dockerfile` 加 COPY 层作真理源：`COPY hub/deploy/executor-skills/ /home/pa/.claude/skills/`（源路径为投影根相对全路径；chown pa:pa 对齐现有 agents 拷贝模式）
- [ ] 2.2 增量版 `hub/deploy/pa-executor.patch.Dockerfile`：`FROM pa-executor:v0.1.7` + 同 COPY（实际 build 走此通道，不重跑 apt/npm/pip 网络层，v0.1.4/v0.1.5 同法先例）
- [x] 2.3 变更先打进投影树，`docker build -f hub/deploy/pa-executor.patch.Dockerfile -t pa-executor:v0.1.8 /tmp/pa-template-v018`（build context = v0.1.7 release 树 `/tmp/pa-template-v017-rel` 副本 + 仅 skills 增量——v0.1.6/v0.1.7 的 /tmp 投影树先例，零未提交 script 夹带）；冒烟一：`docker run --rm --entrypoint sh pa-executor:v0.1.8 -c 'ls /home/pa/.claude/skills'` 21 项齐 + `cat PROVENANCE.md` 可读
- [x] 2.4 冒烟二：容器内 `claude -p --max-turns 1` 单轮问答确认会话能报出技能名（headless 列表可见性）

## 3. 部署切换（hub.env 改前知会用户）

- [x] 3.1 `hub.env` **与 `hub.env.example` 同步**升 `HUB_IMAGE_TAG_DEFAULT=pa-executor:v0.1.8`（example 是模板真理源）；`cd hub/deploy && docker compose up -d` 重启 hub；`docker exec deploy-pa-hub-1 printenv HUB_IMAGE_TAG_DEFAULT` 确认
- [ ] 3.2 真机软验证：下一次派发的容器内 dev 会话日志观察技能引用（不 gate）

## 4. 收尾

- [x] 4.1 质量门：仓库根 `bash hub/quality.sh` 全绿
- [x] 4.2 RUNBOOK §15 或 hub/README 记一句：executor 技能面来源与升级路径（刷新快照 → patch build 新 tag → hub.env 与 example 同步升）
