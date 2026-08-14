# verify-loop-discipline — tasks

> 根因诊断见 `proposal.md`（A 结构 + B 职责双根因），取舍见 `design.md`（D1-D4）。OQ1/OQ2 评审定。

## A. PR 跟需求（结构）

- [x] A1 dev-agent branch 去 stamp（**2026-08-14 实现绿**）：`make_branch(prefix, slug)` 抽至 slug_utils.py（无依赖可 import，绕开 dev-agent.py 连字符不可 import 约束）；dev-agent.py L796 改用 `make_branch`（去 `{s}-` stamp）；stamp `s` 保留给 run_log(L809)/coordinator(L817)（R1-A-P2-2 兄弟消费点）。C1 测试 `test_verify_loop_branch.py` 3 项 GREEN（slug-only / 确定性 / 区分性回归）+ ruff 过。
- [x] A1a dev-agent checkout 适配（**r1 P1-1 阻塞**，2026-08-14 实现绿）：抽 `_ensure_feature_branch(branch, base)`（show-ref 判断存在 → `checkout` 复用 / 不存在 → `checkout -b`；show-ref 直走 subprocess，rc=1 是预期非错误不经 git()）+ `--phase checkout` 早期分叉（机械 phase，subprocess 可离线测）；main L799 段改调同一函数（零漂移）。C1a 2 测 GREEN（round1 新建 / round2 复用不崩）。
- [x] A2 dev-agent 开 PR 幂等（2026-08-14 实现绿）：抽 `run_publish_phase`（`--phase publish` 早期分叉）——`gh pr list --head branch --state open` 查重（镜像 `_pr_automerge` L184-190 幂等模板，经 `gh()` fail-loud）：已有 open PR → 只 push（GitHub 同 head 自动并入，输出 `pr_reused: true`）/ 无才 create；main L962 段改调同一函数（dev_out/on_success/on_no_changes 透传 dev-loop 摘要字段，输出语义不变）。C2 2 测 GREEN（round1 create / round2 复用同 PR + fake-gh state 恒 1 PR + origin 同分支累积 2 commits）。
- [x] A3 worktree 改名跟 slug（2026-08-14 实现绿）：run_daily dispatch_one worktree 路径 `f"{stamp}-{slug}"` → `devslug`（`dev_slugify` 产物——原始 stem 含下划线与分支名漂移；A1 branch=`auto/<devslug>` 一致）。同 PRD 各轮 / 崩溃恢复重入确定性清理（同 slug → 同路径 remove-if-exists 自清；跨 cron 新 PRD 新 slug，防重靠 already_dispatched——R3-A F7 收敛边界）。⚠️ 并发注记（R3-B F4）：**同 slug 并发 dispatch 靠 slot 互斥（DISPATCH_LOCKS + single-flight），不靠 worktree 路径隔离**——A3 去 stamp 后同 slug 并发会撞同一路径。测试：worktree 路径 == `.worktrees/<devslug>` 且无 stamp 时分模式。
- [x] A4 增量重投改同分支累积（2026-08-14 实现绿）：删 round revise 后 `cur_base = branch`（cur_base 恒项目默认分支 → 下轮 `--base=默认分支` + dev-agent A1a checkout 复用同分支 → commits 累积进同一 PR）；round 边界 commit marker——`_dev_cmd` 加 `round_n` 参数 → `--round N` → dev-agent commit 第三段 `-m "pa-dev: round N"`（`git log --grep` 可定位轮边界，供 commit 级回滚；0=不标零噪声；区别于 `--iteration-seq` 的 session 副作用）。测试：r2 base 恒 main + `--round` 逐轮透传 + diff 全量 + marker 落 commit message。
- [x] A4a verify_prompt + diff 语义同步（r2 R2-A-5，2026-08-14 实现绿）：① prompt 文本 `（round≥2 同分支累积：base=项目默认分支，diff 含全部累积 commits，按 commit marker「pa-dev: round N」定位本轮增量）`；② docstring 同步；③ `_dump_branch_diff` 调用处注释固化「diff=默认分支..branch 全量累积，按 marker 定位本轮」（cur_base 恒默认分支自动达成）；round2 提示行同步「同分支上累积重做」。既有测试断言翻转：`test_verify_prompt_red_round2`（增量→同分支累积）、`test_dispatch_red_then_green`（bases `["main","auto/r1"]`→`["main","main"]`）、`test_dispatch_driven_retry_injects_feedback_artifact`（r2 识别 base≠main→round_n==2）。
- [x] A5 reconcile_pr 适配（2026-08-14 实现绿）：兜底开 PR 撞并发已开的同 branch open PR（TOCTOU：查后无 → 他方开 → create rc≠0）→ create 失败重查（`_lookup_pr`），FOUND-OPEN 复用 `pr_url` 收尾（对齐 A2 幂等语义）；仍无才 `blocked_external_state`（原 fail-safe 不变）。测试 `test_reconcile_create_race_reuses_open_pr`（两次查询分流 + create rc=1 → 复用）。

## B. pa-verify 做语义审查（职责）

- [x] B1 pa-verify persona 重写（2026-08-14 实现绿）：`.claude/agents/pa-verify.md` 全文重写——description + 正文「发布前三方独立审核（与项目自审核并行不替代，ADR-0002 边界澄清）」定位；「你做什么」整节改三维度综合审查（verdict 由 persona 合成 / 编排器不替判 / 绿≠自动 pass / 绿轻红重对齐 Bundle-scoped / 非测试缺陷锚验收点 id 或 diff 位置与测试锚正交）；输入列表加第 6 项质量维度集；硬约束「默认怀疑但不苛刻」；禁区加「不重跑测试」。输出 JSON 契约不变。
- [x] B2 independent_verify 基建三分（2026-08-14 实现绿）：dispatch 消费门——`install_rc ≠ 0`（三态 UNKNOWN）→ `status=blocked_infra` 升人工 + reconcile 保留分支 + break，**不进 revise loop 不耗 verify round**；`test_rc ≠ 0` 才喂 pa-verify。实现注记：reconcile 后**重设**终态（reconcile 只管 PR 对账副作用；终态归门——否则对账 status 覆盖 blocked_infra 语义）。测试 `test_dispatch_install_fail_blocks_infra_not_revise`（persona_calls==[]）。
- [x] B2a Python 仓 install 闸（2026-08-14 实现绿）：independent_verify Python 路径加 install 步骤——profile `install_cmd` 优先，缺省探测 worktree 内 `pyproject.toml`/`setup.py` → `pip install -e '.[dev]'`（`_env_python` + venv PATH 注入）；`install_rc≠0` → `note="Python install 失败（基建，非代码红）"` 提前 return（B2 门接手）；无 install_cmd 且无打包文件 → `install_rc=0`。测试 `test_independent_verify_python_install_gate`（真 git tmp repo + 预建 vt 骨架 + pip install rc=1 → install_rc==1 + test_rc is None）。
- [x] B3 verify_prompt 重构（2026-08-14 实现绿）：**删 verdict 机械映射两行**（`测试绿 → verdict=pass` / `测试红 → verdict=revise`）；test_state 三分支渲染保留。prompt 加：`_extract_acceptance_ids`（PRD「验收」节列表项 → 稳定 id `<devslug>:acN`，跨轮稳定，不可读/无节 fail-open 空表）注入 ac_section（显式标注「非测试缺陷锚此 id」）+ 三维度审查指令（功能对齐/开发遗漏/质量达标（{dims}）+「三维度全过 → pass；任一维度有阻塞性缺陷 → revise」+ 两类锚点正交规则）。测试 `test_verify_prompt_no_mechanical_verdict_mapping` + `test_extract_acceptance_ids_stable`。
- [x] B4 项目类型裁剪（2026-08-14 实现绿）：`_review_dimensions(prof)`——profile `review_profile` 显式优先（frontend 含 UX 可用性/响应式布局；backend 仅安全/性能/兼容性）；缺省探测 `prof["repo"]` package.json deps 含 UI 框架关键词（react/vue/next/svelte/angular/antd/vite/tailwind）；异常/无 repo → backend。测试 `test_verify_prompt_dimensions_pruned_per_profile`（frontend 含 UX/响应式、backend 无）。
- [x] B5 graph_pa 对应 node 同步（2026-08-14 实现绿）：自动继承（直调 run_daily）——`verify_prompt`/`independent_verify`（B2a install 闸 + B3/B4 prompt）+ `reconcile_pr`（A5）零改动继承；手动同步 `_dev_post_op` 两门——①b round≥2 `no_changes` → reconcile 对账 + 重设 `terminal=triaged/_exit_status=no_progress`（R3-A F2 graph 侧）；③b `install_rc≠0` → reconcile + 重设 `terminal=blocked/_exit_status=blocked_infra`（B2 graph 侧），均不喂 verify。测试 test_graph_dev_post 3 新测（round2 no_changes / round1 负向 / install fail）。

## C. 测试（TDD，先红后绿）

- [x] C1 同 slug verify revise 复用同分支（round2 branch string == round1，不新建）——`test_verify_loop_branch.py` make_branch 3 测（slug-only / 跨调用确定性 / slug 区分性回归）
- [x] C2 dev-agent 开 PR 幂等（mock 已有 PR → 只 push 不 create）——`test_verify_loop_branch.py` fake-gh + 真实 git tmp repo，subprocess 跑 `--phase checkout`/`--phase publish`（round1 create / round2 复用同 PR + state 恒 1 PR + 同分支累积 2 commits + round marker + 缺参 fail-fast，phase 测 6 项；另 run_daily 侧同分支累积 / no-progress / prompt 语义 4 测）
- [x] C3 independent_verify install 失败 → `blocked_infra` 升人工，不进 revise（不消耗 verify round）——`test_dispatch_install_fail_blocks_infra_not_revise`（persona_calls==[]）+ graph 侧 `test_dev_post_install_fail_blocked_infra`
- [x] C4 pa-verify 测试绿但语义缺陷 → revise——B1 persona 三维度指令 + B3 删机械映射共同保证（`test_verify_prompt_no_mechanical_verdict_mapping` 断言无「测试绿 → pass」映射 + 三维度指令在）；真 LLM 判定属 D1 canary 验证范围
- [x] C5 回归：合法 test 红 → revise——既有 `test_dispatch_red_then_green` / `test_verify_prompt_red_round2`（Line-anchored 反馈锚点不动）+ `test_dispatch_green_revise_enters_loop`（绿 revise 回环 round2 正常）
- [x] C6 回归：install ok + test 绿 + 无语义缺陷 → pass——`test_dispatch_all_green_single_pass`（单轮 rounds==[1]，normal path 不回归）
- [x] C7 `quality.sh` 全绿（2026-08-14：1905 passed + 5 xfailed + ruff E9/F 过）

## D. canary + 发布门（须真实 cron 环境，待用户指示）

- [ ] D1 verdict 分布 canary：改造前后 pass / revise 比率对比（revise 率暴涨 → persona 调参，design D3）——**依赖 ≥1 个真实 verify 轮次跑新三维度 persona**
- [ ] D2 同分支累积 canary：真实 cc-web-control 跑 verify revise 回环，断言单 PR（隔离 state + 临时 log + unset PA_HEARTBEAT，守 pa-test-no-dirty-data）——**outward（开真实 PR），须用户确认后执行**

## E. 运维（outward，须用户确认后执行）

- [x] E1 #55 #56 处理（2026-08-14 用户确认后执行）：关 #56（round2 重复，`auto/20260805-0334-*` 分支保留未删），保留 #55 为该需求唯一在途 PR，在途上限占位 2→1（复核：open PR 仅剩 #55）。
- [x] E2 git commit change 文档（proposal + design + specs + tasks + 实现全集）

## OQ（评审拍板）

- **OQ1** 三维度全量每轮审，还是绿轻红重（test 绿 quick sanity / test 红全量）？倾向后者，与现有 green-path / revise-path 对称。
- **OQ2** PR 开启收归控制面 reconcile（design D4 选项 b）是否作 follow-up？本期选 A2 最小治本。

## R3 实现后评审处置（2026-08-14，三专家 R3-A 结构 / R3-B 契约 / R3-C spec）

- **P0（R3-A F1 + R3-B F1 同一）graph_pa 未同步** ✅ 已修：`graph_pa_verify._redo_fn` 删 `_cur_base=branch`（同分支累积）+ `graph_pa_nodes._dev_build_cmd` 两分支传 `round_n=state.get("verify_round",1)` + `_worktree_op` 改 `dev_slugify(slug)`；test_graph_verify/test_graph_worktree 旧语义断言 intended 翻转（`_cur_base` not in result / wt 路径无 stamp）。
- **P1 R3-A F2 no-progress 检测失灵** ✅ 已修（TDD）：dispatch_one branch 检查后加门——round≥2 且 `script_json.no_changes` → `status=no_progress` + reconcile + break（同分支累积后 has_commits 恒 True，不加则白跑一轮 verify 耗尽 rounds 退化 interrupted_pr）；测试 `test_dispatch_round2_no_changes_terminates_no_progress`（iv_calls==1）。
- **P1 R3-A F3 persona 指令矛盾** ✅ 已修：`.claude/agents/pa-verify.md` L16-19（base 恒默认分支 / diff 全量累积按 marker 定位）；`docs/verify-commit-loop-design.md` L38 加演进注记（历史文档不改写原决策）。
- **P2 批** ✅ 已修：dev-agent publish 加 `on_error` 对称透传（main 传 `test_passed=last_test=="green"`）；checkout/publish 缺 branch/base 参数 fail-fast exit 10（防建 "None" 分支）；注释残留清理（graph_pa_verify 模块/字段 docstring、run_daily 段头、slug_utils 收敛边界注）；mock 改同 branch（test_verify_loop 4 处 + branch 1 处）；RUNBOOK 加旧 stamp worktree 清理 + show-ref 本地盲区条目。
- **R3-C 结论**：A 段完成可进 B 段；E1 前提成立（单 PR 语义闭环，旧 stamp 分支被 already_dispatched 拦截不开第三 PR）。
