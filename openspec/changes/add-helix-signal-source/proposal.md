# Proposal: add-helix-signal-source

## Why

HelixHarness（github.com/didi/helixharness，Apache-2.0；插件主体 = commands 9 / skills 8 / agents 16——3 init-*-bootstrap + 12 scan-* + 1 unit-test-worker，参考克隆实测，遥测 consent 默认 disabled、git 即遥测通道——eval ledger 随业务代码提交，平台侧 `sync_helixharness_usage.py` 从 git 远端回收）是已进入本仓评估视野的同源工程智能框架：它的协议演进（reference/eval-protocol.md 红线）、eval ledger 演化、平台评测出口（`HELIXHARNESS_PLATFORM_EVALUATION_*` env，OpenAI 兼容，将来可指向 roc 代理）对本仓的 skills/agents 治理与后续评测接入都是直接信号——应被 pa 的 radar 持续跟踪，而非靠人肉刷仓。

pa 流水线的本职信号管道正是为此设计的：sources.yaml 配 `kind: github-repo` 源 → `pa-fetch-github-repo`（gh CLI 拉 commits/pulls）→ radar 判相关性 → 产 PRD，零代码。现状缺口不是能力是**配置样例**：bootstrap sample 的 sources.yaml 只有 `sources: []` 与一段结构注释，没有一个可整块照抄的完整条目——且该结构注释把 kind 专属参数写成了平铺（fetch prompt 实际只读 `params.*`，`run_daily.py:1379-1381` / `:1367` / `:1356`），照旧注释抄出来的源 fetch 会静默空转。

## What Changes

1. **sample 加 github-repo 完整示例条目（didi/helixharness）**：`bootstrap/samples/sources.yaml` 以注释教学形态写入条目全集（对齐 profile.example.yaml 注释教学惯例与 ADR-0007 条目字段惯例）：`name` / `kind: github-repo` / `root: Knowledge/github-watch/` / `content_glob`（radar discover 必填）/ `params: {repos: [didi/helixharness], window: "7d"}` / `target_projects`（缺省不喂任何项目——防新源误喂）/ `marker`。live 段 `sources: []` 保持空——template 不替所有实例默认跟踪（bootstrap 会把本文件整份装成实例起点，活条目 = 每个同事机每日都去 fetch 一个与其项目无关的仓）。
2. **既有结构注释修正**（同文件，纯注释零行为）：kind 专属参数平铺 → `params` 嵌套（对齐消费面）；`topic` 更正为实际键名 `prompts`（列表）；kind 清单补 `forge-repo`（add-forge-adapter 已入 spec，注释滞后）。
3. **spec delta（template-bootstrap ADDED「sources 样例教学形态」）**：把样例契约钉进 spec——空模板活段 + 至少一个 params 嵌套的 github-repo 全集示例 + 「平铺键 MUST NOT 示例」（防刚修掉的漂移复发）。能力归属依据：template-bootstrap 本就管 bootstrap 样例面（既有「样例覆盖面」requirement 管住 profile.example.yaml 的教学形态，sources.yaml 是同一步骤装出的姊妹样例，`bootstrap.sh:63`）；亦与 add-executor-eng-skills 先例同构（仓内工件 → ADDED requirement 钉契约）。
4. **各机运行时激活 = 不入仓任务**（tasks 2.x 留 `[ ]`）：本机形态在仓根 `.project-auto/sources.yaml`、hub 形态在 `HUB_DATA_ROOT/<project>/sources.yaml` 落活条目（两者均 gitignore 实例数据），fetch 段单段冒烟验证。
5. **参考克隆钉定**：didi/helixharness main @ `44ffc536f73d8af525b4795ad10384c516898576`（2026-10-30，`git -C` 于 vault 外只读克隆取）——本 change 只做信号跟踪；将来把插件内容快照进 executor 镜像是独立 change，走 add-executor-eng-skills 模板（快照 + PROVENANCE + Dockerfile COPY + tag bump）。

## Non-Goals

- **fetch 源条目内容无 spec 管辖、forge-watch 零 delta**：勘察确认「条目里监控哪些仓」是实例数据，不属任何 capability 的 requirement 管辖面；forge-watch 管 sources.yaml 的 **kind 契约**（forge-repo schema 装载校验、github-repo 原样保留零变化），本 change 不触碰任何 kind 行为——故对 forge-watch 无 delta。delta 只落在 template-bootstrap 的样例教学形态（见 What Changes 3）；另一现实约束：openspec 1.4.1 `--strict` 拒绝零 delta change（实测 exit=1），样例契约与其留白，不如按先例钉进 spec。
- **不 vendor 插件内容**：helixharness 的 commands/skills/agents 不进本仓、不进镜像、不改 pa-* persona 工具面（ADR-0001 控制面/目标面分离；镜像内容 git 快照钉定红线）。
- **不改 fetch 代码**：github-repo kind 既有管道（FETCH_CONFIG → pa-fetch-github-repo → gh api）原样复用；bootstrap.sh 零触碰。
- **不在本 change 内激活各机实例**：`.project-auto/` 与 `HUB_DATA_ROOT/` 均 gitignore，激活属各机运行时任务（见 tasks 2.x）。
- **不接遥测/评测面**：helix 的 eval ledger 回收与平台评测 env（`HELIXHARNESS_PLATFORM_EVALUATION_*`）不属信号采集面，本 change 不碰；其红线（不自动提交推送、配置地址不等于授权）与只读跟踪天然不冲突。
