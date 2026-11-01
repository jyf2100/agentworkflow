# Design: add-helix-target-harness

## D1 快照钉定：git commit 进 PROVENANCE（否决运行时 marketplace add）

- **选**：从 vault 外参考克隆 `/mnt/disk01/workspaces/worksummary/helixharness`（shallow main）取 `helixharness/skills/` 快照入库，PROVENANCE 钉**完整 commit hash** `44ffc536f73d8af525b4795ad10384c516898576`（短 `44ffc53`，2026-10-30 18:30 +0800）。与 eng-skills 先例的差别：mattpocock 来自主机插件缓存（版本号钉定），helix 未作为主机插件安装——参考克隆是唯一钉定源，故钉 commit 而非插件版本号；hash 换算关系（克隆 `git -C ... log -1` 可复验）写进 PROVENANCE 刷新流程。
- **否**：运行时 `claude plugin marketplace add`。理由：① 架构红线——镜像内容必须 git 快照钉定，marketplace 是网络取件 + 凭据依赖，同 tag 不同时刻/不同机器取到的内容可漂移；② executor 容器无外网白名单与 GitHub 凭据，marketplace 首跑即断；③ 插件整包安装会带进 hooks/commands/agents（本 change 明确不进，见 D3/D6）。
- 目录名 `hub/deploy/helix-skills/`：与 `executor-skills/` 平级同模式（部署资产区的镜像内容快照区），命名对称即自解释。

## D2 范围：快照 8 个全量，容器安装 6 个（同名让位）

**快照侧全量 8 个**（1.3M，d-d2c 的 rules/config/scripts 自包含资产占 1.2M）：零清单维护（上游加 skill 刷新即得）、上游保真（PROVENANCE 范围行可与上游目录机械对账）。快照原名原样、不裁剪不汉化（沿 eng-skills「不改 skills 内容」口径）——**唯一例外**：d-d2c frontmatter `hooks:` 块中和（见下「降级面与中和」）。**随包 vendor 插件层 notice 三件**（LICENSE / NOTICE / THIRD_PARTY_NOTICES.md）：本仓公开携带 + 镜像再分发触发 Apache-2.0 保留义务；THIRD_PARTY_NOTICES 取插件层详版——含 `## superpowers`、`## mattpocock/skills` 逐文件 MIT 保留条款与改编表（installed 的 brainstorming/systematic-debugging 有 superpowers 血统，上游 tdd/code-review 即 mattpocock 改编版），根层同名文件只是含平台组件的路由表；LICENSE/NOTICE 两层逐字节一致（diff 已核）。刷新流程固化 vendor 步骤，义务随每次刷新续履。

**容器侧安装 6 个，`tdd` / `code-review` 同名让位**——这是本 change 的关键定夺：

- **冲突事实**：helix skills 含 `tdd`、`code-review`，与 `hub/deploy/executor-skills/`（mattpocock）同名；两者都拷至同一容器用户级目录 `/home/pa/.claude/skills/<name>/`，后拷者静默覆盖先拷者。
- **incumbent 让位（mattpocock 版保留）的理由**：① helix 版深度绑定 HelixHarness 工作流（code-review 评审 `<fixed-point>`..HEAD 并「按 HelixHarness 合同」修复复审；tdd 绑定 feature/Bug Fix 的 Spec/Tasks 与 eval-log）——容器内不装插件主体（D6），这些锚点全部悬空；mattpocock 版自包含通用，正是 v0.1.8 起已交付的形态；② 静默换掉已交付 skill 的内容违反最小意外，且升级链（eng-skills PROVENANCE）会被不知不觉架空。
- **实现（排除表 + 碰撞守卫，评审 R2）**：两个 Dockerfile 的拷贝层用 `case` 排除表跳过同名两项（排除表两行，非逐项白名单——上游新增 skill 自动流入）；排除表只解决**已知**撞名，对**表外新撞名**（executor-skills 20 名里 implement/pr/prototype/triage/research 等通用名高风险）加 build 侧守卫：拷贝前比对 executor-skills 快照源目录，撞名即 `exit 1` fail loud——守卫比对**源目录**而非已安装目录，未来叠层 build 重跑幂等不误伤。spec scenario 的「fail loud 而非静默覆盖」由守卫真成立；排除表与守卫的维护义务写进 PROVENANCE 刷新流程与 RUNBOOK 注记。
- **顺带收益**：按目录遍历（`helix-skills/*/`）不拷顶层 `PROVENANCE.md`——否则会覆盖 executor-skills 已落在 `/home/pa/.claude/skills/PROVENANCE.md` 的同名溯源文件；helix 的 PROVENANCE 随包进投影 `/opt/pa/hub/deploy/helix-skills/PROVENANCE.md`，容器内可读可溯源。
- **可见性成色（SKILL.md frontmatter 实证）**：安装的 6 个中 3 个模型可自主调用（d-d2c、d2c-fidelity-evaluator 为 `user-invocable: false`；feedback 无限制位），3 个 `disable-model-invocation: true` 惰性随行（brainstorming / git-diff-impact / systematic-debugging——headless dev loop 无人工 slash 通道，保留不裁，沿 eng-skills 记账口径）。排除的 2 个（helix tdd/code-review）在容器内由 mattpocock 同名版顶位，可调用面不缩。
- **降级面与中和（评审 R3）**：d-d2c 上游 frontmatter 带 `hooks:` 块（PreToolUse 守卫 matcher AskUserQuestion + Stop 守卫），命令引用 `${CLAUDE_PLUGIN_ROOT}/hosts/claude-code/d2c_hook_entry.cjs`，且自带 fallback：node 启动失败时 printf `permissionDecision:"deny"`（AskUserQuestion）/ `decision:"block"`（Stop）。官方 skills 文档（code.claude.com/docs/en/skills，本次勘察核阅）将 `hooks` 列为 SKILL.md frontmatter 字段，语义是「**skill 被调用时注册、持续整个会话**」，个人/项目级 skill 同样生效——即快照形态下 `${CLAUDE_PLUGIN_ROOT}` 悬空，d-d2c 一旦被调用，该会话后半程 AskUserQuestion 必被 deny、Stop 必被 block（dev loop 会话杀手）。本机 claude CLI 恰为容器同版 2.1.220 但无凭据，未能实证其解析行为——按证据方向（文档明示支持）**防御性中和**：快照内删除该 `hooks:` 块，其余内容 byte-identical（对上游 diff 已核：偏差集仅 hooks 块删除 + 下述标注块新增）；d-d2c 属 Apache-2.0 §4.1 修改文件，文件头（frontmatter 后首行）置修改标注块（改了什么 / 为什么 / 指回 PROVENANCE）；PROVENANCE 记偏差与依据，刷新快照时重复中和并保留标注；tasks 2.5 留容器内验证项（frontmatter 无 hooks + AskUserQuestion 不被 deny），若实证 2.1.220 不解析 skill frontmatter hooks，证据记回本节后中和可视为冗余保险保留。feedback 的 `{plugin_root}/reference/runtime-preflight.md` 硬引用是正文 prose（模型遵循的指令），其自带 fallback 规则可用，无确定性破坏面，不动。

## D3 agents 16 个不进镜像

helixharness/agents/ 是 3 个 `init-*-bootstrap` + 12 个 `scan-*` + 1 个 `unit-test-worker`（共 16，@44ffc53 ls 与 find 双口径核；勘察简报写的 17/13 计数有误，以克隆实数落档），全部服务 `/helix:init` 的**目标仓初始化**（扫描建档 + bootstrap 脚手）——那是 admission 工具范畴（本仓 spec: repo-admission 域），不是 dev loop 会话的执行面技能；真要引入属「目标仓扫描/建档工具选型」议题，列 follow-up。且 scan-* 是 subagent persona 定义，与容器内 pa-* persona 命名空间并存只会混淆（pa 面的 agent 定义 `tools:` 收窄契约不收外来户）——除非后续 change 给出强理由，不进。

## D4 遥测 consent 姿势：快照零面，整合是后续 change

- **上游遥测模型（源码确认）**：consent 默认 disabled（`helixharness/scripts/telemetry_consent.py` 默认 `effective_mode: "disabled"`）；enabled 时插件把 `docs/eval/<feature>/eval-log.json`（schema `helixharness-eval-ledger/1.0`）写进**目标仓**随业务代码提交——git 就是遥测通道；平台侧 `helixharness-platform/eval/cli/sync_helixharness_usage.py` 从 git 远端拉这些仓喂看板，异步 POST `/report`（gzip、≤100 items/批）只是可选补充。
- **本 change 的边界**：纯 skills 快照**不装插件运行时**（无 scripts/、无 hooks、无 commands）→ 不存在 consent 询问面、不存在 eval-log 写入面、不存在异步上报面。默认即 disabled，且没有执行体去违反它。
- **红线（写死，防后续 change 顺手破）**：本 change 及其镜像绝不预配任何 `HELIXHARNESS_*` 遥测 env、绝不代目标仓写 eval-log 账本、绝不在 consent 未显式解决前接平台 sync。上游协议红线（`reference/eval-protocol.md`）在容器语境同样成立：不得估算缺失数据、不自动提交推送、**配置地址不等于授权**。
- **后续 change（显式列档）**：若将来做「容器内 dev loop × helix 框架版本 A/B 评测」（上游平台 env `HELIXHARNESS_PLATFORM_EVALUATION_CLAUDE_BASE_URL/API_KEY/MODEL` 可指向 roc 代理），consent 预授权与平台遥测整合必须先走独立 change 显式解决上述三条红线——本 change 不埋任何前置钩子。

## D5 tag 通道与投影纳管

- 镜像 build `pa-executor:v0.1.35`（当前 v0.1.34）；镜像内容变化必须走新 tag（F13 语义：同 tag = 同内容）。
- **实际 build 通道 = 全量版 `pa-executor.Dockerfile` + rsync 投影副本 context**（RUNBOOK §15 演进注记：v0.1.19 起实际路线——patch.Dockerfile 的 FROM 注释链停在 v0.1.18，「v0.1.19 起未走 patch 面，勿据 FROM 行推断最新 tag」；eng-skills 时代 task 措辞照抄 patch 通道是本次评审 R1 纠正项：patch 版 FROM v0.1.17 叠层会缺 playwright/chromium harness 层与现行 pip 层，产出比 v0.1.34 内容倒退的「新 tag」，且 ls/grep/cat 冒烟抓不到该回归）。rsync context 下全量版网络层（apt/npm/pip/playwright）缓存命中，增量叠代秒级，无代价差。
- 两个 Dockerfile 都加层：全量版随 `COPY . /opt/pa` 进投影后 `RUN` 拷贝（case 排除 + 碰撞守卫 + chown，对齐既有 cp 模式）——实际 build 走它；patch 版显式 `COPY hub/deploy/helix-skills` + 同守卫拷贝 + chown，头部补 v0.1.35 注记——**仅作层序真理源同步**（两个文件对同一变更保持一致，防止未来谁走 patch 面时缺层），`FROM pa-executor:v0.1.17` 不动。
- `hub.env.example` 只升 `HUB_IMAGE_TAG_DEFAULT` 一行（模板真理源；文件尾有用户未提交的追加块绝不触碰）；`hub.env`（0600 gitignore 部署机文件）同步升属运行时 task，改前知会用户。旧队列项钉旧 tag 不受影响（入队时固化）。
- `release/manifest.txt` 已勘察确认**在仓内**（`Projects/项目推进流水线/release/manifest.txt`，executor-skills 纳管先例同文件）——本 change 直接改：加 `hub/deploy/helix-skills/` pathspec。投影内容契约原本不含 hub/，不纳管则后续 tag 的投影内容缺该路径（fail loud 同先例）。

## D6 Non-Goals（边界与理由）

- **不改 dev-agent.py prompt**：技能发现走 CLI 用户级 skills 目录扫描，零 prompt 改动（eng-skills 同法）；prompt 面动一发牵全身（dev loop 行为契约），技能可见性不需要它。
- **不动 SDK tools 白名单**：skills 是上下文/方法注入不是工具；dev loop SDK 会话的工具面（Bash/Read/…）与 pa-* persona 的 `tools:` 收窄一概不变——镜像内容扩大不等于工具面扩大（spec scenario 钉死）。
- **不进 commands / hooks / 插件主体**：dev loop 是 SDK 多轮会话，无 `/helix:*` slash 通道，commands 9 个进了也够不着；插件 hook 运行时（plugin.json 的 hooks.json + hosts/ 脚本）进镜像会在容器内注册行为——超出「技能面」范畴且引入未经评审的运行时；skill frontmatter 内嵌的 hook 声明按 D2「降级面与中和」处置（d-d2c 块中和 + PROVENANCE 记偏差）；scripts/hosts/reference/templates/workflows 一律不进（体积 + 维护面 + D4 的遥测面风险）。
- **PRD↔docs/specs 互译与四目录约定**：helix propose 工作流的目标仓文档约定（PRD 与 docs/specs 互译、四目录结构）是**目标面自决**事项——由目标仓决定要不要采纳，不随执行镜像强加；真要接是独立 change（且涉 dev-agent prompt，见上条）。

## capability 归属：execution-hub（随先例）

姊妹 requirement「executor 镜像工程技能面」（add-executor-eng-skills）放在 execution-hub——镜像内置技能面的**消费方**是容器内 dev loop 执行链，且升级/钉定语义（刷新快照 → 新 tag → hub 默认 tag 同步、旧队列项固化）与 execution-hub 的 F13「入队钉镜像 tag」同域。executor-image 的「镜像内容全集」枚举的是运行栈（python/claude CLI/gh/personas），先例未动它，本 change 同样不动（两技能面 requirement 同居一 capability，避免同构约束两处漂移；若未来重构挪 executor-image，应连姊妹 requirement 一起挪，不属本 change）。

## 验证口径

- 仓内：`openspec validate add-helix-target-harness --strict --no-interactive` 绿；快照目录 8 项各含 SKILL.md、无 symlink / .git 残留（已核）；d-d2c SKILL.md 无 `hooks:` 块（已核，grep 零命中）；拷贝循环守卫本地实演——常态 6 项通过、注入表外撞名 `implement` 时 exit 1 fail loud（已核，测试注入已清理）。
- build 后冒烟（运行时 task）：`docker run --rm --entrypoint sh pa-executor:v0.1.35 -c 'ls /home/pa/.claude/skills'` = executor 20 + helix 6 目录 + executor PROVENANCE.md；`grep -l HelixHarness /home/pa/.claude/skills/tdd/SKILL.md` 应**无**命中（tdd 仍是 mattpocock 版——让位实证）；`cat /opt/pa/hub/deploy/helix-skills/PROVENANCE.md` 可读；镜像层含 playwright（防 build 通道倒退回归）。
- hook 面（运行时 task 2.5）：容器内 `/opt/pa/.../d-d2c/SKILL.md` 无 `hooks:` 块；带凭据会话 load d-d2c 后调 AskUserQuestion 不被 deny（日志零「d-d2c bound guard」命中）。
- 真机：下一次派发的容器内 dev 会话按需引用（软验证，观察不 gate）。
