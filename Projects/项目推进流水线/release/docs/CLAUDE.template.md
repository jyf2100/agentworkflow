# CLAUDE.md

这个仓是 **pa 项目推进流水线的 template 实例**（控制面）：你在自己的机器上用它编排研发任务，
流水线在你的**目标仓**（被控项目）里干活。控制面与目标面严格分离——本仓代码不直接改目标仓文件。

> 上游：pa-pipeline-template（由维护者的 vault 经 release 发布；见 RELEASE_NOTE.md 的上游状态）。
> 本文是你（使用实例的同事）视角的说明；流水线内部机制见 `Projects/项目推进流水线/CONTEXT.md`。

## 回流约定（先读这个）

**本仓的 `scripts/`、`.claude/agents/`、`openspec/` 是 template 投影，不是你的代码。**

- 发现 bug / 想要改进 → **回流上游**：提 issue 给 template 维护者 → 维护者在 vault 修复 → 下次 release 你
  `git pull` 拿到修复。**不要直接改 `scripts/`**——本地改动会让你的实例与上游分叉，升级时 bootstrap
  会警告漂移（警告不阻断，但分叉的修复责任在你）。
- 你的合法本地数据：`Projects/项目推进流水线/config/`、仓根 `.project-auto/`（profiles、sources、state、
  报告）。这些目录 gitignore、升级不检测不覆盖。
- 升级 = `git pull` + 重跑 `bash bootstrap.sh`（详见 RUNBOOK §0）。

## 任务通道指导（什么样的活走流水线）

**需要「新的绿色测试证据」的任务才走流水线**；其余直接跟你的 claude 干。判据：

| 走流水线（inject） | 不走（直接干） |
|---|---|
| 有明确验收标准（可用 `npm test` 判绿红） | 探索性调研、方案讨论 |
| 改动落在代码仓、可开 PR 复核 | 一次性脚本、文档润色 |
| 你能写出一页 PRD（背景 + 验收 + 来源） | 需求本身还没想清楚 |

原因：流水线的测试门要求 commit/push/PR 前必须有**新的绿色测试证据**——没有可判绿红的验收标准，
dev 循环会在验证闸反复打转，浪费你的 token 与时间。想不清楚验收就先别注入。

## 上下文准入两问（写 PRD 前自问）

1. **有据吗？** 任务来源能给出可追溯的 `source_path`（一条 issue、一段需求记录、一封邮件存档）。
   无据的 PRD 会被质量闸（critic）打回 revise/drop。**PRD 里的代码现状断言也要有据**：把断言的
   来源（如目标仓调研：命令 + 输出原样粘贴）写成一份 md 放 `仓根 .project-auto/state/inbox/`，
   frontmatter 的 `source_path` 指向它——7.3 试点中无佐证的现状断言被 critic 以 traceability 打回。
2. **可验收吗？** 验收标准能落成目标仓里可执行的测试（`scripts.test` 入口跑得动、判得出绿红）。
   验收写不成测试 = 通道选错了（回上表，直接干）。

## 安装与配置

```bash
git clone <template 仓> && cd <clone>
bash bootstrap.sh            # 前置冒烟（Python≥3.11 + 你的 claude CLI）+ 依赖 + 实例数据初始化
```

- **模型层是你自己的 claude CLI**：流水线所有语义活（抽信号/审 PRD/验证产出）经本机已登录的
  claude CLI 跑，费用走你的账号；bootstrap 会做端点连通冒烟。不配任何共享模型凭据。
- **profiles**：仓根 `.project-auto/profiles/<project>.yaml` 描述你的目标仓（repo = 目标仓**本地
  clone 路径**、`type: code` 代码仓准入门、准入开关）；样例与逐字段注释见
  `bootstrap/samples/profile.example.yaml`。
- **采集源（可选，自动雷达才需要）**：仓根 `.project-auto/sources.yaml`（GitHub 仓/微信/深研），
  手动注入不需要它。
- **通知（默认关）**：见 `config/notify.example.env`；不配即报告落盘即止。

## 日常使用

```bash
# 1) 首次接入一个目标仓（准入体检：branch protection / 测试入口 / commit 身份）
python3 Projects/项目推进流水线/scripts/admit_repo.py <owner/repo>

# 2) 手动注入一份 PRD（YAML frontmatter 需含 project 字段——见「上下文准入两问」）
python3 Projects/项目推进流水线/scripts/run_daily.py --from-stage inject --inject-prd <prd.md>

# 3)（可选）本地面板：上传 PRD + 只读看状态/PR（默认 127.0.0.1，零构建）
python3 Projects/项目推进流水线/webapp/app.py
```

- **目标仓前置条件**：流水线只对 **main 已开分支保护** 的仓投递（防 agent 直推裸 main）。
  免费个人账户的**私有仓开不了分支保护**（Pro/组织功能）→ 不可作目标仓；转公开、或用
  组织/Pro 下的私有仓。admit_repo 的 protection 检查会如实报（UNKNOWN/NOT_FOUND 都会拦）。
- 注入一条龙会跑到 dispatch+report；产出的 PR 出现在**你的目标仓**，验证闸（pa-verify）
  判绿后由流水线兜底开 PR，你正常 review 合入。

## 可选容器化（默认仍是本机直跑）

**本机直跑是默认形态**——上面的安装与日常使用不依赖 docker。容器化是可选项，适合想把
流水线整体隔离在固定环境里跑的场景（执行面服务的部署形态也基于同一镜像）。

仓根自带 `Dockerfile` + `.dockerignore`（随 template 发布）：

```bash
docker build -t pa-executor:$(git describe --tags 2>/dev/null || echo local) .
docker run --rm -it \
  -v "$PWD/.project-auto:/workspace/.project-auto" \
  -e ANTHROPIC_BASE_URL=... -e ANTHROPIC_AUTH_TOKEN=... -e GH_TOKEN=... \
  pa-executor:local --help
```

- 镜像含 python3.11 + pa 依赖 + claude CLI + gh CLI（版本在 Dockerfile ARG 钉扎）；
  入口即 `run_daily.py`（`docker run <image> --help` 可查参数）。
- **凭据经 env 注入容器**（三值：`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` /
  `GH_TOKEN`），镜像本身零凭据；与本机直跑「模型层是你自己的 claude CLI」语义一致，
  只是登录态从本机 CLI 换成 env。
- 容器内目标仓 worktree 挂载在 `/workspace`——目标面仍是你的目标仓，不随镜像分发。

## FAQ

**Q：注入后想中断？** run 进行中时 `.run.lock` 被占（拒绝并发是刻意的）；中断用
`kill <pid>`（状态接口可查 pid），中断后 `--from-stage <stage>` 断点续跑。

**Q：dispatch 结果在哪看？** 仓根 `.project-auto/state/dispatch_<stamp>.json`
+ 面板状态区；dev 日志在 `state/runs/<project>/`。

**Q：重跑提示「复用已有 prd_gate/dispatch」？** 段产物按当日 stamp 落盘即复用（幂等设计，
防重复投递烧钱）——同日想真重跑加 `--force`。断点续跑用 `--from-stage <stage>`（critic/dispatch
会从盘读上一段的产物接续）。

**Q：critic 把我的 PRD 打回了？** 看 revise 意见（manifest 同目录产物）——通常是无据（缺
source_path）或验收不可执行，补 PRD 再注入一次即可（注入始终是新 run）。

**Q：webapp 能给团队远程用吗？** 不能默认如此——面板默认 bind 127.0.0.1 无鉴权（localhost 单人
场景）。`--host` 显式远程绑定是你自己的决定，需自担鉴权与暴露风险。
