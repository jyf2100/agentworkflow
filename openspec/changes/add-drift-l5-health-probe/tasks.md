## 1. 实现（hub/config_drift.py，探活步住在 _rule_r3 内）

- [x] 1.1 `HttpPost` 类型别名（(url, token, model) → HTTP status）+ `_default_http_probe`：POST `{base}/v1/messages` 最小补全（max_tokens=1、固定 canonical body、Bearer token 同 membership、`_HTTP_TIMEOUT` 超时）；2xx 折叠 200、HTTPError 回原码、网络/超时回 0；响应体不参与判据不落日志
- [x] 1.2 `_rule_r3` 探活步：membership 200 之后，对 `(消费面名 − alias 黑名单) ∩ 在册名` 逐名探活三态分流——2xx 零报；404/400 确定性闭合集（`PROBE_DEAD_CODES`）= 漂移项（层对「L5名册→L5可用」+ 模型名 + HTTP 码 + `/model/update`/`/model/delete` 修复指引）；0/5xx/429/401/403 = UNKNOWN 计数（心跳标注「探活 UNKNOWN×N」，不进漂移不进 MM）；缺名者不探活（判据互斥不重复计费）
- [x] 1.3 `check_drift` 增 keyword-only 可选参 `probe_client: HttpPost | None = None`（None → 默认实现）并穿到 `_rule_r3`——lifecycle/daemon 挂点零改动（默认值兼容既有调用）；模块头断言范围行补探活口径
- [x] 1.4 值不入面复核：token 只进 Authorization 头；探活报文只含规则/层对/模型名/HTTP 码/指引

## 2. 测试（hub/test_config_drift.py 增量，紧凑风格，零网络）

- [x] 2.1 `_probe(status)` fake + `_run_check` 助手增 `probe` 参（缺省 200）——既有测试密闭不漏真网络；`test_dedup_once_and_resolution_receipt` 直调处补 `probe_client`
- [x] 2.2 确定性死链→漂移项：404 与 400 两形态、层对/模型名（键面带（探活）标记）/HTTP 码/修复指引键名面断言、进 MM、token 不入面、同项去抖第二轮不重发
- [x] 2.3 瞬时→UNKNOWN：0/500/429/403 四码不进漂移不进 MM、心跳「探活 UNKNOWN×」可见
- [x] 2.4 可用→零报：probe 全 2xx → R3=ok 零漂移零私信
- [x] 2.5 与 membership 共存不连坐：同轮一缺名（membership 漂移照报）一在册死链（探活漂移）共存；探活 fake 记录调用面断言**只调在册名**（成本面 + 缺名不探活）
- [x] 2.6 键身份独立性（冷评审 R2）：死链→按指引删注册→缺名的跨轮形态转换**无消解轮隔开**——缺名漂移照报不被探活同键吞掉、不误发消解回执

## 3. 文档

- [x] 3.1 RUNBOOK §23：看门狗段 R3 描述补探活一句；**排障速查补一行**（死链注册形态「L5名册→L5可用」→ admin API 修上游/下线死注册 + 5xx 形态人工复测 curl 通道）

## 4. 验证

- [x] 4.1 `cd hub && python -m pytest test_config_drift.py -q` 全绿（单文件 scoped，全量质量门归编排）
- [x] 4.2 `openspec validate add-drift-l5-health-probe --strict --no-interactive` 绿
- [ ] 4.3 真机（184）复核 roc 死链（qwen3.6-35b）探活实际回码形态（404 no-deployments vs 恒 500）——校准 D1 确定性锚；恒 500 则激活 strike counting follow-up（design Open Questions，不阻塞本 change）

## 实施证据（2026-11-01）

- 1.1：config_drift.py `HttpPost` 别名 + `PROBE_DEAD_CODES = {400, 404}` 闭合集（带零误报双向论证注释）+ `_default_http_probe`（canonical body：model/max_tokens=1/messages[ping]；2xx 折叠 200、HTTPError 原码、URLError/Timeout/OSError/ValueError → 0；响应体零参与零落日志）
- 1.2：`_rule_r3` 探活步在 membership 200 分支内（同轮次内做）：`(names − ALIAS_BLACKLIST) ∩ registered` 逐名 POST `{base}/v1/messages`（Bearer token 同 `_dispatch_source` 解析）；DriftItem("R3", "L5名册→L5可用", 模型名) 层对命名对仗 R1「L3意图→L3快照」（同层双态）；UNKNOWN 计数入心跳 `ok（探活 UNKNOWN×N）`
- 1.3：`check_drift(probe_client=None)` keyword-only 可选参，None → `_default_http_probe`；lifecycle.check_config_drift（禁改面）零改动默认兼容；模块头断言范围行更新
- 1.4：报文面 = 规则/层对/模型名/HTTP 码/修复指引（test 断言 "Bearer" 不在私信）；token 仅入 Authorization 头
- 2.1-2.5：test_config_drift.py 16 测试全绿（11 既有 + 5 新增：`test_r3_probe_deterministic_dead_link_and_dedup` / `test_r3_probe_transient_unknown_no_drift` / `test_r3_probe_available_zero_report` / `test_r3_probe_coexists_with_membership_no_crossfire`——probe fake 记录调用面断言 `calls == ["m-dead"]` 只探在册名 / `test_r3_probe_key_independent_from_membership_on_delete`——冷评审 R2 形态转换）
- 3.1：RUNBOOK §23 两处（看门狗段 R3 描述 + 排障速查死链行含 curl 人工复测通道）
- 4.1：`cd hub && python -m pytest test_config_drift.py -q` → 15 passed in 0.04s（scoped，未跑全量 quality.sh——归编排）；`python -m ruff check config_drift.py test_config_drift.py` → All checks passed（E9+F，同 quality.sh 口径）
- 4.2：`openspec validate add-drift-l5-health-probe --strict --no-interactive` → valid（MODIFIED 基线逐字取自 add-config-drift-watchdog 既有 delta——hub-config-drift-watchdog 尚未归档进 openspec/specs/，本仓 openspec 1.4.1 对未归档 capability 的 MODIFIED 不做基线存在性交叉校验，已实证）
- 4.3：未做（roc 网关 184 真机不在本工作面内）——design Open Questions 已落档，apply 期复核

## 冷评审 R2 修正（2026-11-01，独立冷评审）

- 发现：探活死链漂移与 membership 缺名漂移共用裸去抖键 `("drift","R3",<模型名>)`，「死链→按修复指引 /model/delete→名册缺名」方向不隔消解轮——轮 N 死链键入 seen 后，轮 N+1 起同键缺名漂移被 new_drift 吞掉，且漂移非空使消解回执永不触发、seen 键永不清理，缺名持续期 MM 零告警（初稿 design D4 只论证了「缺名→注册死」单向）
- 修正：探活 DriftItem 键面改 `模型名（探活）`（config_drift.py `_rule_r3` 探活步 + 步内注释落档链路）——与缺名项（裸模型名）去抖身份独立，两方向形态转换无消解轮隔开也恢复告警；不改全局去抖契约（「同一漂移项 = 规则 + 键名」是基 change「漂移报告面」既有语义）；design D4/Risks、spec delta R3 条款与死链 scenario THEN、proposal 同步改写
- 测试：新增 `test_r3_probe_key_independent_from_membership_on_delete`（死链告警→删注册→缺名照报 n.sent==2、不误发消解回执）；`test_r3_probe_deterministic_dead_link_and_dedup` 键面断言收紧为 `m404（探活）`/`m400（探活）`——16 测试全绿 + validate 复跑绿（见 4.1/4.2 行）
