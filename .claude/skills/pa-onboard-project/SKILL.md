---
name: pa-onboard-project
description: Onboard a new target repo into the project-auto pipeline (pa-radar→pa-prd→pa-prd-critic→dispatch→dev-agent). Two axes: (A) admit the target repo (profile + ADR-0003 admission trio: scripts/dev-agent.{py,mjs} + CLAUDE.md + GitHub branch protection, dispatch runtime wiring node vs python/conda env, data-hygiene gitignore, branch-protection config, admission/dev_agent_ready gating flip); (B) wire its information sources (ADR-0007: 5 source kinds in sources.yaml — directory/local-file/wechat-url/github-repo/agent-deepresearch — persona-based fetchers via FETCH_CONFIG dispatch, target_projects allowlist, drop-zone lanes, how to add a new kind). Use when adding/admitting a project, opening its signal feed, or adding a fetch kind. Does NOT touch target-repo business code or decide how the project tests (ADR-0002).
---

# pa-onboard-project — 把目标仓接入项目推进流水线

## 何时用

- 用户说「把 X 项目加进流水线 / 接入 project-auto / 让 X 能收到 PRD / 给 X 配 dev-agent」。
- 新建 `.project-auto/profiles/<project>.yaml`，或把某仓从 `admission:false` 翻到可投递状态。
- **给项目挂信息源 / 开投递箱 lane**（`Knowledge/投递箱/<project>/`），让 radar 能看见它（ADR-0007）。
- **加一个全新采集 kind**（建 `pa-fetch-<kind>` persona + 登 `FETCH_CONFIG`）。
- **不用来**：写 PRD（那是 pa-prd / 手写后 `--inject-prd`）、改目标仓业务代码（那是 dev-agent 自治）、规定项目怎么测（ADR-0002 项目自治）。

## 前置认知（动手前必读，SPEC 权威）

- **ADR-0001**：控制面**绝不污染目标仓**——`.project-auto/`、PRD 副本、run log 只在控制面；目标仓只多出它自己的 `scripts/dev-agent.*` + CLAUDE.md（仓自己的开发结构，不算污染）+ dev 代码 PR。**PRD Write 仅限 `state/prd/`，绝不写目标仓。**
- **ADR-0002（项目自治）**：pipeline 只投递 PRD + 信息源；**不画 boundaries、不规定开发流程、不替项目决定怎么测**。profile **不含** `boundaries`/`test_cmd` 字段。验证闸的测试命令由 **dev-agent 自治发现、运行、上报**（stdout JSON 的 `test_cmd`），dispatch **只重放、不替项目猜**。
- **ADR-0003（准入三件套）**：目标仓必须自带 ① `scripts/dev-agent.{py,mjs}`（claude-agent-sdk `query()` 跑 dev loop，语言跟仓栈：node 仓 `.mjs`、python 仓 `.py`）+ ② `CLAUDE.md`（dev-loop 自治守则段）+ ③ 远端主干的 **GitHub branch protection**（禁直推/禁 force-push/必须 PR）。dispatch 投递前**实查** `branches/<default_branch>/protection`，**404 即拒投**（平台态、运行时实查、不进静态 profile 字段）。
- **ADR-0004**：幂等由 run 锁 + GitHub 去重保证；dispatch 对账以 GitHub 为真源。
- **混合拓扑（ADR-0003/0005）**：控制面 3 persona = CLI（`claude -p --agent pa-xxx`），dispatch/report = Python 机械 stage；目标面 dev-agent = 仓自带 SDK 脚本。（ADR 文档落地差异：ADR-0001/0002/0003/0004 在 §11 `docs/adr/0001–0004` 有独立文档；**ADR-0005 仅 SPEC 正文内联引用**——决策#22 / §3.2 / §4.6，含义=控制面 dispatch/report 是机械 stage 不立 persona，**无独立 adr 文档**，去 `docs/adr/` 找 0005 会扑空。）
- **ADR-0007（多源 radar）—— 两条轴**：准入（下面 Steps 1–7）让项目**能收 PRD**；信息源（`.project-auto/sources.yaml`）让项目**有信号被 radar 拾取**。两轴独立：项目可已准入但无源（雷达看不见），也可有源但未准入（拾到信号也无处投）。**新项目通常两件都做。** 源有 5 种 `kind`（directory / local-file / wechat-url / github-repo / agent-deepresearch），后 3 种各有 **headless persona fetcher**（`.claude/agents/pa-fetch-<kind>.md`，经 `FETCH_CONFIG[kind]` 分发）；**消费侧 radar 零分支**——全靠 `content_glob` 按 `YYYYMMDD_*.md` 拾取，不认 kind。详见下方「信息源接入」整节。
- 关键路径：profile → `.project-auto/profiles/<project>.yaml`；**源集合 → `.project-auto/sources.yaml`**；编排器 → `vault/Projects/项目推进流水线/scripts/run_daily.py`（`load_sources` / `FETCH_CONFIG` / `stage_fetch` / `stage_radar`）；SPEC → `vault/Projects/项目推进流水线/SPEC.md`。

## 信息源接入（另一条轴 · ADR-0007）

> 准入（下面 Steps 1–7）让项目**能收 PRD**；信息源让项目**有信号被 radar 拾取**。两轴独立。源集合 = `.project-auto/sources.yaml`（gitignored）：每条源告诉 fetch 段「去哪采」+ 告诉 radar 段「采到的喂哪些项目」。

### 5 种 kind（采集层分类；消费侧 radar 零分支）

| kind | fetcher | 谁产 `YYYYMMDD_*.md` | mode |
|------|---------|----------------------|------|
| `directory` | 无 | 人手丢（如 `wechat` 源 = 微信知识目录） | — |
| `local-file` | 无 | 人手丢（投递箱 lane，见下） | — |
| `wechat-url` | `pa-fetch-wechat-url` persona | fetcher（web_reader + exa 兜底） | items（N 篇/次） |
| `github-repo` | `pa-fetch-github-repo` persona | fetcher（gh CLI / Bash） | items（N 仓/次） |
| `agent-deepresearch` | `pa-fetch-deepresearch` persona | fetcher（exa 深研合成） | single（1 份 md/次） |

- **fetcher 是 headless persona，不是 .py 脚本**：`.claude/agents/pa-fetch-<kind>.md`，编排器经 `claude --agent pa-fetch-<kind> -p --allowedTools <csv>` 链式调用。**sources.yaml 里 persona 源绝不写 `fetcher:` 键**——`load_sources` 按 `.py` 脚本查存在，写了会触发 missing-script warn。`directory`/`local-file` 也不写（本就无 fetcher）。
- **`FETCH_CONFIG` 分发**（`run_daily.py`）：`{kind: {agent, tools, prompt, mode}}`。`stage_fetch` 按 `FETCH_CONFIG.get(kind)` 分发：在配置里 → 调对应 persona；不在（directory/local-file/未知）→ 跳过。
  - `mode="single"`：一次调用产一份合成 md（agent-deepresearch）。
  - `mode="items"`：一次调用产 N 篇 → N 文件（**Contract A**：每 item 一个 `YYYYMMDD_<slug>.md`，slug = `dev_slugify(title)`，CJK 会被丢、24 字符上限——title 尽量 ascii）。

### 接投递箱 lane（最常见：给准入项目开一道人工喂入口）

每加一个 admission 项目，加一道 `local-file` 源（root 排他、name 唯一），并建目录 `Knowledge/投递箱/<project>/`（gitignored）：

```yaml
- name: drop-zone-<project>            # 全局唯一
  kind: local-file
  root: Knowledge/投递箱/<project>      # 全局排他（一 root 只属一源）
  content_glob: "**/[0-9]*.md"          # 必须；消费侧唯一识别手段
  target_projects: [<project>]          # 必须；缺省 = 不喂任何项目
  marker: state/consumed_dropzone_<project>
```

想喂项目时，丢 `YYYYMMDD_<slug>.md` 到该目录（**文件名日期 = 投递日，非内容日**——决定 #3）。

### 加一个全新 kind（四步，消费侧零改动）

1. **建 persona**：`.claude/agents/pa-fetch-<kind>.md`，frontmatter `name: pa-fetch-<kind>` + `tools: <该 kind 需要的 MCP/Bash>`；正文定输出契约（`{"items":[...]}` 或 single）+ 硬约束 + 禁区（不写盘、不自定文件名、不产 PRD）。
2. **登记分发**：`run_daily.py` 加 `def <kind>_prompt(src)` + `FETCH_CONFIG["<kind>"] = {"agent":"pa-fetch-<kind>", "tools":[...], "prompt": <fn>, "mode": "single|items"}`。
3. **用 kind**：`sources.yaml` 加源，`kind: <kind>`、`params:` 喂该 fetcher 的入参，**不写 `fetcher:`**。
4. **冒烟**：headless 实跑 `claude --agent pa-fetch-<kind> -p "<prompt>" --allowedTools "<csv>"`，确认工具齐 + 单行 JSON 合约 OK。**消费侧别动**——radar/discover 不认 kind，自动经 `content_glob` 拾取新 kind 产的文件；**严禁在消费侧加 `if kind==` 分支**。

### sources.yaml 字段速查

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | ✅ | 全局唯一（`load_sources` 重复即 `sys.exit` 拒载） |
| `kind` | ✅ | 5 种之一；缺省 `directory` |
| `root` | ✅ | 相对 `VAULT_ROOT`；**全局排他**（一 root 只属一源，重复 `sys.exit`） |
| `content_glob` | ✅ | radar 拾取 glob（如 `**/[0-9]*.md`）；**消费侧唯一识别手段** |
| `target_projects` | ✅ | 喂哪些项目（profile name 列表）；**缺省 = 不喂任何项目**（防新源忘标误喂） |
| `marker` | ✅ | `state/consumed_<src>`；仅"跳旧日"快路径，**非幂等真源**（幂等靠 GitHub 去重 + run 锁，ADR-0004） |
| `exclude_glob` | 可选 | 排 meta（审校报告/URL参考列表/文章清单） |
| `params` | kind 定 | fetcher 入参（wechat-url `urls`、github-repo `repos`+`window`、agent-deepresearch `prompts`） |
| ~~`fetcher`~~ | ❌ | **不写**——persona 源写了触发 missing-script warn |

## Onboarding 步骤（顺序执行）

### Step 1 — 写 profile（守门态）

新建 `.project-auto/profiles/<project>.yaml`，**`admission: false` 起步**（未就绪前 radar/prd 不拾取，避免产出投不出去的 PRD）。字段见下方速查表。要点：

- `repo`: 目标仓**绝对路径**。
- `default_branch`: **非 main 仓必填**（dispatch 实查 `branches/<default_branch>/protection`；填错→404 拒投）。
- `conda_env`: python 仓填（dev-agent.py 与独立验证闸用的 conda env 名）；node 仓留空。
- `type: code`（doc 类不进 dev loop）。
- `merge_policy: branch-only`（固定：永不自动合主干）。
- `match_surface`: 喂 pa-radar 做"贴合"判断（`one_liner` + `keywords`）。
- **不放** `boundaries` / `test_cmd`（ADR-0002）。

### Step 2 — 准入三件套（ADR-0003，目标仓内）

目标仓必须自带（缺一不可，否则 dispatch `fail`/拒投）：

1. **`scripts/dev-agent.{py|mjs}`**：claude-agent-sdk `query()` 跑自治 dev loop。`permission_mode=acceptEdits` + 有界 `allowedTools`（**不是** `bypassPermissions`；实际 9 个：`Read/Grep/Glob/Edit/Write/MultiEdit/TodoWrite/Bash/Agent`）；branch-only（feature 分支，**永不直推主干**——dev-agent.py 默认前缀 `pa-dev/`，dispatch 传 `--branch-prefix auto` 覆盖为 `auto/<stamp>-<slug>`）；SPEC #27 无进展刹车（验证红后连续 N_STALL=3 轮无写类 tool_use（`Edit/Write/MultiEdit`，Bash 不计）→ stalled，exit 12，不开 PR）；stdout 吐单行 JSON——核心字段 `ok/branch/base/cost/turns/test_cmd/test_passed` 常驻，**条件字段**：`pr_url` 仅成功开 PR 时、`run_log/stalled` 仅 stalled/dry-run 时出现（dispatch 端按 `.get()` 容错）。exit codes：0=成功(PR/dry-run) / 10=PRD 缺失 / 11=SDK loop 失败 / 12=stalled / 13=git·push·PR 失败 / 99=未捕获。
2. **`CLAUDE.md` dev-loop 自治守则段**：身份与触发 / 作业范围（新工作落版本化核心层，旧脚本仅参考；数据产物目录只读）/ 验证闸铁律（绿才算完，取数失败必 raise 禁静默兜底）/ 子代理分工 / 提交与停止 / 禁区（不直推主干、不删分支、不碰 branch protection、不静默兜底假数据）。
3. **远端主干 branch protection**：见 Step 5。

### Step 3 — dispatch 运行态适配（`run_daily.py`）

编排器要能认出该仓的 dev-agent。确认 `dispatch_one` 的**运行态探测**覆盖该仓（不靠静态字段）：

- 仓内有 `scripts/dev-agent.py` → 用 `conda_env` 的 python 跑（`_env_python(env)` = `CONDA_ENVS_DIR/<env>/bin/python`），PATH 注入 env bin 让 SDK 的 Bash `python` 解析到 env python。
- 仓内有 `scripts/dev-agent.mjs` → `node` 跑。
- 都没有 → `rec.status=fail, skip_reason="仓内无 scripts/dev-agent.{py,mjs}（ADR-0003 准入未满足）"`。
- `base` 取 `profile.default_branch`；独立验证闸**重放** dev-agent 上报的 `test_cmd`（无上报→跳过验证、注明"测试归项目自治"）。

### Step 4 — 数据卫生安全前置（公开仓尤其必做）

dev-agent 自治提交跑 `git add -A`——**.gitignore 漏忽略的数据产物会被全扫进 PR**（公开仓泄数据 + PR 混入无关大文件）。onboarding 前核查目标仓 `.gitignore` 覆盖：

- 数据/产物目录：`training_data/` `market_data/` `downloads/` `models/` `reports/` `cache/` `logs/` `*.log` `.env` 等。
- **源码目录下的数据产物**（易漏）：如 `back/*.json` `back/*.csv` `back/training_data_backup_*/` `*.npy` 等——这些不在标准数据目录里、但确是运行产物。
- 核查手法：`git ls-files | grep -E '\.(npy|json|csv|parquet|pkl)$'` 看有无已跟踪的数据泄漏；`git check-ignore <path>` 验 pattern 命中。**只忽略数据产物，别误伤 .py 源码**（先确认该目录只跟踪源码再加通配 pattern）。

### Step 5 — 配置主干 branch protection（ADR-0003 ③，平台兜底）

`gh api -X PUT repos/<owner>/<repo>/branches/<default_branch>/protection`。**最小可用**（branch-only dev loop）：

- `required_pull_request_reviews.required_approving_review_count: 0`（solo 自合并 OK；要求 PR 但不卡审批数）。
- **`enforce_admins: true`**（关键！dev-agent 用 owner SSH 凭证 = admin；`false` 则 admin 可绕过→保护对 dev-agent 形同虚设。设 true 才让"禁直推主干"真正绑住 dev-agent）。
- `allow_force_pushes: false`、`allow_deletions: false`。
- `required_status_checks: null`（**本仓无 CI 时务必 null**——要求 status check 会死锁所有 PR）。
- 验证：`gh api repos/<owner>/<repo>/branches/<default_branch>/protection` 返回 200（非 404）。

### Step 6 — 翻 admission

三件套全绿后，profile 改 `admission: false → true` + `dev_agent_ready: false → true`（③ 标记 ⏳→✅，注释更新为 protection 已配）。此刻 radar/prd 开始拾取该仓。

### Step 7 — 验证

- dispatch 实查 protection 返回 200（Step 5 已验）。
- 可选 dry-run / 单 PRD 投递测试：手写一份 PRD 落 `state/prd/<project>/`（或经 `run_daily.py --inject-prd <md>`，§4.2b，会补 frontmatter + 吐 manifest JSON）→ 过 critic 闸 → dispatch 投 dev-agent → 看是否在 feature 分支自改自验开 PR。
- 已知项（**归项目 dev-agent 自治，不归本 skill**）：仓内测试 harness 健康（pytest 缺失、`import src` PYTHONPATH、联网取数 flaky）——首跑大概率撞红，靠 dev-agent 按 CLAUDE.md 验证闸铁律自修或回滚。

## 边界（不做什么）

- **不改目标仓业务代码**——那是 dev-agent 自治（ADR-0002）。本 skill 只做"接入"：profile + 准入三件套引导 + dispatch 探测 + 数据卫生 + protection + 翻闸。
- **不替项目决定怎么测**——`test_cmd` 由 dev-agent 自治上报，dispatch 只重放（ADR-0002）。
- **不画 boundaries / 不规定开发流程**——profile 不含这些字段。
- **commit/push 仅用户授权时**；branch protection 配好后**不能直推主干**，所有改动走 feature 分支 + PR。

## profile 字段速查（`.project-auto/profiles/<project>.yaml`）

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | ✅ | 项目名（= 目录名） |
| `repo` | ✅ | 目标仓绝对路径 |
| `default_branch` | ✅（非 main 仓） | 主干名；dispatch 实查 `branches/<default_branch>/protection` |
| `conda_env` | python 仓 | dev-agent.py + 验证闸用的 conda env 名；node 仓留空 |
| `type` | ✅ | `code` / `doc`（doc 不进 dev loop） |
| `admission` | ✅ | `false` 起步守门；三件套全绿后翻 `true` |
| `dev_agent_ready` | ✅ | 准入前置自检（① dev-agent 脚本 ② CLAUDE.md ③ protection）全绿后翻 `true` |
| `goal` | ✅ | 一句话项目目标 |
| `tech_stack` | ✅ | 技术栈列表 |
| `current_focus` | ✅ | 当前推进方向（PRD 贴合判断用） |
| `merge_policy` | ✅ | 固定 `branch-only` |
| `max_prs_in_flight` | ✅ | 该项目在途 PR 上限（人工 + 流水线） |
| `match_surface` | ✅ | `{one_liner, keywords}` 喂 pa-radar |
| ~~`boundaries`~~ | ❌ | 不放（ADR-0002 项目自治） |
| ~~`test_cmd`~~ | ❌ | 不放（dev-agent 自治上报，ADR-0002） |

## 完成检查清单（done criteria）

- [ ] `.project-auto/profiles/<project>.yaml` 存在，字段齐全，`admission:false` 起步
- [ ] 目标仓有 `scripts/dev-agent.{py,mjs}`（语言跟仓栈）
- [ ] 目标仓 `CLAUDE.md` 有 dev-loop 自治守则段
- [ ] `.gitignore` 覆盖所有数据产物（含源码目录下的），`git ls-files` 无数据泄漏
- [ ] 远端主干 branch protection 已配（`enforce_admins:true` + PR-required:0 + 禁 force/delete + 无 CI 死锁），`gh api .../protection` 返 200
- [ ] `run_daily.py` 运行态探测覆盖该仓（py 走 conda env python / mjs 走 node）
- [ ] `admission:true` + `dev_agent_ready:true` 已翻
- [ ] 边界守住：未改目标仓业务代码、未替项目定 test_cmd、未画 boundaries
- [ ] （另一条轴·ADR-0007）该项目的投递箱 lane 已开：`Knowledge/投递箱/<project>/` 目录 + 一道 `local-file` 源（`root`/`name` 不撞已有源）+ `target_projects: [<project>]`——否则 radar 看不见该项目、无信号可拾

## 常见坑

- **gh OAuth 缺 `workflow` scope → push 被拒**（历史 commit 含 `.github/workflows/*` 时）：改用 SSH remote（`git@github.com:...`）绕过 OAuth scope guard；或先剥离 workflow 文件。
- **公开仓数据泄漏**：`admission` 前必做 Step 4 数据卫生（dev-agent `add -A` 会把漏忽略的数据扫进 PR）。
- **`default_branch` 填错**：非 main 仓（如 `master`）protection 查 `branches/main/protection` 必 404 拒投——必须填实际主干名。
- **`enforce_admins:false` 的假保护**：dev-agent 用 owner 凭证（admin）能绕过→保护形同虚设。必须 `true`。
- **要求 status check 死锁**：本仓无 GitHub Actions 时，勾"require status checks"会让所有 PR 永远无法合并。无 CI 时 `required_status_checks: null`。
- **conda env PATH 未注入**：dev-agent.py / 验证闸跑 `python` 时若 PATH 没注 env bin，会落到系统 python 缺依赖。`build_env_for_sdk` / `_env_python` 要把 env bin 前置到 PATH。
- **`fetcher:` 键写进 persona 源**：`load_sources` 按 `.py` 脚本查、查不到 → warn「声明 fetcher 但脚本不存在」。persona 源（wechat-url/github-repo/agent-deepresearch）**绝不写 `fetcher:`**，`directory`/`local-file` 也不写。
- **github MCP headless 不可用**：`mcp__plugin_ecc_github__*` 只在交互 session 注入，headless `claude -p` 工具集仅 `context7/exa/web_reader/4_5v_mcp`。github-repo 走 **gh CLI / Bash**，`FETCH_CONFIG` 写 `"tools": ["Bash"]`，不挂 github MCP。
- **headless 首次 Bash 调用偶发 permission_denial**（scope 无关——`Bash(gh api:*)` 与 plain `Bash` 都中，疑似首次调用 gate；后续调用正常）→ github-repo persona 内置「deny 即原样重试一次」；`FETCH_CONFIG` 用 plain `Bash`（恢复调用不受 scope 限）。**别因一次 denial 放弃该仓**，否则 commits_count 会像冒烟那样误报 0。
- **web_reader 对 `mp.weixin.qq.com` 常被反爬挡**（返回验证页/残缺正文）→ exa `web_fetch_exa` 兜底常才是实际抓回正文那一路；wechat-url persona 已把「web_reader 失败是常态、果断走 exa」固化进守则。
- **`target_projects` 漏标**：新源默认不喂任何项目，雷达拾到信号也不投 PRD——**显式列出**订阅项目。
- **root 跨源复用 / name 重名**：`load_sources` `sys.exit` 拒载（防双扫 / marker 互污 / candidate 重复）。每源独立 root + 独立 marker。

## 参考资料

- SPEC：`vault/Projects/项目推进流水线/SPEC.md`——§2 决策记录（ADR-0001/0002/0003/0004/0005）、§3.3 目录与产物布局、§4.1 pa-radar、§4.2 pa-prd、§4.2b stage_inject（`--inject-prd` 手动注入 PRD）、§4.3 pa-prd-critic、§4.4 pa-dispatch（实查 protection/限量/对账）、§4.5 dev-agent。
- 编排器：`vault/Projects/项目推进流水线/scripts/run_daily.py`（dispatch_one 运行态探测 / `_env_python` / `independent_verify`）。
- 控制面 persona：`vault/.claude/agents/pa-{radar,prd,prd-critic}.md`。
- 范例 profile：`vault/.project-auto/profiles/ashare-llm-analyst.yaml`（python 仓 + conda_env + master 主干，完整字段）。
- dev-agent 范例：`ashare-llm-analyst/scripts/dev-agent.py`（python 仓版）、cc-web-control `scripts/dev-agent.mjs`（node 仓版）。
- **信息源（ADR-0007）**：ADR 文档 `vault/Projects/项目推进流水线/docs/adr/0007-multi-source-radar.md`（5 kind 表 + follow-up ①②③ 状态 + 冒烟发现）；源集合 `vault/.project-auto/sources.yaml`（6 源范例，覆盖全 5 kind）；采集 persona `vault/.claude/agents/pa-fetch-{deepresearch,wechat-url,github-repo}.md`；分发与落盘 `run_daily.py`（`FETCH_CONFIG` ~L490 / `stage_fetch` ~L515 / `_payload_to_items` ~L506 / `load_sources` ~L130 / `discover_today_new` ~L180）。
