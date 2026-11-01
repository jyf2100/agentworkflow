## Context

本体论审核 R1（2026-11-01，P1-1）对 add-config-drift-watchdog 的裁定：六层模型的 L5（网关注册层）**捆绑了两个判据**——存在判据（名字在 `/v1/models` 名册）与可用判据（上游可达、补全真的能跑通）。R3 只断言前者：roc 名册含 `qwen3.6-35b`（在册=True 真机复核）而其上游 8896 已死，membership 放行、run 全挂——2026-10-31 事故链一环。层的内部结构未展开是模型盲点，同构先例即三态远程查询把 UNKNOWN 从 NOT_FOUND 拆出来的那一课。

处置选审核给出的主路径：R3 增补消费面轻探活（而非最低限度的「断言范围=名册非健康」声明——后者把可用性推回 ops 人肉面，事故已经证明那条路排障靠 `docker exec` 逐层对表）。

## Goals / Non-Goals

**Goals:**
- 可用判据机械可断言：在册消费面名一次最小补全三态分流，死链（确定性闭合集）进漂移项、瞬时错绝不误报——零误报纪律全承
- 成本有界：每模型每轮恰一次 max_tokens=1 补全，同轮内做，无新配置面
- 观察面纪律全承：只读、值不入面、异常不连坐、daemon/lifecycle 挂点零改动

**Non-Goals:**
- **不做重试/多次采样升级（strike counting）**：连续 N 轮 5xx 升级漂移需要跨轮状态机（每模型计数器 + 清退语义），超出最小起步；单发判据先落地，若真机实证死链恒以 5xx 形态呈现再立 follow-up（Open Questions）
- **不把 5xx/连接错推断为死链**：单发探活无法机械分辨「持续死」与「瞬时抖」（过载/限流/网络分区都呈 5xx 族）——非确定性错绝不进漂移，这是零误报纪律的反向面
- **不探活缺名模型**：名册缺名的漂移项已由 membership 承载，探活缺名者既重复计费又抢跑判据（membership 是探活的前置闸）
- **不做独立周期/独立开关**：与 membership 同轮内做（list 200 之后立即），周期沿用 `HUB_DRIFT_TICKS`；无新 `HUB_*` 键——探测调用量已由「每模型每轮一次」上界封顶，无需运维旋钮
- 不动 daemon/lifecycle/compose/env.example（探活步整体住在 `_rule_r3` 内部，`check_drift` 只增可选注入参）

## Decisions

### D1 探活判据三分（本体修复的核心：把 L5 内部两判据拆开断言）

探活问题 = 「消费面今天能不能真的用这个名跑一次补全」。三态：

| 态 | 机械判据 | 动作 |
|---|---|---|
| 可用 | `POST {base}/v1/messages` 回 **2xx** | 零报（心跳 R3=ok） |
| 确定性死链 | 回 **404 或 400**（闭合集） | 漂移项（层对「L5名册→L5可用」+ 模型名 + HTTP 码 + 修复指引） |
| UNKNOWN | 0（网络/超时/解析）、**5xx**、**429**、**401/403** | 不进漂移，心跳标注「探活 UNKNOWN×N」 |

**为什么确定性集是 404/400 而非「所有 4xx」或「含 5xx」**——两个方向的零误报论证：

- **正向（确定性绝不漏报进 UNKNOWN）**：请求体是固定 canonical 形状（三必填字段、max_tokens=1、content="ping"），所以 4xx 拒绝**是注册的属性而非请求的属性**。404 = model_not_found / no deployments 类——网关自身权威裁决「此名无可用部署」（路由配置与名册解耦的形态，正是「名册在册 ≠ 可用」的机械可证面）；400 = invalid_request 类——canonical 请求被拒即网关对该名注册的确定性拒绝。两者都是网关说出口的裁决，不是我们对外部状态的推断。
- **反向（非确定性绝不进漂移）**：429 是负载瞬态（限流不等于死）；5xx 是上游/过载族（死上游的原始连接错呈 500 connection error，但过载也呈 500——单发不可分辨，推断必假阳）；401/403 是探活凭据作用域（virtual key 面，同 membership 401/403 精神——是探活器的局限不是模型的死活）；0 是网络。全归 UNKNOWN。
- **边界诚实声明**：上游死以 5xx 形态呈现时本轮探活 UNKNOWN——这是刻意的保守取舍（RUNBOOK 排障速查行给出人工复测通道：一次 curl 最小补全）。若网关（LiteLLM router）把持续死上游的 deployment 打入 cooldown 后以 404 no-deployments 回应，探活即抓住——确定性锚在**网关自己的权威口径**上，这正是「机械判据零人工判读」的本义。

### D2 成本面：每模型每轮恰一次最小补全，同轮内做

- **对象**：`(消费面名集合 − alias 黑名单) ∩ 名册在册名`——缺名者不探活（membership 漂移项已承载，不重复计费不抢跑）；alias 名不探活（alias 漂移项已承载，且 alias 名本就不是有效注册名）。
- **形状**：`POST {base}/v1/messages`，body = `{"model": <名>, "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]}`（固定 canonical——D1 论证的根基），header 仅 `Authorization: Bearer <token>`（与 membership 同源同值，`_dispatch_source` 解析，不另立真相源）+ `Content-Type`；超时沿用 `_HTTP_TIMEOUT`（10s）。
- **频次**：与 membership 同一轮次内（list 200 之后立即顺序执行），周期即 `HUB_DRIFT_TICKS`（默认 ≈ 每小时）；每模型每轮至多一次、无重试。量级：在册消费面名（persona×N + dev + 派发源，个位数）× 1 token 级补全/轮。
- **无新配置键**：调用上界由判据结构封顶，不给运维留旋钮（先例：R4 取样最老一个容器也无旋钮）。

### D3 注入接缝：HttpPost 独立可 fake（FakeGateway 先例扩展）

`HttpGet = Callable[[str, str], tuple[int, list[str] | None]]` 旁新增 `HttpPost = Callable[[str, str, str], int]`（(url, token, model) → HTTP status，2xx 折叠 200、网络错 0）；`check_drift(..., probe_client: HttpPost | None = None)`，None → `_default_http_probe`。**为什么独立参数而不是复用 http_client 按 URL 分派**：FakeGateway 先例的精神是「每个外部调用面一个可替换接缝」——列表与补全是两个协议不同的调用（GET/解析 JSON vs POST/只要码），测试要能分别设局（名册全绿 + 探活全红的组合死链形态、名册缺名 + 探活不可达的组合），单 callable 按 URL 分派会把两个独立断言面的测试耦合在一个 fake 的内部分支里。既有测试经 `_run_check` 助手默认注 `_probe(200)` 保持密闭零网络（否则默认实现会对 `http://gw:4000` 发真请求）。

### D4 去抖与键面：复用既有 drift 键机制，探活键带形态标记与 membership 身份独立（冷评审 R2 修正）

探活漂移项 = `DriftItem("R3", "L5名册→L5可用", <模型名>（探活）, …)` → 自动走既有 `("drift", rule, key)` 去抖 + 消解回执 + MM/stderr 三分报告 + 日报未消解计数（`daily_digest` 数 `s[0]=="drift"` 键，自动兼容）。层对命名对仗 R1 的「L3意图→L3快照」（同层双态）：名册态 → 可用态。

**探活键 = `模型名（探活）` 而非裸模型名（与 membership 缺名项键身份独立）——初稿共用裸键被冷评审 R2 推翻**：共用裸键在「死链 → 按修复指引 `/model/delete` 删注册 → 名册缺名」方向不隔消解轮——轮 N 死链键入 seen 后，轮 N+1 起的同键缺名漂移被 `new_drift` 过滤吞掉，且漂移非空使消解回执永不触发、seen 键永不清理，缺名持续期 MM 零告警（按修复指引操作的忠实用户恰踩入此静默窗，与 membership 缺名告警承诺冲突）。修正 = 键带形态后缀：两个方向的跨轮形态转换（删除注册→缺名、重新注册→死链）**无需隔消解轮即恢复告警**；同轮二者仍按构造互斥（缺名者不探活，在册者不缺名）。不改全局去抖契约（「同一漂移项 = 规则 + 键名」是基 change「漂移报告面」requirement 的既有语义，键面区分在其内完成）。心跳态在探活有 UNKNOWN 时标注 `ok（探活 UNKNOWN×N）`——UNKNOWN 可见（stderr 心跳）但不进 MM、不进漂移（防告警疲劳，R3 membership UNKNOWN 同款纪律）。

### D5 值不入面全承

报文面 = 规则 id + 层对 + 模型名 + HTTP 码 + 修复指引。token 只进 Authorization 头绝不落日志；**响应体完全不参与判据也不落日志**（判据锚 status 码——机械稳定性，不嗅探 body 错误文本，防网关版本措辞漂移腐化判据）；修复指引含 admin API 通道（`/model/update` 改 litellm_params 指向活上游，或 `/model/delete` 下线死注册，需 master key——master key 暴露面停在 litellm 容器的既有边界不动，hub 只用 virtual key 探测）。

## Risks / Trade-offs

- [上游死恒以 5xx 呈现 → 探活抓不到] → 刻意保守：单发不可分辨持续死与瞬时抖，推断必假阳；RUNBOOK 排障速查行给人工复测通道（curl 一次最小补全）；若真机实证恒 5xx，strike counting（连续 N 轮同码升级漂移）为已记名 follow-up（Open Questions）
- [400 误报（网关对 canonical 请求的特判）] → canonical 形状固定（三必填字段最小集），误报即网关对该名的确定性拒绝——本身就是要抓的事态；漂移项去抖一次，修复指引即人工核实通道
- [成本] → 每轮（≈每小时）× 在册消费面名（个位数）× max_tokens=1——个位数 token 级/轮；对齐 R3 既有出站 HTTP 开销量级
- [跨轮形态转换的告警静默窗] → 已由探活键形态标记（`模型名（探活）`）结构性消除（D4 冷评审 R2 修正）：删除注册→缺名、重新注册→死链两方向均无消解轮隔开也恢复告警；代价是同模型两形态各占一键，消解回执计数按开口项计（准确反映两条告警史）
- [探活调用被网关计入配额/计费] → virtual key 面的既有配额语义，max_tokens=1 是协议最小；与派发同 key 同 base，不新增暴露面

## Migration Plan

1. 纯增量部署即生效：无 DB 迁移、无新 env 键、无 compose 改动（`up -d` 重建随下次例行部署携带）。
2. 回滚：`check_drift` 签名只增可选参（默认值兼容），撤探活步不影响其余三规则；残留行为零。
3. RUNBOOK §23：看门狗段 R3 描述补探活一句 + 排障速查补死链注册形态行。

## Open Questions

（不阻塞合并）roc 真机上死上游（8896 类）在 `/v1/messages` 的实际回码形态——404 no-deployments（LiteLLM router cooldown 生效）还是恒 500 connection error。apply 期真机复核校准 D1 确定性锚；若恒 500，激活 strike counting follow-up（连续 N 轮同 5xx 升级漂移项，需跨轮状态机）。人工复测通道已在 RUNBOOK 排障速查行落档。
