## MODIFIED Requirements

### Requirement: 最小断言规则集（R1–R4，零误报设计）

看门狗每轮 SHALL 执行四条层间机械断言（判据零人工判读；网络依赖项走三态分流，UNKNOWN 绝不进漂移项）：

- **R1（L3 意图 vs L3 快照）**：intent 文件（`HUB_DRIFT_ENV_INTENT` 指定的 ro 挂载 hub.env 副本；未配 = 本规则关闭零行为）中每个 `KEY=VALUE`（compose env_file 字面语义）与 hub 进程 env 逐键比较——值不等或进程 env 缺键 = 漂移项（「盘上 hub.env 已改、容器未重建」；修复指引 `docker compose up -d`）。R1 部署前提 = ro 挂载 + 宿主侧属主对齐（bind mount 保留宿主属主/权限：`chown "$(id -un)":1001 hub.env && chmod 0640`，宿主 gid 1001 被占时等效 ACL `setfacl -m u:1001:r`——容器进程 `user: "1001:1001"` 直读 0600 属主文件恒 EACCES）；路径已配但打开失败 SHALL 为独立前提故障告警：一次性（含属主对齐修法）+ 心跳行标注「R1 降级（intent 不可读）」，MUST NOT 逐轮重复异常刷屏，其余规则不受连坐。
- **R2（L4 vs L3 兜底源）**：仅对配置单用户（cfg.user_id）的 vault 记录，与 env 兜底源（`user_env()` 同源键）**双侧共有键**值比对，键分两档：
  - **凭据键**（ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / GH_TOKEN / GITLAB_TOKEN / GITCODE_TOKEN）不等 = 漂移项（vault 旧快照；派发 vault 优先，改 hub.env 不生效即此因；修复指引 `vault-import --from-env`，merge 语义幂等——cfg.user_id 记录仅 vault-import 写入（面板录入写 mm_user_id 键），凭据键分歧必为导入后 hub.env 又改，零误报）。
  - **模型键**（vault ANTHROPIC_MODEL ↔ env HUB_USER_MODEL）不等 = **信息项，不进漂移项**：vault 模型按派发既有语义是「用户显式选择，优先于 hub 级缺省」，与 hub.env 缺省不同是合法状态且机械不可辨「显式选择」与「旧快照」——按零误报原则降档；信息项报文 MUST 中性（注明派发以 vault 值优先、确系旧快照可重导）并 MUST 警示 vault-import merge 会以 hub.env 非空值覆盖显式选择。
  - vault 单侧独有键 MUST NOT 报（merge「只增不减」与面板留空保留是 by-design）；vault 已配但无该用户记录 = 信息项（派发恒走 env 兜底 warn）；vault 未配置（master key 缺）= env-only 合法形态，R2 整体关闭零报。
- **R3（L5 注册 membership + 消费面探活 + alias 形态预检）**：消费面模型名集合（hub 进程 env `PA_PERSONA_MODEL_*` / `PA_DEV_MODEL` + 派发源 ANTHROPIC_MODEL；base_url/token 与派发同序解析 vault 优先，不另立真相源）逐名对照 `GET {base_url}/v1/models` 的 200 列表**精确匹配（大小写敏感）**——缺名 = 漂移项（修复指引 LiteLLM admin API 注册/修正，`/model/new` 需 master key）；连接错/超时/5xx = UNKNOWN 仅日志；401/403（virtual key 限 llm_api_routes）= UNKNOWN + 一次性降级提示。**消费面探活（L5 的健康 = 名册存在 ∧ 上游可用，两判据合取——「注册名在、上游死」是 2026-10-31 死链形态，membership 单断言放过）**：membership 通过的在册名（alias 黑名单除外）逐名 `POST {base_url}/v1/messages` **最小补全**（max_tokens=1、固定 canonical 请求体、Bearer token 与 membership 同源；与 membership 同轮次内执行、无独立周期，每模型每轮至多一次）三态分流——2xx = 可用零报；**404/400（确定性闭合集）= 确定性死链 → 漂移项**（固定 canonical 请求遭 4xx 拒绝是注册的属性而非请求的属性：404 = model_not_found / no deployments 类网关权威裁决「此名无可用部署」，400 = invalid_request 类确定性拒绝；修复指引 LiteLLM admin API 修正上游 `/model/update` 或下线死注册 `/model/delete`，需 master key）；网络错/超时/5xx/429/401/403 = UNKNOWN 不进漂移（单发探活无法机械分辨「持续死」与「瞬时抖」——确定性错绝不归 UNKNOWN 静默，非确定性错绝不进漂移）。探活漂移项键面 = 模型名（探活），与 membership 缺名项（键 = 裸模型名）去抖身份独立——跨轮形态转换（修复路径 `/model/delete` 删注册 → 缺名、重新注册 → 死链）无需隔消解轮即恢复告警；同轮二者按构造互斥（缺名者不探活、不重复计费）。将进容器的模型值命中 claude CLI 常量 alias 集（haiku/sonnet/opus/fable）= 漂移项（容器 CLI 把 alias 解析成裸 Anthropic id → 网关 400，run 93 实证；此为负向必死黑名单，非正向合法性白名单——与 spec: per-agent-model-routing「代码层不维护 alias 白名单」约束不冲突）。
- **R4（L6 在场 + 形态）**：存在 running run 时取最老一行，按行内快照 docker_host 路由 `docker exec <container_ref> printenv`：必需键（ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN）在场且形态合法（复用 placeholder_issues 纯形态判据）——缺键/形态非法 = 漂移项（注入链断裂）。MUST NOT 比对具体值（env-file 即弃 + vault 跑中变更是 by-design 快照语义，值比对必假阳）；exec 失败或容器已终态 = 跳过本轮（1:N 即弃常态，不误报）。

#### Scenario: 改 hub.env 未重建被 R1 抓住

- **WHEN** 盘上 hub.env 的 `PA_DEV_MODEL` 已改为新值并保存，但 hub 容器仅 restart 未 `up -d` 重建（进程 env 仍持旧值）
- **THEN** 下一轮检查产出 R1 漂移项（键名 PA_DEV_MODEL + 双侧长度差 + 修复指引 `docker compose up -d`），值本身不出现在任何日志/通知

#### Scenario: intent 面挂载但属主未对齐

- **WHEN** compose 已 ro 挂载 hub.env 但宿主侧未做属主对齐（0600 属主 uid≠1001，容器进程 uid 1001 打开 EACCES）
- **THEN** 一次性前提故障告警（指明 `chown :1001 + chmod 0640` 或 ACL 对齐修法）+ 心跳行标注「R1 降级（intent 不可读）」，绝不逐轮 PermissionError 刷屏；R2/R3/R4 不受连坐照常执行

#### Scenario: vault 旧快照被 R2 抓住（事故主因形态）

- **WHEN** vault 中配置单用户记录的 ANTHROPIC_BASE_URL 为旧网关值，而 hub.env 的 `HUB_USER_ANTHROPIC_BASE_URL` 已更新且未重导 vault
- **THEN** 产出 R2 漂移项，报文注明「派发正走 vault 旧值（vault 优先），改 hub.env 不生效即此因」+ 修复指引 `vault-import --from-env`；会话用户其他记录不被读取比对

#### Scenario: vault 单侧保留键不误报

- **WHEN** vault 记录含 GITLAB_TOKEN 而 hub.env 已删该键（merge 只增不减的历史保留）
- **THEN** 不产出漂移项（单侧独有键是 by-design 保留，非层间分歧）

#### Scenario: 模型键分歧报信息项（显式选择保护）

- **WHEN** vault 中 cfg.user_id 记录的 ANTHROPIC_MODEL（导入/录入时点的显式选择）与 hub.env `HUB_USER_MODEL` 值不同
- **THEN** 产出信息项（仅 stderr 去抖一次，不进 MM 漂移私信、不计入漂移项与消解计数），报文注明派发按既有语义以 vault 值优先（合法）并警示重导（vault-import）会以 hub.env 值覆盖该显式选择——绝不标为「vault 旧快照」

#### Scenario: 网关模型名分家被 R3 抓住

- **WHEN** 消费面含 `qwen3.8-27b-fast` 而网关 `/v1/models` 列表仅有 `Qwen3.8-27B`（大小写敏感精确匹配不命中）
- **THEN** 产出 R3 漂移项（模型名 + 修复指引 LiteLLM admin API 注册/修正，注明 /model/new 需 master key）

#### Scenario: R3 网络错与权限不足不误报

- **WHEN** 网关连接超时（或 5xx），或 virtual key 调 /v1/models 恒 403（llm_api_routes 限定）
- **THEN** 均为 UNKNOWN：不产出漂移项仅记日志；403 形态额外一次性提示「membership 断言降级」（不逐轮刷屏）

#### Scenario: 死链注册（名册在册、上游死）被 R3 探活抓住

- **WHEN** 消费面模型名在 `/v1/models` 名册在册（membership 通过），但其最小补全探活（`POST /v1/messages`，max_tokens=1）遭 404/400 确定性拒绝（如名册仍含 qwen3.6-35b 而其上游 8896 已死的 2026-10-31 形态）
- **THEN** 产出 R3 探活漂移项（层对「L5名册→L5可用」+ 模型名 + HTTP 码 + 「名册在册≠上游可达」口径 + `/model/update` 修上游或 `/model/delete` 下线死注册的修复指引，需 master key）；与 membership 缺名判据共存不连坐——同轮缺名模型照常报缺名漂移且不被探活（不重复计费）；探活漂移项复用既有 drift 键去抖与消解回执，键面 = 模型名（探活）与缺名项（裸模型名）身份独立——修复路径删注册后的缺名形态转换无消解轮隔开也照常告警

#### Scenario: 探活瞬时错不误报

- **WHEN** 最小补全探活遭网络错/超时/5xx/429 限流/401/403（瞬时族或探活凭据作用域，非网关对该注册的确定性裁决）
- **THEN** 均为 UNKNOWN：不产出漂移项、不进 MM，心跳行标注探活 UNKNOWN 计数（stderr 可见）——单发探活无法机械分辨持续死与瞬时抖；确定性错（404/400）绝不归 UNKNOWN 静默，反向同样成立

#### Scenario: 探活可用零报

- **WHEN** 每个在册消费面模型名的最小补全探活回 2xx
- **THEN** R3 探活零漂移项零额外报文（心跳 R3=ok）；成本面每模型每轮恰一次最小补全（无重试、无独立周期）

#### Scenario: 容器形态 alias 值被预检拦下

- **WHEN** hub 进程 env 的 `PA_PERSONA_MODEL_PA_PRD=sonnet`（claude CLI 常量 alias 将透传进 run 容器）
- **THEN** 产出 alias 漂移项（容器 CLI 会把 sonnet 解析成裸 Anthropic id 遭网关 400，run 93 类；值须裸模型名）

#### Scenario: R4 只断言在场与形态不比值

- **WHEN** 某项目 running run 容器 printenv 缺 ANTHROPIC_BASE_URL（注入链断裂），或 ANTHROPIC_AUTH_TOKEN 为 len=1 占位形态
- **THEN** 产出 R4 漂移项（缺键/形态非法，键名面）；容器凭据值与派发时点快照的一致性 MUST NOT 被断言（跑中 vault 变更是既有 spec 语义）

#### Scenario: 无 running run 时 R4 零行为

- **WHEN** 检查轮执行时无任何 running run（或最老 running 容器已终态/exec 失败）
- **THEN** R4 跳过本轮，不产出漂移项（即弃容器常态）
