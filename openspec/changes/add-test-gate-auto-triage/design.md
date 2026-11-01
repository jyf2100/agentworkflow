## Context

发布门 4.5 段（dev-agent.py）：`evaluate_gate(state["test_evidence"])` 非 publish → exit 14 阻断。test_stale = 证据 exit_code==0 且 fresh==False（绿后又有候选写，evidence.py 写事件标记模型）。#110/#111 的 gate_reason 均为「绿色测试后又发生候选写，证据已不反映当前待提交内容」。

## Decisions

### D1 重验即「新的绿色测试」，门语义零放松
spec「Changes occur after the green test」场景原文：publication remains blocked **until a new green test completes**——编排器侧重跑同一条命令正是「一次新的绿色测试完成」，且采集时点晚于全部候选写（dev loop 已终止，重验期间零写），fresh=True 按构造成立而非豁免。放行路径与 dev 自己再跑一遍测试完全同权。

### D2 纯逻辑归 evidence.py（无依赖模块惯例），subprocess 留 dev-agent
`auto_reverify(evidence, *, runner)` 纯函数 + 注入 runner（返回 (exit_code, stamp)）——单测零 SDK 零 subprocess 锁定分支；dev-agent 侧 runner 用 subprocess(shell=True, cwd=REPO_ROOT) 承接（命令原文来自 is_clean_test_cmd 过滤后的 dev 自选命令）。红/超时 → 返回 (None, record) 走原阻断，record.outcome ∈ green/red/timeout。

### D3 flag 与超时键
`test_gate_auto_triage` → `PA_TEST_GATE_AUTO_TRIAGE`（feature_flags 域切割，gate 能力域前缀，默认 off——渐进启用惯例）；`PA_TEST_GATE_TRIAGE_TIMEOUT` 秒（默认 600，仅 dev-agent 读取，不走 feature_flags）。

### D4 记录面
`auto_triage` 增量字段进最终 JSON 两条路径（on_success 与 exit 14）；dispatch 侧读者不识别该键（增量零破坏），人审经 run 日志可见。stale 重验成功后 on_success 的 `test_cmd`/`evidence_fresh` 自动反映新证据。

## Risks / Trade-offs

- [重验期间环境漂移（依赖锁文件等）导致绿变红] → 按 red 阻断走人工，与现状等价（现状 = 无条件人工）；record 留痕供判读。
- [重验命令有副作用（如 e2e 起服务）] → 命令是 dev 自己选的干净测试命令，dev loop 内已跑过至少一次；重验同权不新增风险面。超时兜底 600s。
- [shell=True 注入面] → 命令原文来自 dev 会话内被识别的测试命令（非外部输入），且 dev-agent 本就在同环境执行它；无新增信任边界。

## Migration Plan

默认关（flag off = byte-identical）；启用 = 容器 env 透传链 `PA_TEST_GATE_AUTO_TRIAGE=1`（hub.env PA_* 面）。回滚 = 关 flag。
