# Design — agent-verify-injection

## Context

`independent_verify`（run_daily.py:3392）现双轨纯机械：Node → `npm ci + npm test`；非 Node → 重放 dev 自报 `test_cmd`（没报跳过）。两个根因问题：测试面同盲（dev 自选面被两层共享；验收测试 dev 自写自证）+ 纯机械执行在 cron 极简 PATH 下对陌生工具链环境脆弱。用户裁决（2026-10-26，两项）：① 执行面不能纯机械，至少走 claude code / codex 之一，**全轨统一**；② agent 必须是**纯执行器**——环境/需求/测试用例三输入全部外部注入，agent 不自选、不自编。判定权（绿/红）留机械，这是与既有不变式（`verified-dev-execution` 测试门、shadow parity 确定性）的和解点。

## Goals / Non-Goals

**Goals**
- 验收测试成为控制面工件：PRD 验收点（稳定 id）→ persona 外部编写 → content-addressed 存储 → 双注入（dev 目标 / verify 门）→ digest 对账。
- verify 执行面 agent 化（全轨统一）+ 三机械锚（transcript 退出码 / tracked 不变 / 注入件 digest），flag `PA_VERIFY_AGENT` 默认 off。
- 吸收 harden-verify-test-scope 的 `verify_test_cmd` 优先序（该题 0/9 未施，delta 归并）。

**Non-Goals**
- 不改 pa-verify 三维度审查（输入面换成注入测试结果 + 全量门结果）。
- 不改 dev 侧 SDK dev loop 结构（只加注入参数面）。
- 服务级环境依赖（DB/redis 起容器）v1 不做——env spec 只到工具链级。
- codex 第二执行器 v1 不做——执行器接口留 Protocol 形态可后补。
- harness 声明面 v1 收窄到行锚解析器支持集（jest/pytest）；其余 harness 显式接受红反馈降级 `unresolved`（测试学二审 T5），解析器扩面后再放宽声明面。
- single-flight auto-merge 的 post-merge verify 面（`_post_merge_test_cmd`）不走 agent 轨——保持纯机械重放，agent 轨只覆盖 dispatch 阶段 independent verify（同能力双面的歧义显式消除，T5）。

## Decisions

### D1 执行/判定分离（agent 执行，机械判定）
备选：全机械（用户已否决）/ 全 agent 判定（agent 自述绿即绿——破坏测试门不可伪造性）。定案：agent 只做「让注入的测试跑起来」的执行智能（装环境、诊断基建），绿红证据三锚机械提取，agent 自述仅观测不进判据。类比：agent 是 lab technician，编排器是签字的检验师。

### D2 三输入真源与形态
- **环境** = profile env spec：既有 `conda_env` / `install_cmd` 之上加可选 `env` 块（工具链声明，v1 仅工具链级）。项目 onboard 自声明（ADR-0002 自治面）。
- **需求** = PRD 验收标准稳定 id（既有真源，pa-verify 已消费）——注入给 test-author 做编写依据、注入给 verify agent 做参照，不新造实体。
- **测试** = 新工件：`scripts/acceptance_tests.py` 编排（critic pass 后调 pa-test-author persona → 产物存 artifact_store → journal 事件 `acceptance_tests`{digest, path, criteria_ids}）。digest 门复用 handoff/artifact_store 既有 content-addressed 模式。**覆盖账双向闭合（测试学二审 T1）**：criteria_ids 必须 ⊇ PRD 全部验收点 id，缺者显式记 uncovered 不静默。**编写时自检（T2）**：harness 支持收集级校验的（pytest `--collect-only` 类）入注入面前先跑，破损套件扣留记缺席（fail-open 同编写失败面）；verify 门上发现 oracle 自身缺陷（收集级失败签名 + triage 确认 `acceptance_suite_defective`）→ 重编写为新 digest 版本（journal 记 supersedes 版本链）→ 重跑 verify，不烧 dev revise 预算。persona 编写契约含**确定性纪律（T4）**：禁 sleep/墙钟/网络依赖，随机必须钉种子。

### D3 编写时机与可见性：critic pass 后、dispatch 前；对 dev 可见
在 critic 后写：测试从**过闸的** PRD 派生（不过闸的 PRD 不值得写测试）。对 dev 可见（TDD 语义：验收点本来就在 PRD 里可见，测试不隐藏信息增量；防 overfit 靠「测试锚定验收点语义」而非隐藏）。备选 verify 前写且对 dev 隐藏——否决：错位风险大于 overfit 风险，且 dev 盲做命中率低浪费轮次。

### D4 verify agent 会话形态
headless `claude -p --output-format stream-json`（编排器捕获原始字节流）于 verify worktree，`--allowedTools "Bash"`（Read 可给可不给——判定不靠它读；给了便于诊断，v1 给）。**stream-json 仓内零先例**（persona 走 json 信封、SDK 流在 prompt_stream 另路）——tasks 2.0 前置冒烟锚定事件结构（本体论审核 B3）。prompt 结构化注入：env spec + 验收测试位置 + 全量门命令（优先序：profile `verify_test_cmd` > dev hint > 无则只跑注入测试），明确围栏（禁改任何文件，装依赖/工具链可）。超时沿用 `VERIFY_INSTALL_TIMEOUT` / `VERIFY_TEST_TIMEOUT` 量级合并为会话墙钟上限。install 失败（agent 跑不到 test cmd）→ transcript 无匹配 tool_result → `unresolved` → invalid/triage（基建红语义，沿 verify-loop-discipline B2 三分管方向，不进 revise 回环）。**会话墙钟超时同映射 invalid（非红）**——对齐机械轨 `_run_capture` 超时抛 RuntimeError 的非失败码语义（run_daily.py:1806），超时是不可判读不是失败（测试学二审 T4）。

### D5 三锚实现 + 证据落盘
1. **退出码 + stdout**：解析编排器捕获的 stream-json，匹配 declared cmd（shell 归一化后前缀匹配注入命令串），取对应 tool_result 的 **exit code**（判绿红）与 **stdout**（落 `test_log`——runner 原始输出保留，主 spec「Line-anchored verify feedback」的行锚解析输入在新轨不断供，本体论审核 B1）。匹配不到 → `unresolved` → invalid（保守，绝不猜绿）。
2. **tracked 不变**：会话后 `git -C vt diff --quiet && git diff --cached --quiet`；untracked 检查 = `git status --porcelain` 的非 ignored 项必须 ⊆ 注入集。
3. **注入件 digest**：对注入文件集逐个重算 sha256（`artifact_store.compute_digest`）对账注入时 digest。
4. **证据落盘 + 完整测试报告（B2 + 用户追加 2026-10-26）**：原始 stream（或锚证据子集 + digest）持久化到 `state/runs/`，与 test_log 同目录同生命周期——agent 轨的判定证据必须 durable 可复核（invalid triage / crash 复核有可回看对象），与机械轨 test_log / journal 同类别。**每次 verify 完成（绿/红/invalid，两轨统一）编排器产完整结构化测试报告**落 `state/runs/<stamp>.verify-report.json`：run 身份（PRD/iteration/branch/stamp）+ 命令来源与 declared 命令清单 + 逐命令 exit code 与输出指针（test_log 路径）+ 锚检查结果（agent 轨）或 install/test rc（机械轨）+ 判定与证据指针（test_log / 原始 stream / 注入件 digest 清单）+ 会话元（turns/时长/命令来源 profile|dev-reported|none）+ **环境奇偶节**（declared env spec vs transcript 观测到的实际组装事实——Bash 命令机械提取；v1 记录不阻断，奇偶分歧升门留 v2，测试学二审 T3）；dispatch record 持报告路径。报告是 verify 阶段的输出契约，机械可查（零 LLM 组装），pa-verify / triage / report 阶段共消费。
三锚任一违反 → result invalid（非红非绿），dispatch record 记录违反详情，走 triage。

### D6 flag-gated + canary cutover + graph 对称
`PA_VERIFY_AGENT`（feature_flags.py，默认 off = 现行为 byte-identical）。`graph_pa_verify` / `graph_pa_nodes` 直调面对称同改（flag off 零变化）。cutover 证据：双跑 canary（机械轨 vs agent 轨同 PRD 对账绿红分布；分歧样本人工 triage）≥3 轮后可切默认——默认切换独立决策，不在本 change 内完成。**shadow parity / graph cutover canary 一律 `PA_VERIFY_AGENT=off` 跑**（本体论审核 C2：on 会把 LLM 非确定性注入 dispatch 终态分布，污染 matched 判定——与 `--dispatch-skip-dev` 零 LLM 原则同源）。

### D7 harden-verify-test-scope 归并
其唯一 delta requirement「Independent verification test scope」的场景（优先序/来源标注/回归锚）已逐条并入本题 ADDED「Independent verification execution and scope」。原目录（0/9 未施）归档，tasks 含收尾步骤。

## Risks / Trade-offs

- [每次 verify 烧一次 agent 会话（绿路径不再零 token）] → 会话 max-turns 收紧（执行型任务 ~10 turn 量级）+ 只在 dispatch 到 verify 步才起会话；成本换环境自适应，用户已裁决接受。
- [agent 越权改文件] → 锚②③机械拦截，invalid fail-closed + triage；围栏同时写进 prompt（软）与锚（硬）。
- [stream-json 命令匹配误判] → 归一化前缀匹配 + 匹配不到一律 `unresolved`→invalid（宁可 triage 不猜绿）；测试锁归一化器。
- [验收测试与项目意图错位（persona 写偏）] → 编写依据=过闸 PRD 验收点 + traceability（每测试标 criterion id，机械可查覆盖）；错位残余由 pa-verify 语义审查兜底。
- [harness 面外项目（profile 没声明 harness）] → 不编写、显式记缺席（不造假测试面）；全量门仍跑 `verify_test_cmd`。
- [oracle 缺陷误归为代码红] → 收集级失败签名机械可辨（pytest exit 2 / jest suite-level）+ triage 语义确认双闸；重编写走 digest 版本链不烧 revise 预算（测试学二审 T2）。

## Migration Plan

1. **阶段 1（flag 无关）**：acceptance_tests.py 工件链 + journal 事件 + dev 侧 `--acceptance-tests` 注入（dev 侧注入独立 flag `PA_DEV_ACCEPTANCE_TESTS`？否——同一工件链，注入本身无行为变更：dev 收到文件只是多输入，证据门语义由 MODIFIED requirement 生效即红注入测试同样拦发布）。harden 目录归档。
2. **阶段 2**：verify_agent.py + independent_verify flag 分支 + 三锚 + graph 对称 + 测试。
3. **回滚**：`PA_VERIFY_AGENT=off` 秒回机械轨；工件链停用 = profile 不声明 harness 即不编写（自然衰减，无需卸载）。

## Open Questions

- **OQ1** env spec 是否需要 `services` 块（DB/redis 容器）：v1 不做，等第一个真实项目卡住再立。
- **OQ2** transcript 中 install 阶段与 test 阶段的更细粒度区分（agent 一次 Bash 里跑 `install && test`）：v1 靠 prompt 要求分步执行 + 命令串锚定 test；若实测混跑率高，再收紧 prompt 模板或加结构化 marker（`echo` 哨兵）。
- **OQ3** codex 作为第二执行器（用户提到「之一」）：v1 只 claude；执行器以 Protocol 抽象（`VerifyExecutor.run(spec) -> stream`），后补 codex 实现不动锚点层。
