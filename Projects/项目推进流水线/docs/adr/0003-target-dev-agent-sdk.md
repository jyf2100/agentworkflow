# 0003 — 目标面 dev agent = 仓自带 SDK 脚本；控制面留 CLI；刹车由平台兜底

> **2026-07-16 修订（见 ADR-0005）：** 控制面最终为 3 个 CLI persona（radar / prd / critic）+ 2 个机械 stage（dispatch / report）。本 ADR 的目标面 SDK 决策不变；下文「控制面 5 persona」是当时的原始决策，已由 ADR-0005 部分取代。

## 决定

1. **混合拓扑**：控制面 5 persona（radar/prd/critic/dispatch/report）= CLI markdown（`claude -p --agent`，复用 [[project-workbench-agents]] 的 wkw/wkr/wkp 模式）；**目标面 dev agent = 仓自带 SDK 脚本** `<仓>/scripts/dev-agent.*`，用 `claude-agent-sdk` 的 `query()` 跑完整 dev loop。两 plane 各用原生机制，隔离不变（ADR-0001）。
2. **dev agent 归属（更新决策 #14）**：每仓自带 `scripts/dev-agent.*`（语言跟仓栈；cc-web-control = `dev-agent.mjs`）+ CLAUDE.md（dev persona + 自治 scope/质量/review）。仓自己 owning、随仓走——ADR-0001 原则不变，仅产物从 `.claude/agents/dev.md` 换成 SDK 脚本（仍属仓自己的开发结构，不算污染）。
3. **permissionMode = `acceptEdits`**：+ 定向 `allowedTools`（`Bash(git push *)`/`Bash(gh pr create *)`/`Bash(npm test)`/Read/Edit/Write/Grep/Glob）+ PreToolUse hook（拦 never-merge / 碰主干 / `rm -rf` / 读密钥）+ worktree。dev 机非容器，`bypassPermissions` 不符。
4. **刹车强制（方案 b）**：不可逆刹车（never-merge / never-touch-main / force-push）由 **GitHub branch protection 平台兜底**（准入时配）；可挂死的由 dispatch wall-clock；PR 质量由 dispatch 独立验证（决策 #18）；**budget/turns/危险命令 hook 信仓**（项目自治，ADR-0002 + R2 缓做成本）。
5. **dispatch↔dev-agent 契约**：dispatch 建 worktree（`auto/<YYYYMMDD>-<slug>`）→ `cd <worktree> && node <仓>/scripts/dev-agent.* --prd <abs> --source <abs>`（只读、不落盘进仓）+ wall-clock；脚本自 push + `gh pr create` + 输出 stdout JSON `{pr_url, branch, success, self_test_pass, files_modified, cost_usd, turns, session_id, summary}`；dispatch 独立 `npm test` 比对，不一致/红则标红（failing PR 留作 GitHub PR）。

## 背景

用户读完 Agent SDK 深度分析（`产品分析/Claude-Code-Agent-SDK/`，2026-07-15，已验证准确）后明确："**项目仓走这个流程**"——把 SDK 的重机器（budget 帽 / 权限 / hooks / dev-loop）部署到「自治改代码」这个最高风险段；控制面低风险的信号/PRD 活儿继续用轻量 CLI markdown persona（跟既有 wkw/wkr/wkp 一致）。SDK 已由用户验证可用。

## 考虑过的替代

- **dev agent 定义归控制面（B）**：dispatch 在 vault Python 定义 `AgentDefinition(dev)` 直接 `query(cwd=<repo>)`。拒绝：dev agent 定义落控制面，违背 ADR-0001「dev agent 归目标仓、随仓走」。
- **markdown + SDK 混载（C）**：仓仍放 `.claude/agents/dev.md`、用 SDK 加载。拒绝：文档 §2.2 把文件系统式 persona 归 CLI、编程式 AgentDefinition 归 SDK，能否混载未核实；不如直接让仓 owning 可执行脚本清晰。
- **permissionMode = `bypassPermissions`**：拒绝：文档 §1.4 明确仅限 CI/容器/隔离环境，dev 机 + 真 remote 不符；安全全压 hook。
- **刹车强制 = 包络式（c）**：dispatch 用自己的 SDK envelope 注入 hook+budget。拒绝：最硬但部分收回「仓 owning 脚本」(A)，且 dispatch 要维护 per-repo envelope；平台 branch protection 已足够兜不可逆风险。
- **刹车强制 = 审计式（a）**：dispatch grep 审脚本含刹车。拒绝：repo 可糊弄审计；不如平台 branch protection 机械可靠。
- **全 SDK（控制面也 SDK）**：拒绝：控制面是固定顺序 DAG（非 subagent 扇出），SDK 形态只能退化成"进程内 CLI"，收益有限；两 plane 本就隔离，没必要强求统一。
