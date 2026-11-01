## Why

本体论审核 R1（docs/reviews/2026-11-01-p0-p1-batch-ontology-review-r1.md，P1-1）实证：**L5 的「存在」≠「可用」**——roc 名册至今仍含 `qwen3.6-35b`（真机复核在名册=True）而其上游 8896 已死，正是 2026-10-31 事故链的一环；R3 membership 断言只查名册（`/v1/models` 含名即 ok），**对「注册名在、上游死」形态零告警**。本体诊断：六层模型把「网关注册」当一层，实则捆绑了两个判据——存在判据（名字在册）与可用判据（上游可达），L5 的健康 = 两者合取，R3 只断言了前者。层的内部结构未展开是模型盲点（同构先例：三态远程查询把 UNKNOWN 从 NOT_FOUND 里拆出来，正是同一课）。本 change 把可用判据落成 R3 内的**消费面轻探活步**：每个通过 membership 的在册名做一次最小补全（max_tokens=1），三态分流——确定性死链（404/400 闭合集）进漂移项，瞬时错（网络/5xx/超时/限流/鉴权）归 UNKNOWN 绝不误报。

## What Changes

- **R3 增补消费面探活步**（`_rule_r3` 内、membership 200 之后同轮执行）：在册消费面名逐个 `POST {base_url}/v1/messages` 最小补全（max_tokens=1、固定 canonical 请求体、Bearer token 与 membership 同源）；三态判据——2xx = 可用零报；**404/400（确定性闭合集）= 死链漂移项**（固定 canonical 请求遭 4xx 拒绝是注册的属性而非请求的属性；404 = model_not_found / no deployments 类网关权威裁决，400 = invalid_request 类确定性拒绝）；网络错/超时/5xx/429/401/403 = UNKNOWN 不进漂移（单发探活无法机械分辨「持续死」与「瞬时抖」——确定性错绝不归 UNKNOWN 静默，反向同样成立）
- **成本有界**：每模型每轮**恰一次**最小补全（缺名者不探活——membership 漂移项已承载，不重复计费）；与 membership 同轮次内做，无独立周期、无新 `HUB_*` 键、daemon/lifecycle 挂点零改动
- **注入接缝扩展**（FakeGateway 先例）：`check_drift` 增 keyword-only 可选参 `probe_client`（None → `_default_http_probe`）——列表（http_client）与补全（probe_client）可分别 fake，测试零网络
- **去抖/报告面全承**：探活漂移项 = DriftItem("R3", "L5名册→L5可用", 模型名（探活）, …) 走既有 drift 键去抖 + 消解回执 + MM 私信通道；键面带形态标记与 membership 缺名项（裸模型名）身份独立——修复路径 `/model/delete` 后的缺名形态转换无消解轮隔开也照常告警（冷评审 R2 修正）；值不入面（报文只含模型名/HTTP 码/指引，token 与响应体绝不入）
- **spec delta**：`hub-config-drift-watchdog`「最小断言规则集」requirement MODIFIED——R3 断言范围从名册 existence 扩到消费面 availability（基线逐字取自 add-config-drift-watchdog 既有 delta）；RUNBOOK §23 排障速查补死链注册形态行（含 5xx 形态的人工复测通道）

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `hub-config-drift-watchdog`: 「最小断言规则集（R1–R4，零误报设计）」的 R3 条款扩为「L5 注册 membership + 消费面探活 + alias 形态预检」——探活步三态判据、成本上界（每模型每轮至多一次、同轮内）、与 membership 缺名判据共存不连坐（缺名者不探活）、探活漂移项复用既有去抖；新增三个 scenario（死链在册被探活抓住 / 瞬时错 UNKNOWN 不误报 / 可用零报）。

## Impact

- **代码**：`hub/config_drift.py`（`HttpPost` 类型别名 + `_default_http_probe` + `_rule_r3` 探活步 + `check_drift` 可选参 + 模块头断言范围行）；不改 `hub/lifecycle.py` / `hub/daemon.py` / `hub/config.py`（无新周期键）
- **契约**：R3 从「名册 existence」扩到「在册 + 消费面 availability」——探活是对网关的第二个只读探测调用（同 base/token，POST 而非 GET）；派发路径、写面、其余三规则零改动
- **部署**：零（无新 env 键、无 compose 改动）；RUNBOOK §23 两处文字（看门狗段 R3 描述 + 排障速查死链行）
- **测试**：`hub/test_config_drift.py` 增量四测试（确定性死链→漂移项键名面+去抖 / 瞬时四态→UNKNOWN 不进漂移 / 可用→零报 / 与 membership 缺名共存不连坐+成本面只探在册名）；`_run_check` 助手默认注 `_probe(200)` 保既有测试密闭零网络
- **成本**：每轮（默认每小时）每在册消费面模型一次 max_tokens=1 补全——个位数 token 级/轮；修复闭环经消解回执可见
