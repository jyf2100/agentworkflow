# `independent_verify` install 失败被吞 — 框架层诊断与三方案

> 诊断日期：2026-08-11
> 性质：**框架层通用缺陷**（控制面 `independent_verify` 验证闸），对所有 node 目标仓生效。cc-web-control #55/#56 是触发案例，非项目特化。
> 状态：诊断 + 方案设计完成，**未实现**（用户「只诊断不实现」）。

---

## 1. 摘要

pa 控制面的 `independent_verify`（通用验证闸，`run_daily.py:2615`）在 install 阶段（`npm ci`）失败时，直接 `return` 使 `test_rc=None`，且 install 的 stderr 被导向脏日志 `log_file`、**故意不进 `test_log`**（`:2622` 注释）；而 `verify_prompt`（`:640-647`）**只读 `test_rc`/`test_log`，不读 `install_rc`/`note`**——install 失败的事实被编排器**藏住**，pa-verify 拿到误导的「未跑（仓无测试）」state + 空的 test_log，**根本看不到 npm ci 的报错**。

**问题不在 pa-verify 的判断力，在编排器把输入蒙住了**——pa-verify 是语义对抗 persona，本应自行判断，却被剥夺了判的依据。**修法应是补输入让它自行判断，不是编排器替它判。**

后果：dev 自报测试绿 + 自开 PR（在 verify 之前），verify 却抓不住「manifest 与 lock 不同步」这个必然导致 CI 红的问题 → PR 推到远端 → CI 也跑 `npm ci` 失败 → PR 永远合不了 → 停滞；且触发「增量重投」死循环，产生 PR 链。

---

## 2. 根因因果链（框架层视角）

```
dev agent 改 package.json（加/改依赖）但忘更新 package-lock.json   ← 任何 node 仓通用
  ↓
dev worktree 跑 npm test 用旧 node_modules → 报绿                  ← 绕过 lock 检查
  ↓
dev agent 自开 PR（dev-agent.py:189/974 的 gh pr create）           ← 发生在 verify 之前
  ↓
independent_verify 全新 worktree 跑 npm ci → 失败（lock 不同步）
  ↓ run_daily.py:2651-2653  npm ci rc≠0 → return → test_rc=None（测试根本没跑）
  ↓
verify_prompt :642-646  test_state 只看 test_rc → 走「未跑」分支
  ↓ install_rc 信号未传递；「未跑」文案只说「仓无 scripts.test」，不提 install 失败
  ↓
pa-verify persona 拿到「未跑」→ 不知 install 失败 → 判 revise（模糊）
  ↓
编排器增量重投（round2，base=round1 分支）→ dev 又自开 PR（同样问题）→ PR 链
  ↓
CI 也跑 npm ci → 同样失败 → PR test=FAILURE → 合不了 → 停滞
```

**定性**：这条链的**根因在编排器（机械层），不在 pa-verify（语义层）**。pa-verify 是对抗 persona，职责就是自行判断 dev 产出是否验证通过；但编排器在 `:2652-2653`（install 失败 return 丢输出）和 `:640-647`（verify_prompt 不读 install_rc/note、test_log 排除 install 噪声）两处把 install 失败的事实**藏住了**——pa-verify 被蒙而非判错。**修法应是补输入让它自行判断，不是编排器替它判。**

---

## 3. 代码依据（行号锚点，2026-08-11 实证）

| 锚点 | 现状 | 问题 |
|---|---|---|
| `run_daily.py:2615` `independent_verify` | 通用验证闸：全新 worktree 重跑 test | Node 仓 → `npm ci` + `npm test`；Python → 重放 dev 上报的 test_cmd |
| `run_daily.py:2651-2653` | `npm ci` rc≠0 → `out["note"]="npm ci 失败"; return out` | **return 后 test_rc 永远 None**，install 失败被降级为「未跑」 |
| `run_daily.py:642-646` `verify_prompt` test_state | 只读 `test_rc`：`=0` 绿 / `≠None` 红 / `None`「未跑」 | **不读 `install_rc`**；「未跑」分支文案「dev 未报 test_cmd 或仓无 scripts.test」**不含 install 失败** |
| `run_daily.py:1894-1937` `reconcile_pr` | FOUND 保持 / NOT_FOUND 补开 / 无 commit 删分支 | **无 `gh pr close` 能力**（`pr_closed` 只是被动状态枚举） |
| `dev-agent.py:189/974` | `gh pr create --base ... --head ...` | dev agent 自开 PR 路径（在 verify 之前） |
| `run_daily.py:116-117` | `VERIFY_INSTALL_TIMEOUT=600` / `VERIFY_TEST_TIMEOUT=600` | install 与 test 分离的超时，但 install 失败时输出被丢、不传 pa-verify（编排器藏输入，非 pa-verify 判错） |

---

## 4. 触发案例（cc-web-control #55/#56 — 非特化）

cc-web-control（目标面项目，Claude Code Web）的 PR #55/#56 停滞 7 天，是本通用缺陷的触发：

- dev 把 `puppeteer-core` 列为 `optionalDependencies`（改了 package.json），**忘更新 package-lock.json**
- dev worktree `npm test` 报绿（1065/0、1067/0，用旧 node_modules 绕过 lock）
- dev 自开 PR #55（base=main）、#56（base=#55 head，增量重投 round2）
- `independent_verify` 全新 worktree `npm ci` 失败（package.json 与 lock 不同步）→ test_rc=None → verify revise 两轮
- CI `npm ci` 同样失败 → #55/#56 `test` check = FAILURE → 合不了 → 停滞

**关键**：换任何 node 目标仓，只要 dev 改了 manifest 忘 lock，都会触发同样链路。这是框架层 `independent_verify` 的缺陷，不是 cc-web-control 的问题。

---

## 5. 三个框架层方案（独立可实施，可递进组合）

### 方案 1〔输入层〕补全 install 失败的事实输入，让 pa-verify 自行判断

**切入根因**：编排器把 install 失败的输出藏住了（`:2652-2653` return 丢输出 + `:2622` test_log 排除 install 噪声 + `:640-647` verify_prompt 不读 install_rc/note），pa-verify 被蒙而非判错。

**机制**：编排器（机械层）只如实记录 install 的客观事实（`install_rc` + npm ci 的 stdout/stderr 写盘成 `install_log`）并喂给 pa-verify；**pass/revise 结论、原因、怎么修的 feedback 全部归 pa-verify（语义层）自行判断**。编排器不预判结论、不预写 feedback——守 CLAUDE.md「语义活交给 persona：审 dev 产出」。

| 改动点 | 现状 | 改向 |
|---|---|---|
| `independent_verify` :2651-2653 | `npm ci` rc≠0 → return（test_rc=None，install 输出进脏 log_file 不留存） | install 输出也写盘成 `install_log`（像 `:2665-2668` test_out 那样留存）；out 装上 `install_rc`（已记）+ `install_log` 路径。**仍 return，但带上 install 的事实，不丢** |
| `verify_prompt` :640-647 | 只读 test_rc/test_log；test_rc=None 时喂误导文案「未跑（仓无测试）」 | 把 `install_rc`/`install_log` 纳入呈现：install_rc≠0 时 state 如实写「install 失败（npm ci rc=X），输出见 install_log」。**不写预判的 pass/revise 结论、不预写 feedback**；persona 契约加「install_log 非空时必 Read」 |

**关键区别（守架构原则）**：~~原设计让编排器判 install=红 + 预写 feedback「跑 npm install 更新 lock」~~ = 编排器替 pa-verify 判断，把对抗 persona 降格成传声筒，**越界**。本方案 = 编排器补输入，pa-verify 自己 Read npm ci 报的「package-lock.json out of sync」、自己定位、自己写怎么修。**编排器只如实传事实，所有语义判断归 pa-verify。**

**通用性**：此 bug 路径**仅 node 仓存在**——`independent_verify` 双轨中，node 仓有 `npm ci` install 闸（`:2651-2653`，失败 → test_rc=None）；Python 仓走 else 分支、**无 install 闸**（`install_rc=0`、test 直接跑、test_rc 永真），不经此路径。方案思路（补输入让 persona 自行判断）可迁移 Python，但 Python 需先补 install 闸（如 `pip install` / conda env 依赖验证），否则无 install 事实可补。
**trade-off**：✅ 守「语义活归 persona」架构原则 ✅ 改动小、风险低 ✅ pa-verify 拿到真实报错能写出有用的 feedback ⚠️ 依赖 pa-verify 真去 Read install_log（persona 契约须写清「install_log 非空必读」，否则又退化成被蒙）。
**测试**：independent_verify mock install 失败 → assert out 带 `install_rc` + `install_log` 路径（非 None）；verify_prompt install_rc≠0 → assert state 含「install 失败」+ 指向 install_log，**且不含预判的 pass/revise 结论或预写 feedback**（守「编排器不替判」）。

### 方案 2〔源头层〕dev 产出保证 lock 一致

**切入根因**：dev 改 manifest 忘 lock，用旧 node_modules 绕过检查。

**机制**：dev 改了 manifest 后，**编排器代办 commit 前自动同步 lock**，让产出的 commit 本身 lock 一致。

**改动点**：编排器 dispatch 的代办 commit 处（cc-web-control 的 `scope-bash.cjs` 锁 git，commit 本就由编排器代办，正是注入点；ADR-0006 dev-agent 纯调度器不读控制面，故放编排器侧）——commit 前检测 diff 含 `package.json` → 跑 `npm install`（更新 lock，非 npm ci）→ 更新的 `package-lock.json` 纳入同一 commit。

**通用性**：所有 node 目标仓（改 package.json 触发）；Python 类比（requirements.txt + pip compile / uv lock）。
**trade-off**：✅ 源头杜绝，产出即一致 ✅ 防御纵深 ⚠️ 改动面较大 ⚠️ `npm install` 生成的 lock 变动需 dev 知晓。
**测试**：dispatch 改 package.json → assert commit 前 `npm install` 跑过 + lock 在 commit 内；不改则不触发。

### 方案 3〔闭环层〕PR 生命周期治理（放行仍归 pa-verify）

**切入根因**：dev 自开 PR 在 verify 前（`dev-agent.py:189/974`）+ PR 链 base 非 main（增量重投副作用）+ 红 PR 不清理（`reconcile_pr` 无 `gh pr close`）。

**机制**：~~原设计提「verify 双绿门（install_rc≠0 或 test_rc≠0 编排器硬阻断不放行）」~~——这又是编排器替 pa-verify 判，**越界，删除**。**是否放行一律归 pa-verify**（它拿到方案 1 补的 install_rc/install_log 输入后自行判）。本方案只做**纯机械活**（PR 状态机操作），触发条件依赖 pa-verify 的判决：

| 改动点 | 改动（均为机械动作，触发依赖 pa-verify 判决） |
|---|---|
| PR 开启权 | dev-agent 不自开 PR（编排器模式下禁用 `dev-agent.py:189/974` 的 gh pr create）；PR 由编排器在 **pa-verify 判 pass 后**统一开，**base=main**（消除 PR 链）。开 PR 是机械动作，触发条件 = pa-verify 的 pass 判决 |
| reconcile_pr | **新增 `gh pr close` 能力**——pa-verify 终态判 revise 且 `VERIFY_MAX_ROUNDS` 用满 → 编排器关该 slug 旧红 PR（机械动作，触发 = pa-verify 的 revise 终态判决；保留最新供 review 或全关标 wontfix） |

**通用性**：所有目标仓（PR 生命周期是通用机械状态机，与 pa-verify 判断正交）。
**trade-off**：✅ 根治 PR 链/双重 PR/红停滞 ✅ 守「放行归 pa-verify」（删了越界的双绿门） ⚠️ 改动面较大（ADR-0008 §PR-path + reconcile_pr 新增关 PR + dev-agent 禁自开 PR） ⚠️ 实现前需查清 PR 实际开启路径（dev-agent 自开 vs 编排器代办——run log 有矛盾信号）。
**测试**：reconcile_pr 在 pa-verify 终态 revise + 用满 → assert `gh pr close` 调用；dev-agent 自开 PR 被禁 → assert 编排器模式下无 dev-agent gh pr create；PR base=main（无链）。

---

## 6. 推荐与递进路径

| | 方案1 输入 | 方案2 源头 | 方案3 闭环 |
|---|---|---|---|
| 改动面 | 小 | 中 | 中-大 |
| 抓根因 | 直接（补输入，修 8/5 失效） | 源头杜绝 | PR 生命周期根治 |
| 守架构 | ✅（补输入，判归 persona） | ✅（源头，不涉判断） | ✅（删双绿门，放行归 persona） |
| 风险 | 低 | 中 | 中 |
| 依赖 | 无 | 无 | 依赖方案1（补的 install 输入） |

**推荐**：**方案 1 起步**（最小、直接补输入修 8/5 失效、守架构原则、风险低）。三方案可递进：**1（输入）→ 2（源头）→ 3（闭环）**，每步独立可验证、可回退。方案 1 是方案 3 的前置——方案 3 关红 PR 的触发依赖 pa-verify 的 revise 判决，而 pa-verify 要能判对，前提是方案 1 先把 install 输入补全。

---

## 7. 通用性声明

本诊断与三方案均为**控制面框架层**，对所有目标仓通用，不绑定任何特定项目（遵 ADR-0001 控制面/目标面隔离、ADR-0006 dev-agent 通用执行器）。cc-web-control 在本文仅作为触发案例出现。

## 8. 相关文档

- 设计依据：`docs/verify-commit-loop-design.md`（verify 闭环 §5-② 契约）
- SPEC：`SPEC.md` §4.4（独立验证闸）
- 术语：`CONTEXT.md`（控制面/目标面/verify 闸/dev agent/persona）
