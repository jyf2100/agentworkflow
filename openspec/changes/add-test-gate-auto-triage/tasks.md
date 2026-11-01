## 1. 实现

- [x] 1.1 `evidence.py` 增 `auto_reverify(evidence, *, runner)`：重跑同命令取新证据（fresh=True 按构造）；绿 → (新证据, record{outcome:green})；红 → (None, record{outcome:red})；runner 异常/超时由调用侧转 outcome=timeout。纯函数 + 注入 runner（无 subprocess 依赖）
- [x] 1.2 `dev-agent.py` 发布门 4.5 段：verdict==test_stale 且 flag 开 → auto_reverify（subprocess runner，shell=True，cwd=REPO_ROOT，timeout=PA_TEST_GATE_TRIAGE_TIMEOUT 默认 600）→ 新证据非 None 则替换 state["test_evidence"] 并复评（publish 则照常走发布，on_success/on_error 增 auto_triage 字段）；仍阻断则 exit 14 JSON 增 auto_triage
- [x] 1.3 `feature_flags.py`：`test_gate_auto_triage` → `PA_TEST_GATE_AUTO_TRIAGE`（域切割注释，默认 off）

## 2. 测试

- [x] 2.1 `test_evidence.py` 增量：auto_reverify 绿 → 新证据 fresh=True 且 evaluate_gate=publish；红 → None+record；注入 runner 断言命令原文传递；scope 判据（仅 stale 分支由 dev-agent 侧 flag+verdict 守卫，单测以纯函数面覆盖）
- [x] 2.2 `test_feature_flags.py` 增量：新 flag 默认 off / truthy 开
- [x] 2.3 `cd Projects/项目推进流水线 && python -m pytest scripts -q` 相关文件全绿；`bash scripts/quality.sh` 对照基线（既有噪声不新增失败）

## 3. 验证

- [x] 3.1 本机 dry：构造 stale 证据 + fake runner 全分支（绿放行/红阻断/超时记录）
- [ ] 3.2 真机：下次 test_stale 阻断的 run（flag 开）观察 auto_triage 记录与放行/阻断正确性——列观测非 gate

## 实施证据（2026-11-01）

- 1.1：evidence.auto_reverify：runner 注入（(exit_code, stamp) 契约）→ 绿=新证据(fresh 构造成立)+record{green} / 红=None+{red} / None=+{timeout}；命令原文传递
- 1.2：dev-agent 4.5 段：GATE_STALE + flag 守卫 → subprocess(shell=True, cwd=REPO_ROOT, TRIAGE_TIMEOUT_S 默认 600 可 PA_TEST_GATE_TRIAGE_TIMEOUT 覆盖) → 新证据替换 state 复评放行；阻断与 on_success 双路径 JSON 增 auto_triage
- 1.3：feature_flags：LoopFlags 字段 + FLAGS_ENV_MAP（PA_TEST_GATE_AUTO_TRIAGE，gate 域切割注释）+ 镜像 map 测试同步
- 2.1：test_evidence 增 2 测试：绿→fresh+publish / 红+超时→None+record；命令原文断言
- 2.2：test_feature_flags 增 1 测试：默认 off / 1 / true 开
- 2.3：test_evidence+test_feature_flags 40 测试全绿；bash scripts/quality.sh exit 0 全绿（2026-11-01）
- 3.1：单测即 fake 全分支（注入 runner 绿/红/超时）
- 3.2：真机观测点——hub 已启用 PA_TEST_GATE_AUTO_TRIAGE=1（hub.env PA_* 透传链 + up -d 重建），下次 test_stale 阻断 run 看 auto_triage 记录与放行正确性（不 gate）
