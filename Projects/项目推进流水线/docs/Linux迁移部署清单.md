# 项目推进流水线 · Linux 迁移部署清单

> For future Claude（也供 roc 在 Linux 服务器上手操）：本文是「跨平台化代码已落（commit 51c3e82）后，把整套流水线迁到 Linux 自洽运行」的操作单页。代码侧已跨平台，这里只剩**装依赖 + 迁配置 + rsync 数据 + 自测 + 装 cron**。
>
> 配套设计见 `~/.claude/plans/flickering-pondering-ripple.md`。状态：2026-07-17，代码改动已提交，待部署。

---

## 0.5 实际部署坐标（本批固定值）

| 变量 | 值 | 说明 |
|---|---|---|
| **HOST** | `ubuntu@172.32.153.184` | SSH 免密（key 已配）；ubuntu 用户 sudo 免密（装依赖不卡密码） |
| **VAULT_LX** | `/mnt/disk01/workspaces/worksummary/vault` | vault 在服务器上的绝对路径 |
| **目标仓（建议）** | `/mnt/disk01/workspaces/worksummary/cc-web-control` | 与 vault 平级；按实际目录布局可调 |
| **LiteLLM proxy** | `http://172.32.153.184:4000` | 即服务器本机 IP；`settings.json` 的 `ANTHROPIC_BASE_URL` 保持原样（认证已跑通，本地可达） |

> 下文所有 `linuxsrv:~/vault/` 已替换为 `ubuntu@172.32.153.184:/mnt/disk01/workspaces/worksummary/vault/`，`~/vault` 替换为绝对路径。

---

## 0. 已就位（代码侧，commit 51c3e82）

| 改动 | 文件 | 说明 |
|---|---|---|
| 凭据跨平台 | `scripts/smtp_send.py` | macOS→Keychain / Linux→pass，`PA_SMTP_PASSWORD_FILE` 文件回退；macOS 行为不变（self-test rc=0 验证过） |
| 路径推导 | `scripts/run_cron.sh`、`scripts/install_cron.sh` | VAULT 从脚本位置上溯推导 + `PA_VAULT` 覆盖，去硬编码 |
| 平台提示 | `scripts/install_cron.sh` | 凭据提示按 `uname -s` 分支；删 macOS 磁盘访问提示 |
| NVM glob | `scripts/run_daily.py` | claude 兜底改 glob（去硬编码 v25.8.1） |
| rc=2 提示 | `scripts/run_daily.py` | 平台无关 + 默认 sina |

> **回滚依据**：vault 无 remote，但 Mac 上 git 历史完整——`git revert 51c3e82` 即回退全部代码改动。

---

## 1. Linux 装依赖

```bash
# ubuntu 用户 sudo 免密，下列 sudo 命令不卡密码。
sudo apt update
sudo apt install python3 python3-yaml git pass gnupg

# claude_agent_sdk —— 控制面标准执行器 dev-agent.py 的硬依赖（ADR-0006 上收后：Python 执行器
# 需 Python SDK；旧版用仓内 node dev-agent.mjs 走 Node SDK，已废弃）。装进控制面 python3（本机
# = miniconda base，即 `python3` 解析到的那个）。dev-agent 由 run_daily 经 `_env_python` 调起，
# profile 无 conda_env 时回退 python3 → 故 python3 必须有 SDK，否则 dispatch 记 dev_killed/fail
# （ModuleNotFoundError: claude_agent_sdk，2026-07-20 canary 实证）。
# ⚠️ 必须钉版本 ==0.2.121：0.2.123 起 can_use_tool 回调要求 streaming（AsyncIterable prompt），
#   dev-agent 用 string-prompt query() 会崩 "can_use_tool callback requires streaming mode"
#   （2026-07-20 canary 第 2 次失败实证；0.2.121 经 ashare 84-turn dev loop 验证可用）。
python3 -m pip install 'claude-agent-sdk==0.2.121'
python3 -c "import claude_agent_sdk; print('sdk', claude_agent_sdk.__version__)"   # 须显 0.2.121

# gh CLI（按官方源装：https://github.com/cli/cli#installation），然后：
gh auth login                                   # 写 ~/.config/gh/hosts.yml（不跨机拷）

# node >=18 + npm（nvm，与 Mac 同形）
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
. "$HOME/.nvm/nvm.sh"
nvm install 18 && nvm use 18 && nvm alias default 18

# claude CLI（用户称已跑通；若未装：npm i -g @anthropic-ai/claude-code）

# pass 密码库初始化（替代 macOS Keychain）
gpg --generate-key                              # 生成 GPG key，记下打印的 key ID（如 ABC123DEF456）
pass init "<GPG key ID>"                        # 用该 key 初始化密码库
```

---

## 2. 迁配置（认证 + 凭据）

| 项 | 操作 |
|---|---|
| **模型路由/认证** | `~/.claude/settings.json` 从 Mac 拷贝（含 `ANTHROPIC_BASE_URL=http://172.32.153.184:4000` + `ANTHROPIC_MODEL=glm-5.2` + `ANTHROPIC_AUTH_TOKEN`）。proxy 即本机，本地可达；用户称已配好——**确认此文件在且 `claude -p "ping"` 能通**。 |
| **gh 登录** | `gh auth login`（hosts.yml 不跨机拷） |
| **git push 凭据** | 目标仓 cc-web-control 的 push 凭据：SSH key 加 GitHub，或 `gh auth setup-git`（用 gh 的 https credential helper） |
| **SMTP 授权码** | `pass insert smtp/sina`（粘贴与 Mac Keychain 相同的 sina 授权码）；可选 `pass insert smtp/newland` |

> ⚠️ **cron 非交互读 pass 的坑**：gpg-agent 默认要密码解锁 GPG key，cron（无 tty）弹不了 pinentry → 邮件 rc=2（报告仍落盘，不阻塞）。配缓存：
> ```bash
> mkdir -p ~/.gnupg && cat >> ~/.gnupg/gpg-agent.conf <<'EOF'
> default-cache-ttl 86400
> max-cache-ttl 2592000
> allow-preset-passphrase
> EOF
> gpgconf --kill gpg-agent && gpgconf --launch gpg-agent
> ```
> 首次交互跑一次自测（解锁后缓存进 agent），cron 即可非交互读取。

---

## 3. rsync 数据（从 Mac 推到 Linux）

从 Mac 跑，目标 `ubuntu@172.32.153.184`（SSH 免密）。先在服务器建好父目录：

```bash
ssh ubuntu@172.32.153.184 'sudo mkdir -p /mnt/disk01/workspaces/worksummary && sudo chown -R ubuntu:ubuntu /mnt/disk01/workspaces/worksummary'
```

然后从 Mac（vault 根 `/Users/roc/dailywork/日常工作`）推：

```bash
# 流水线脚本 + 配置（含本次跨平台化改动）
rsync -av Projects/项目推进流水线/ ubuntu@172.32.153.184:/mnt/disk01/workspaces/worksummary/vault/Projects/项目推进流水线/

# .project-auto（profiles/sources/state/含 consumed_wechat_date 断点）
rsync -av .project-auto/ ubuntu@172.32.153.184:/mnt/disk01/workspaces/worksummary/vault/.project-auto/

# 3 个 headless persona
rsync -av .claude/agents/pa-radar.md .claude/agents/pa-prd.md .claude/agents/pa-prd-critic.md \
   ubuntu@172.32.153.184:/mnt/disk01/workspaces/worksummary/vault/.claude/agents/

# 信号源（radar 输入，否则天天空跑）
rsync -av Knowledge/微信/ ubuntu@172.32.153.184:/mnt/disk01/workspaces/worksummary/vault/Knowledge/微信/

# 目标仓（仓 CLAUDE.md + .claude/hooks/scope-bash.cjs；**不再需要 scripts/dev-agent.*** —— 执行器已上收控制面，见 ADR-0006）
rsync -av /Users/roc/workspace/cc-web-control/ ubuntu@172.32.153.184:/mnt/disk01/workspaces/worksummary/cc-web-control/
```

> wechat 信号源**持续产出**侧（we-mp-rss 服务 + wechat-articles skill + Projects/data/we-mp-rss/）是否也迁，是迁移后独立决策；先保证 vault 里已有历史信号能跑通。

---

## 4. 目标仓就位（Linux 上）

```bash
cd /mnt/disk01/workspaces/worksummary/cc-web-control
npm install                                    # 装 claude-agent-sdk@0.3.210 等

# 仓本地 commit 身份（SPEC Phase-2；global/repo 均空则 dev-agent 开 PR 时 commit 失败）
git config user.name "roc (项目推进流水线)"
git config user.email "9880962+jyf2100@users.noreply.github.com"

# 确认 origin 远程在（dispatch 运行时会用 gh API 校验 branch protection）
git remote -v
```

---

## 5. profile repo 路径 + 自测（Linux 上，从 vault 根跑）

先改 profile repo 指向 Linux 目标仓路径（**Mac 上的 yaml 不动**——Mac 仍用 `/Users/roc/workspace/cc-web-control` 跑；只改 Linux 这份 rsync 过来的）：

```bash
# 编辑 /mnt/disk01/workspaces/worksummary/vault/.project-auto/profiles/cc-web-control.yaml 第 5 行：
#   repo: /mnt/disk01/workspaces/worksummary/cc-web-control
```

然后 4 步自测（每步独立验证一段链路，从 vault 根跑）：

```bash
cd /mnt/disk01/workspaces/worksummary/vault    # run_daily.py 必须从 vault 根跑

# ① cheap 段先验（不投递、不花钱）：radar→prd→critic
python3 Projects/项目推进流水线/scripts/run_daily.py --to-stage critic

# ② dispatch smoke（真投一个 PR，验控制面 dev-agent.py + gh + git push + npm test + 发布门/三态准入 全链路）
python3 Projects/项目推进流水线/scripts/run_daily.py --from-stage dispatch --to-stage dispatch --dispatch-limit 1

# ③ report 段（sina 发到 juyf，验跨域 + .md 附件 + cron 同款路径）
python3 Projects/项目推进流水线/scripts/run_daily.py --from-stage report --to-stage report --stamp <已有state的YYYYMMDD>

# ④ SMTP 直发自测（默认 sina，dvs→dvs，验 pass 凭据 + smtp.vip.sina.com:465 可达 + gpg-agent 缓存）
python3 Projects/项目推进流水线/scripts/smtp_send.py --self-test
```

---

## 6. 装 cron

```bash
bash /mnt/disk01/workspaces/worksummary/vault/Projects/项目推进流水线/scripts/install_cron.sh   # 幂等，写 17 3 * * *
crontab -l | grep run_daily                                                                     # 确认入表
```

> install_cron.sh 现已用 `PA_VAULT` 或脚本位置自动推导 VAULT，无需手改脚本。若 vault 不在脚本推导的 `../../..`（即 `/mnt/disk01/workspaces/worksummary/vault`），传 `PA_VAULT=/mnt/disk01/workspaces/worksummary/vault bash install_cron.sh`。

次日 03:17 看日志：`/mnt/disk01/workspaces/worksummary/vault/.project-auto/state/cron.log`。

---

## 验证矩阵（端到端含义）

| 自测通过 | 证明的链路 |
|---|---|
| ① critic 段 | python3 + pyyaml + claude CLI + 3 persona + LiteLLM proxy 全通 |
| ② dispatch smoke | python3 + 控制面 dev-agent.py + gh + git push + npm test + branch protection + 发布门/三态准入 全通 |
| ③ report 段 | pass 凭据 + sina:465 可达 + 报告/日报落盘 + 邮件附件 |
| ④ SMTP self-test | pass 凭据 + smtp.vip.sina.com:465 + gpg-agent 缓存（cron 前置） |
| cron 次日 | cron 包装器 PA_CLAUDE_BIN/VAULT 推导 + run_daily 全流程 |

---

## grill 共识与已知边界（2026-07-17 对齐）

> 迁移前 `/grill-with-docs` 逐项拷问后的决策记录。这些是**有意接受的边界**，不是缺陷。

- **信号源不迁（先不管）**：本次只迁流水线，不迁 we-mp-rss 服务 + wechat-articles skill。Mac 历史 `Knowledge/微信/` 随 rsync 带过去（`consumed_wechat_date=20260630`，含 **67 篇 7 月文章**待消费）。**跑完即断供**——此后 radar 天天空跑。信号源持续产出是迁移后独立决策（候选：Mac 增量 rsync 续命 / 一步迁 we-mp-rss Docker 服务）。
- **积压自然消化**：迁移后第一次 cron 全量跑，67 篇积压走 radar→prd→critic→dispatch 全链路。`max_prs_in_flight=2` 限速投递 + critic 默认怀疑 drop 多数，不会洪水开 PR。不重置 `consumed_wechat_date`。
- **每日心跳邮件**：`run_cron.sh` export `PA_HEARTBEAT=1` → report 段全绿也发一封状态邮件（标题带「全绿心跳」）。无头服务器上**「今天没收到邮件」= 流水线挂了**。手动跑 `run_daily.py` 不设此 env，行为不变（全绿不发）。心跳逻辑在 `run_daily.py:stage_report`。
- **node_modules 不带**：rsync cc-web-control 排除 `node_modules/`(276M)、`.playwright-mcp/`、`.superpowers/`、`.worktrees/`——Mac 编译的 native 模块 Linux 不兼容，到 Linux 后 `npm install` 干净装。
- **state 可安全迁移**：`dispatch_*.json` 用相对路径 + GitHub URL（不含 Mac 绝对路径）；`runs/*.log` 历史日志含 `/Users/roc` 但只是历史记录，不影响运行。

---

## 风险 / 注意

- **gpg-agent 非交互缓存**：见 §2 坑。这是 Linux 版的「Keychain 解锁」问题，不配缓存则 cron 邮件 rc=2（报告仍落盘，不阻塞）。
- **LiteLLM proxy = 服务器本机**：`172.32.153.184:4000` 既是服务器 IP 也是 proxy 地址，proxy 跑在本机，本地回环可达。认证已跑通即不用动 `settings.json`；若 proxy 实际不在本机，改 `ANTHROPIC_BASE_URL` 到服务器可达地址。
- **slugify 耦合（已消解，ADR-0006）**：旧版 `run_daily.py` 幂等闸须与仓内 `dev-agent.mjs` 的 slugify 子串匹配同步漂移；执行器上收后 `dev_slugify` 抽到单一源头 `scripts/slug_utils.py`，两侧 `from slug_utils import dev_slugify`，shadow 耦合消失，迁移无需再操心两侧一致。
- **L15 模式**：目标仓 cc-web-control 是外部仓，本次只 rsync 现状 + 重配 git 身份，**不改其代码**；日后若有代码改动走用户终端 PR。
- **vault 无 remote**：代码改动在 Mac 提交后随 rsync 带 Linux；Mac git 历史是唯一回滚依据。
- **Mac/Linux profile 并存**：Mac 上的 `cc-web-control.yaml` 仍指 `/Users/roc/workspace/cc-web-control`（Mac 还在跑）；只改 Linux rsync 过来的那份。两边 `repo` 不同不影响（各自本机路径）。

---

## 阻断处置（fail-safe / 发布门 — 运维 triage 指南）

> harden-project-pipeline 之后，dispatch 会因「远程态不明」或「测试发布门未过」记两类**阻断**状态（`blocked_external_state` / `blocked_test_gate`）。它们**既不算投递成功、也不算 verify 绿/红**，在报告「🚫 阻断」节单独列出、并触发邮件（subject 含「K 阻断」）。本节给出重试语义与运维动作。

### 重试语义（何时不需手动干预）

- **阻断当轮终态、不自动重试**：两类阻断都是当轮（当夜）的终态——不进 verify 闭环、不开 PR、不删分支（fail-safe：宁可不动，等运维判明）。`blocked_test_gate` **不**走 verify 的「红→增量重投」：门拦是 dev 自治产出质量问题，不是 pa-verify 语义修订。
- **次夜 cron 自动重试**：下一次 cron 全量重跑准入。远程态若已恢复可决断（`FOUND/NOT_FOUND`）→ PRD 正常推进；`already_dispatched` 幂等闸确保**已成功的 PRD 不会重复开 PR**。故绝大多数瞬时故障（GitHub 抖动 / token 短暂失效 / flaky test / 证据窗刚过期）**等次夜自愈即可**，无需手动。

### 运维动作（按阻断类型）

| 阻断类型（报告「🚫 阻断」节） | 判读 | 动作 |
|---|---|---|
| `blocked_external_state` · reason 含 `401`/`Bad credentials` | GitHub PAT 失效/过期 | `gh auth login` 刷新 → `gh auth status` 验证 → 次夜自愈；急则手动重投（见下） |
| `blocked_external_state` · reason 含 `timeout`/非零退出 | GitHub/Git 远程瞬时抖动、rate-limit | `gh api rate_limit` 看配额 + `ping github.com`；通常次夜自愈 |
| `blocked_external_state` · `blocked_check=inflight_count` | 在途 PR 查询不明 | 同上（远程态）；若持续，查 `gh pr list --state open` 手动核对在途数 |
| `blocked_test_gate` · `test_status=red` | dev 产出测试红（真缺陷） | 查 `state/runs/<proj>/<stamp>_<slug>.log` + 测试输出；**真缺陷需改 PRD/补反馈**后重投，非 flaky |
| `blocked_test_gate` · `gate_status=test_stale` | 证据窗过期（dev 跑测后又有候选写） | 多为 flaky/竞态；查 log，通常次夜自愈 |
| `blocked_test_gate` · `gate_status=test_not_run` | dev 未跑测试就到发布门 | 查 log——dev loop 是否被 budget/turns 截断或测试入口探测失败；仓 CLAUDE.md 测试入口可能要补 |

### 手动重投（急用，跳过等次夜）

```bash
# 同 stamp 重读那夜的 gate state，重跑 dispatch（幂等：已成功的不会重复开 PR）
python3 Projects/项目推进流水线/scripts/run_daily.py \
  --from-stage dispatch --to-stage dispatch --stamp <受阻那夜的YYYYMMDD>
# 或一次性走完 dispatch→report（重投 + 重出报告 + 发邮件）
python3 Projects/项目推进流水线/scripts/run_daily.py \
  --from-stage dispatch --to-stage report --stamp <YYYYMMDD>
```

- 先修根因（刷新 token / 等 GitHub 恢复 / 改 PRD）再重投——否则只会再次阻断。
- `--dispatch-skip-dev` 可只验「准入 + 阻断上报」不真起 dev loop（不写远程），适合排查准入链路本身。
- 安全网：即便手动重投，三态准入 + 幂等闸 + 发布门三重保护仍在——**不会因重投而超额或开脏 PR**。
