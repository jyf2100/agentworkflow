# harden-verify-test-scope

## Why

独立验证的测试范围存在**同盲裂缝**：dev 侧发布门（`evidence.py` TestEvidence）保证「最新测试绿 + 之后无写」但**不约束测试命令范围**；编排器侧 `independent_verify`（`run_daily.py` L3308-）双轨中 Node 轨跑 `npm ci + npm test`（package.json `scripts.test` 即项目全量门，独立于 dev），而 **Python 轨只重放 dev-agent 上报的 `test_cmd`**（L3327-3330：dev 未上报则跳过）。若 Python 目标仓的 dev 上报窄命令（如单文件 `pytest scripts/test_foo.py`），两层验证都绿、发布照常，**回归漏过**——两层都绿的「证实」其实共享同一盲区，独立验证失去独立性。

对照：Anthropic `claude-quickstarts/autonomous-coding`（long-running agents 官方参考实现）的 coding prompt 强制「新会话先跑已通过的用例」——独立于 dev 自选测试面的回归验证是长时程 agent 的既有实践。

「测试归项目自治（ADR-0002）」不与本案冲突：**profile 声明的命令是项目方自己写的**（onboard 时落 `.project-auto` profiles），用 profile 命令不是控制面替项目猜怎么测，而是项目自治的显式、全量表达。

## What Changes

- `independent_verify` Python 轨测试命令优先级改为：**profile 声明的 `verify_test_cmd` > dev 上报 `test_cmd` hint > 跳过（注明）**。
- profile schema 新增可选字段 `verify_test_cmd`（项目全量测试命令；无声明零变更）。
- Node 轨不动（`npm test` 已是全量门）。
- 独立验证结果 note 记录命令来源（`profile` / `dev-reported`），审计可分辨用了哪个面。

## Capabilities

### New Capabilities

（无。）

### Modified Capabilities

- `verified-dev-execution`：新增 **"Independent verification test scope"** requirement——Python 轨独立验证命令的优先序与来源标注；profile 声明优先于 dev 上报。与 `verify-loop-discipline` delta 的关系：B1 把 `test_rc` 定为三维度审核的证实输入之一（本变更保证该输入的**范围独立性**）、B2 的基建三分管 install 失败（本变更管测试命令选择），三者正交、无 requirement 交集。

## Impact

- **代码（改）**：`run_daily.py` `independent_verify`（Python 轨优先序 + note 来源标注，约 15 行）；profile 读取处透传 `verify_test_cmd`。`graph_pa`* 对应 node 同步（直调面同改）。
- **不变**：ADR-0002 项目自治哲学（profile = 项目自治声明）；Node 轨；dev 侧 TestEvidence 发布门；`test_rc` 喂 pa-verify 的既有链路。
- **取舍**：profile 声明的命令可能过时（项目测试入口变了未同步）→ 命令失败即 `test_rc≠0` 走既有红路径（fail-safe 方向正确：过时命令红 → 拦发布 → 人工发现并更新 profile，好过静默窄绿）。
- **测试（新增）**：① profile 有 `verify_test_cmd` → 用之（即使 dev 上报了 hint）；② 无声明 + dev hint → 重放 hint（现状回归锚）；③ 皆无 → 跳过 + note（现状）；④ note 正确标注来源；⑤ Node 轨行为不变（回归锚）。
- **风险**：低——纯优先序变更，无新状态、无新通道；最坏情形是过时 profile 命令导致 fail-safe 红，方向安全。
