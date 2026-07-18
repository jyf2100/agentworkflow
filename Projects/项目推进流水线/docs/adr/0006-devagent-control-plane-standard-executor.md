# 0006 — dev agent 上收控制面为标准执行器（修订 0003 #1/#2、消解 0004 #4 残留耦合）

> 修订 [[0003-target-dev-agent-sdk]] 的 #1/#2（dev agent 仓 owning → 控制面 owning）；
> 消解 [[0004-dispatch-devagent-source-of-truth]] #4 的 slugify shadow 残留耦合。
> **不违背 [[0001-vault-target-isolation]]**——见决定 #2（源码位置上收 ≠ 运行时归属上收）。

## 决定

1. **dev agent 源码归控制面**：单一 `vault/Projects/项目推进流水线/scripts/dev-agent.py` 服务所有被控仓。被控仓**零 dev-agent 脚本**（灰度后删 `<仓>/scripts/dev-agent.{py,mjs}`）。统一 Python（调度器语言与被控仓语言解耦——SDK 在被控仓 cwd 里干活，调度器用什么语言无所谓）。

2. **cwd-相对纯调度器 → ADR-0001 平面隔离不变**：`REPO_ROOT = Path.cwd()`，所有 git/SDK 操作都用 REPO_ROOT。dispatch 时 `cwd=被控仓 worktree`，本脚本就地操作该仓。**源码在 vault、执行贴目标仓**——运行时平面隔离（控制面进程 vs 目标仓 worktree）与 ADR-0001 一致，仅源码维护位置上收。这是对 ADR-0003 当年拒绝替代 B（「dev agent 归控制面」）的回应：当年拒绝理由「违背 ADR-0001 dev agent 归目标仓」混淆了「源码归属」与「运行时归属」——dev-agent 归控制面源码，但运行时仍归目标仓（cwd）。

3. **仓特定知识归仓 CLAUDE.md（B1）**：目录结构 / 测试入口 / 受保护路径 / 环境名等**不进 dev-agent.py**——由各仓根 CLAUDE.md 承载，SDK 经 `setting_sources=["project"]` 自动加载。dev-agent.py 只含跨仓一致逻辑（CLI、退出码、stdout JSON 契约、SPEC #27 stall 刹车、通用 dev 守则 prompt）。

4. **定制走 profile 参数，脚本本体不归仓**：被控仓个别需求（更严的刹车阈值等）通过 profile 参数注入，不在仓内 fork 脚本。

5. **`dev_slugify()` 为分支 slug 单一源头**：抽到独立无依赖模块 `scripts/slug_utils.py`，dev-agent.py 与 run_daily.py 均 `from slug_utils import dev_slugify`，**消解 ADR-0004 #4 的「slugify 算法须与 dev-agent 同步漂移」残留耦合**——shadow 根因（脚本在仓、控制面被迫复刻）随上收消失。
   > **落地修正（2026-07-18）**：原字面 `from scripts.dev_agent import dev_slugify` 不可行——① dev-agent.py 是连字符文件名（Python 标识符不允许 `-`）；② `scripts/` 无 `__init__.py` 非 package；③ **致命**：dev-agent.py 顶层 `from claude_agent_sdk import (...)` 会被 run_daily.py 顶部 import 连带加载，而 run_cron.sh 用裸 `/usr/bin/python3`（无 sdk）跑 run_daily.py 顶层 → 每晚 cron 崩。故抽 `slug_utils.py`（仅依赖 `re`）作单一源头，精神不变（单一源头 + 消 shadow）。

6. **跨仓常量单一源头**：`N_STALL`/`MAX_BUDGET`/`WRITE_TOOLS` 只在 dev-agent.py 一处，消除多仓漂移（2026-07-18 实证：cc-web-control N_STALL=100、ashare=3，同一天人为制造的不一致）。

7. **工具白名单用 SDK `tools=`（硬限制），非 `allowed_tools`**：Python SDK 的 `allowed_tools` 仅是权限批准列表、非可用性白名单（SDK 文档实证）；历史 dev-agent 误用 `allowed_tools` 当白名单是 headless 安全缺口。上收顺手修。
   > **落地修正（2026-07-18）**：`tools=` 只限制工具**可用性**，但 `permission_mode="acceptEdits"` 下 Bash 仍需 approval、headless 无人批 → 测试跑不动。历史靠各仓 gitignored 的 `.claude/settings.local.json` 放行，worktree（尤其 `/tmp` 或跨机新克隆）摸不到 → `test_passed=false`（2026-07-18 dry-run 实证）。长效修法 = `ClaudeAgentOptions(can_use_tool=_can_use_tool)` 回调 + 无依赖模块 `scripts/bash_allowlist.py`（`decide_bash` 默认拒、放行测试/构建/VCS/只读族/仓内脚本、显式拒网络外传与破坏性操作；如实声明 prefix 匹配非硬沙箱，抗误操/注入但不抗定向逃逸），**把放行规则收敛进控制面单一源头、摆脱机器本地 settings 依赖**。验证：SDK `_warn_if_can_use_tool_shadowed` 无报警（回调不被 acceptEdits/tools= 架空），pytest 22 passed。

## 背景

ADR-0003 #2 定「dev agent 归目标仓、随仓走」，理由是 ADR-0001 平面隔离。实践中此决策产生两类**结构性成本**：

- **多仓漂移**：每仓一份 `dev-agent.{py,mjs}`，常量/逻辑各自演化。2026-07-18 实证——为修「dev 先诊断后改被过早刹车」把 cc-web-control 的 `dev-agent.mjs` `N_STALL` 3→100，但 ashare 的 `dev-agent.py` 仍是 3；改动需分别 commit 进两个被控仓 main，且 worktree 从各自 main checkout 才能拿到。
- **控制面被迫 shadow**：ADR-0004 #4 明记 dispatch 复刻 dev-agent slugify 算法做幂等前置闸，`⚠️残留耦合：算法须与 dev-agent 同步漂移，否则匹配静默失效`。只要脚本留仓，控制面就得复刻其内脏。

dev-agent 是 **cwd-相对纯调度器**（`REPO_ROOT=cwd()`，SDK 与 git 全用 REPO_ROOT）——物理位置无关，源码上收零运行时代价。两类成本皆随上收消失。「仓 owning」的收益（仓自治其开发结构）在 dev-agent 这层不成立：dev-agent 是 pa 流水线的标准执行器，不是仓业务代码。

## 考虑过的替代

- **保留仓 owning（现状）**：拒绝——漂移 + shadow 是结构性、已实证（N_STALL、slugify），不随时间自愈。
- **dev agent 归控制面、执行也跨仓远程操作被控仓 git**：拒绝——dev-agent 不只 git，还要 Edit/Write 改仓代码 + 跑 `npm test`/`pytest`，这些必须发生在被控仓工作树；vault 进程跨仓操作撞 worktree 锁/路径耦合。执行必贴目标仓（决定 #2）。
- **保留 .py + .mjs 两套搬 vault**：拒绝——漂移从「仓间」搬到「vault 内语言间」，痛点不变。统一 Python（调度器语言与被控仓解耦）。
- **仓特定指引 profile 参数化（B2）**：拒绝——控制面 profile 拥有仓业务知识（目录/测试入口），违背「仓自治其业务」。用仓 CLAUDE.md（B1，决定 #3）。
- **big-bang 切换**：拒绝——vault 版首跑必有暗坑（Python SDK 事件解析、`tools=` 字段实跑验证），big-bang 使所有仓同时断。用灰度（决定 #后）。

## 后果

- **推翻 ADR-0003 #1/#2**（dev agent 仓 owning → 控制面 owning）；**ADR-0001 平面隔离不变**（运行时仍贴目标仓 worktree，仅源码位置上收）。建议在 ADR-0003 顶加修订注指向本 ADR（follow-up）。
- **消解 ADR-0004 #4** slugify shadow：dispatch 改 `import dev_slugify`，删复刻逻辑（run_daily.py:567/694）。属 ADR-0004 #4 的残留耦合收尾。
- **灰度迁移**：profile 加 `dev_agent_source: vault|repo`（默认 repo），run_daily.py 按字段选源；先切 ashare-llm-analyst（Python 仓，最顺），绿后切 cc-web-control（废弃 .mjs），都绿后删两仓残余脚本/测试 + 删选源分支逻辑。
- **准入重定义**：run_daily.py:877 从「仓内有 `scripts/dev-agent.{py,mjs}`」改为「profile `dev_ready:true` opt-in + dev-agent 运行时自探测 test_cmd（看 package.json/pyproject 标志）+ 第一次 loop test 跑不动即 fail」。比「文件存在」更强（真跑过 test 才算 ready），贴 ADR 既有的「平台态运行时实查不进静态 profile」原则。
- **DXP**：dev-agent 调试改在 vault fixtures（极简 node+python 假仓），brakes 单元 + dev-loop 集成就地回归两语言，不碰真实被控仓。fixtures + brakes pytest = follow-up。
- **follow-up 待办**：① fixtures + brakes pytest；② run_daily.py 选源 + 准入 + slug import；③ 被控仓 CLAUDE.md 迁入仓特定 (B) 段；④ `tools=` 字段 0.2.x 实跑验证；⑤ ADR-0003 顶加修订注。
