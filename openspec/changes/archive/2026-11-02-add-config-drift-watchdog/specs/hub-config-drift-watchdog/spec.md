## ADDED Requirements

### Requirement: 六层漂移模型与断言范围

hub SHALL 以六层漂移模型维护模型路由/凭据配置的层间一致性可观测性：L1 本机 CLI settings 层（`~/.claude/settings.json` env 块）、L2 本机形态路由文件层（`Projects/项目推进流水线/config/model-routing.json`）、L3 hub env_file 层（`hub.env`——仅容器创建时读入 hub 进程 env，restart 不重读）、L4 凭据保险箱层（spec: hub-credential-vault）、L5 网关注册层（roc LiteLLM 模型注册面）、L6 run 容器实况层（最终生效面）。断言范围 SHALL 限 hub 容器可达四层（L3/L4/L5/L6）；L1 与 L2 MUST NOT 进入机械断言——L1 本机家目录 hub 不可达，L2 容器形态按 spec: per-agent-model-routing 有意不读（镜像构建面 `.dockerignore` 固化排除），两层的排障入口是部署文档六层模型节。看门狗 SHALL 只读不写：发现漂移仅产出报告面动作（漂移项 + 修复指引），MUST NOT 自动修复（重建容器 / vault 重导入 / 网关注册变更都是人工决定）。

#### Scenario: L1/L2 层漂移不产生断言

- **WHEN** 本机 L1 settings.json 或仓内 L2 model-routing.json 发生配置漂移
- **THEN** hub 看门狗不产生任何漂移项或告警（两层不在 hub 可达面），RUNBOOK 六层模型节是这两层的排障入口

#### Scenario: 不自动修复

- **WHEN** 看门狗发现任一漂移项（如 vault 旧快照）
- **THEN** 唯一动作是报告面输出（漂移项 + 修复指引文本）；不执行 `up -d` 重建、不写 vault、不调网关 admin API

### Requirement: 最小断言规则集（R1–R4，零误报设计）

看门狗每轮 SHALL 执行四条层间机械断言（判据零人工判读；网络依赖项走三态分流，UNKNOWN 绝不进漂移项）：

- **R1（L3 意图 vs L3 快照）**：intent 文件（`HUB_DRIFT_ENV_INTENT` 指定的 ro 挂载 hub.env 副本；未配 = 本规则关闭零行为）中每个 `KEY=VALUE`（compose env_file 字面语义）与 hub 进程 env 逐键比较——值不等或进程 env 缺键 = 漂移项（「盘上 hub.env 已改、容器未重建」；修复指引 `docker compose up -d`）。R1 部署前提 = ro 挂载 + 宿主侧属主对齐（bind mount 保留宿主属主/权限：`chown "$(id -un)":1001 hub.env && chmod 0640`，宿主 gid 1001 被占时等效 ACL `setfacl -m u:1001:r`——容器进程 `user: "1001:1001"` 直读 0600 属主文件恒 EACCES）；路径已配但打开失败 SHALL 为独立前提故障告警：一次性（含属主对齐修法）+ 心跳行标注「R1 降级（intent 不可读）」，MUST NOT 逐轮重复异常刷屏，其余规则不受连坐。
- **R2（L4 vs L3 兜底源）**：仅对配置单用户（cfg.user_id）的 vault 记录，与 env 兜底源（`user_env()` 同源键）**双侧共有键**值比对，键分两档：
  - **凭据键**（ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / GH_TOKEN / GITLAB_TOKEN / GITCODE_TOKEN）不等 = 漂移项（vault 旧快照；派发 vault 优先，改 hub.env 不生效即此因；修复指引 `vault-import --from-env`，merge 语义幂等——cfg.user_id 记录仅 vault-import 写入（面板录入写 mm_user_id 键），凭据键分歧必为导入后 hub.env 又改，零误报）。
  - **模型键**（vault ANTHROPIC_MODEL ↔ env HUB_USER_MODEL）不等 = **信息项，不进漂移项**：vault 模型按派发既有语义是「用户显式选择，优先于 hub 级缺省」，与 hub.env 缺省不同是合法状态且机械不可辨「显式选择」与「旧快照」——按零误报原则降档；信息项报文 MUST 中性（注明派发以 vault 值优先、确系旧快照可重导）并 MUST 警示 vault-import merge 会以 hub.env 非空值覆盖显式选择。
  - vault 单侧独有键 MUST NOT 报（merge「只增不减」与面板留空保留是 by-design）；vault 已配但无该用户记录 = 信息项（派发恒走 env 兜底 warn）；vault 未配置（master key 缺）= env-only 合法形态，R2 整体关闭零报。
- **R3（L5 注册 membership + alias 形态预检）**：消费面模型名集合（hub 进程 env `PA_PERSONA_MODEL_*` / `PA_DEV_MODEL` + 派发源 ANTHROPIC_MODEL；base_url/token 与派发同序解析 vault 优先，不另立真相源）逐名对照 `GET {base_url}/v1/models` 的 200 列表**精确匹配（大小写敏感）**——缺名 = 漂移项（修复指引 LiteLLM admin API 注册/修正，`/model/new` 需 master key）；连接错/超时/5xx = UNKNOWN 仅日志；401/403（virtual key 限 llm_api_routes）= UNKNOWN + 一次性降级提示。将进容器的模型值命中 claude CLI 常量 alias 集（haiku/sonnet/opus/fable）= 漂移项（容器 CLI 把 alias 解析成裸 Anthropic id → 网关 400，run 93 实证；此为负向必死黑名单，非正向合法性白名单——与 spec: per-agent-model-routing「代码层不维护 alias 白名单」约束不冲突）。
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

#### Scenario: 容器形态 alias 值被预检拦下

- **WHEN** hub 进程 env 的 `PA_PERSONA_MODEL_PA_PRD=sonnet`（claude CLI 常量 alias 将透传进 run 容器）
- **THEN** 产出 alias 漂移项（容器 CLI 会把 sonnet 解析成裸 Anthropic id 遭网关 400，run 93 类；值须裸模型名）

#### Scenario: R4 只断言在场与形态不比值

- **WHEN** 某项目 running run 容器 printenv 缺 ANTHROPIC_BASE_URL（注入链断裂），或 ANTHROPIC_AUTH_TOKEN 为 len=1 占位形态
- **THEN** 产出 R4 漂移项（缺键/形态非法，键名面）；容器凭据值与派发时点快照的一致性 MUST NOT 被断言（跑中 vault 变更是既有 spec 语义）

#### Scenario: 无 running run 时 R4 零行为

- **WHEN** 检查轮执行时无任何 running run（或最老 running 容器已终态/exec 失败）
- **THEN** R4 跳过本轮，不产出漂移项（即弃容器常态）

### Requirement: fail-open 检查器挂点（daemon 低频槽）

看门狗 SHALL 挂 daemon 排度循环低频槽：每 `HUB_DRIFT_TICKS`（默认 720，5s tick 下 ≈ 每小时一次）个 tick 执行一轮，与镜像陈旧检测、runner 池探针同型 N-tick 计数；逻辑归独立模块，Lifecycle 只持薄挂点。看门狗 MUST NOT 进入派发路径（凭据解析 / 派发决策 / env-file 写面零改动——纯只读观察面）；检查槽整体异常兜底 + 模块内逐规则吞异常（单规则异常不连坐他规则），任何异常 MUST NOT 崩排度循环或影响派发。`HUB_DRIFT_TICKS` SHALL 为 ≥1 整数（非法值启动 fail fast 拒起——与 HUB_STALE_CHECK_TICKS / HUB_PROBE_TICKS 同口径）。

#### Scenario: 检查器异常不崩循环

- **WHEN** 单轮检查抛异常（intent 文件 IO 错、HTTP 库异常等）
- **THEN** 记 warn 日志后下个间隔照常迭代，派发与对账不受影响；不因检查异常退出常驻循环

#### Scenario: 看门狗不影响派发

- **WHEN** 看门狗发现漂移项且队列有可派项
- **THEN** 仅产出报告面动作；派发决策与凭据解析路径 byte-identical（漂移不拦派发——派发前 fail-fast 已有占位符检查，看门狗是事后观察面）

#### Scenario: 周期键非法启动拒起

- **WHEN** `HUB_DRIFT_TICKS=0`、负数或非整数
- **THEN** load_config 抛 ConfigError 拒绝启动（0 炸 modulo 同型教训），不进入半配置运行

### Requirement: 漂移报告面（只报漂移项 + 修复指引 + 值不入面）

报告 SHALL 只含漂移项（全绿轮仅 stderr 一行心跳摘要）；每项 = 规则 + 层对 + 键名（可含长度差）+ 修复指引。报告 SHALL 三分：① 漂移项 → MM 私信 + stderr；② 前提故障告警（R1 intent 面不可读）→ MM 私信一次性 + 心跳行标注降级；③ 信息项（R2 vault 无记录 / 模型键分歧）→ 仅 stderr 去抖可见，MUST NOT 进入 MM 私信（值班私信只承载真漂移）。凭据值与 base_url 片段 MUST NOT 进入日志或通知（键名/长度面先例：placeholder_issues）。通道 SHALL 复用管理面告警 `notify_admin`（MM 私信配置单用户，fail-open）并降级 stderr（未配 bot 通道 = 可见的丢，对齐镜像陈旧告警降级语义）。去抖：同一漂移项（规则 + 键名）进程生命周期至多报一次；当轮零漂移且存在历史项时 SHALL 报一次消解回执并清去抖集合（修复闭环可见）。MUST NOT 建立日报或任何新持久化写面。

#### Scenario: 漂移项私信与去抖

- **WHEN** 首轮发现 R1、R2 各一项且 MM bot 通道已配
- **THEN** 配置单用户收到含两项的私信（各带修复指引）；后续轮次同两项仍在仅 stderr 可见、不重发私信

#### Scenario: 值不入面

- **WHEN** R2 报 vault 旧快照漂移（BASE_URL/Token 键值两侧不同）
- **THEN** 报文只含键名与长度差等形态信息，两侧实际值与任何片段不出现

#### Scenario: 消解回执

- **WHEN** 此前报过的漂移项全部修复（当轮零漂移）
- **THEN** 配置单用户收到一次「漂移已消解（N 项）」回执，去抖集合清空——同项若复发可再次告警

#### Scenario: 未配通道降级 stderr

- **WHEN** 未配 `HUB_MM_BOT_TOKEN`（或 MM base_url 缺）时发现漂移项
- **THEN** 漂移项降级 stderr 完整可见（可见的丢而非静默吞），心跳与去抖语义不变
