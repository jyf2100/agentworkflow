# tasks — in-loop-semantic-checkpoint

TDD 顺序：每节先写测试（RED）→ 最小实现（GREEN）→ ruff 干净。

> **实现注记（2026-08-09）**：原计划假定可 `import dev_agent` 直测内循环，但 `dev-agent.py` 带连字符**不可 import**（既定约束）。故所有可测逻辑（helpers + checkpoint 状态机 + decide_after_leg）提取到零依赖 `scripts/semantic_gate.py`，与 `bash_allowlist` / `evidence` / `persona_call` 同构；`dev-agent.py` 退化为薄接线层（import semantic_gate + 控制流）。T1-T8/T10 改为测 `semantic_gate` 纯函数（`test_semantic_gate.py`，19 测）；`dev-agent.py` 接线（process_dev_loop checkpoint 分支 / main resume while / exit-15）靠 `compileall` + 静态 review + 推迟的 canary（验证方法 #5）。
>
> 另：静态 review 发现并修复 main while 一个时序 bug——`redirect_pending` 原在 `decide_after_leg` 返回后即清空，但第二段 `build_redirect_prompt` 需读它；改为 build 后用完即清（dev-agent.py）。

## 1. 共享零依赖 persona_call.py（守 SDK-隔离 invariant 的基石）

- [x] T9 `test_persona_call.py`：两层 JSON 解析（outer 信封 + inner payload）+ 容错 `_extract_first_json`（散文前后缀）+ 重试 cap=2（首轮非 JSON 加强 JSON-only 重试）+ 超时 raise RuntimeError + 非零退出 raise + is_error raise（纯函数测，monkeypatch `subprocess.run`，无 SDK）
- [x] T9 实现 `scripts/persona_call.py`：`run_persona_subproc(claude_bin, agent_name, prompt, *, max_turns, timeout, stage=None, allowed_tools=None, retry_cap=2) -> tuple[dict, dict]`，复制 `_extract_first_json` / `_JSON_RETRY_SUFFIX`（不 import run_daily），接 stage_contracts 契约校验（fail-open warning）
- [x] T11 `test_persona_call_does_not_load_sdk`：subprocess `import persona_call` 后 `sys.modules` 无 `claude_agent_sdk`（守 `test_dev_agent_source.py` 反 invariant 扩展）
- [x] `ruff check scripts/persona_call.py scripts/test_persona_call.py` 干净（仅 E9+F）

## 2. pa-progress persona（新评判器，独立于实现 Agent）

- [x] `.claude/agents/pa-progress.md`：对齐 pa-verify.md 格式；输入 PRD 全文 + diff 摘要；判据 on_track / off_track（不看绿、不要测试产物）；输出 `{"verdict","covered":[],"off_topic":[],"redirect_hint","summary"}`；tools: Read；frontmatter description 说明 dev 内循环方向抽查
- [x] `stage_contracts.py` 注册 `CONTRACTS["progress"]`（ProgressContract：verdict 必填 ∈{on_track,off_track}）+ 单测覆盖（并入 test_persona_call）

## 3. dev-agent helpers → 提取到零依赖 semantic_gate.py（diff 摘取 + prompt 构造 + fail-open 评判）

- [x] T1 `judge_direction` fail-open：monkeypatch `run_persona_subproc` raise → 返回 None，不抛（test_semantic_gate.py）
- [x] T2 `truncate_diff` / `collect_diff`（git_fn 注入）截断 + git 失败安全占位（test_semantic_gate.py）
- [x] T3 `build_progress_prompt(prd_text, diff_bundle)` 含 PRD 全文 + diff + JSON 输出契约说明（test_semantic_gate.py）
- [x] 实现 helpers 在 semantic_gate.py（`collect_diff` 调注入 git_fn + `run_persona_subproc(stage="progress")`）；`build_redirect_prompt(base_prompt, redirect_hint)` 续做提示

## 4. checkpoint 状态机 → semantic_gate.run_checkpoint（纯函数测，无需 process_dev_loop mock）

- [x] T4 on_track 重置 off_track_count、action=continue：评判 stub 返回 on_track → state.off_track_count 归零
- [x] T5 首次 off_track：设 redirect_pending、action=redirect：评判 stub 返回 off_track（off_track_count 0→1）→ state.redirect_pending=redirect_hint
- [x] T6 二次 off_track：设 off_track_exhausted、action=exhausted：off_track_count 已 1 → 评判 off_track → state.off_track_exhausted=True
- [x] T10 成本熔断 skip：judge_cost_acc ≥ JUDGE_BUDGET_CAP → 不调评判、action=none（monkeypatch 计数）
- [x] `create_loop_state` 加 9 字段：judge_k/judge_rounds/off_track_count/last_verdict/last_covered/redirect_pending/off_track_exhausted/judge_cost_acc/last_session_id（dev-agent.py）
- [x] `process_dev_loop` 加 `prd_text` 参；插入点（append_run_line 后 / stall 刹车前）调 `semantic_gate.run_checkpoint`（dev-agent.py 接线，compile + 静态验证）
- [x] ~~message-stream mock helper（`_msgs`/`_ast`）~~ — **跳过**：process_dev_loop 不可 import，checkpoint 状态机改测纯函数 run_checkpoint（无需 mock 消息流）

## 5. main() resume 重发循环 + exit-15

- [x] T7 off_track_exhausted emit exit-15 JSON：main 末段 off_track_exhausted → stdout `{"ok":false,"off_track":true,"exit_code":15,...}` + return 15（dev-agent.py 接线，compile + 静态）
- [x] T8 首次 off_track 用 redirect 重发：**decide_after_leg 纯函数覆盖**（resume_redirect=True + next_redirects_done=1）；main while 接线 compile + 静态
- [x] main() dev 段改 while 循环（redirects_done 计数 + 每段经 `_build_options` 工厂新建 ClaudeAgentOptions，不依赖 options 可变性）；docstring 加 `15=semantic off_track`
- [x] **H1 review 修正（接受新 session 现实）**：原设计「resume=last_session_id 续做」经三方（python-reviewer / architect / silent-failure-hunter）核验为伪——break 在 AssistantMessage 分支拿不到流末 session_id → redirect leg 实为**开新 session**（非 resume），靠工作树 diff 自恢复（仍 off_track → 2nd strike exit 15，不无限循环）。见 design.md D1。`last_session_id` 字段已删（消除 dead state）

## 6. run_daily.py 对接 off_track 消费

- [x] `TRIAGE_REASONS` 加 `"semantic_off_track"`（frozenset 扩位）
- [x] dispatch 消费 dev-agent stdout：读 `off_track:true` → `rec["off_track"]=True` + 提前退出（reconcile_pr interrupted + emit + return）
- [x] reconcile_pr 分支：off_track → `status="triaged"` + `triage_reason="semantic_off_track"`（类比 stalled→stalled，pre-merge triage 出口，不阻塞队列）

## 7. 全绿 + 离线 drill

- [x] `python -m pytest scripts -q` 全绿（T1-T11 等价覆盖 + 既有套件）
- [x] `ruff check scripts` 干净（仅 E9+F）
- [x] `bash scripts/quality.sh` 全绿（compileall + pytest + ruff）→ 1488 passed, 5 xfailed
- [x] `test_dev_agent_source.py` 反 invariant 不破（run_daily 不连带加载 SDK）
- [x] 离线 drill：semantic_gate 纯函数覆盖完整两阶段状态机（on_track→continue / 1st off_track→redirect / 2nd off_track→exhausted / 评判 fail-open / 熔断）；**dev-agent.py 接线 drill 推迟到 canary**（验证方法 #5：dry-run 观察 run_log judge 字段，守 pa-test-no-dirty-data）
