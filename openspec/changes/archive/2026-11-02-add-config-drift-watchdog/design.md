## Context

2026-10-31 真机事故（run 全挂、「改了无数次都没改回来」）实证模型路由/凭据的**六层漂移模型**——同一逻辑配置在六处独立存在，互不知晓：

| 层 | 载体 | 消费形态 | 正确写法 | hub 容器可达 |
|---|---|---|---|---|
| L1 | `~/.claude/settings.json` env 块 | 本机 CLI 直调（ANTHROPIC_BASE_URL 指 roc LiteLLM 172.32.153.184:4000 + token） | 编辑该文件 | ✗（本机家目录，无挂载无通道） |
| L2 | `Projects/项目推进流水线/config/model-routing.json` | 本机形态 per-agent 路由主点（`scripts/model_routing.py` resolve_*）；`.dockerignore` 固化排除出镜像（run 93） | git 提交；容器形态按 spec 有意不读（裁决 2026-10-27） | ✗（hub 镜像 build context = `hub/`，compose 挂载仅 sock + `/data/pa`） |
| L3 | `hub/deploy/hub.env` | hub 容器 env_file，**仅容器创建时读入** hub 进程 env | 编辑后 `docker compose up -d` **重建**（restart 不重读——RUNBOOK 实证：盘上 v0.1.25 / 运行时 v0.1.24） | 快照 ✓（进程 env）；意图文件需 ro 挂载（本 change 新增） |
| L4 | 凭据保险箱 `<data_root>/vault/credentials.json`（`vault_store.py`） | 派发凭据源（vault 优先、env 兜底——`lifecycle._credentials`） | `vault-import --from-env`（merge 语义：旧记录打底 + env 非空覆盖、只增不减，幂等）或面板凭据页；改 hub.env **不会**自动进 vault | ✓ |
| L5 | roc LiteLLM 注册（litellm 容器 + litellm_db postgres 持久化） | 网关按注册名路由；模型名按网关分家（9001 认 qwen3.8-27b-fast，roc 曾只认 Qwen3.8-27B、后经 admin API 注册 fast） | admin API `/model/new`（需 master key = litellm 容器 env `LITELLM_MASTER_KEY`；virtual key 只能 llm_api_routes，写不了注册面） | ✓（HTTP 只读探测） |
| L6 | run 容器实况 | 最终生效面（env-file 注入 + `PA_*` 透传的合成结果） | 无直写——修上游层后重派；验证通道 `docker exec printenv` | ✓（docker CLI，按 run 行内快照 docker_host 路由） |

事故链：L4 停留 4000+glm-5.3 旧快照（派发 vault 优先 → 改 hub.env 不生效）＋ L3 改后未 `up -d` 重建 ＋ L5 死链（qwen3.6-35b→8896）→ run 全挂。三层各自「正确」，叠加不可排——缺的不是任何一层的正确性，是**层间一致性断言**。

## Goals / Non-Goals

**Goals:**
- 层间不一致主动可见：漂移项 = 规则 + 层对 + 键名 + 修复指引，排障从人肉六层对表变看门狗报文
- 最小规则集起步，全部机械断言（零人工判读）、零误报设计（三态分流 / 关闭态 / 纯形态判据 / 即弃容器跳过）
- fail-open 观察面纪律全承：看门狗任何异常不影响派发与排度循环

**Non-Goals:**
- **不自动修复**：只报告 + 修复指引（`up -d` 重建 / `vault-import` / admin API 都是人工动作——修复涉及重启 hub 或改网关注册，自动执行的风险高于收益；先例：stale_check「告警是唯一动作，升级是人工决定」）
- **不重构配置存储**：六层形态各就各位（env_file / vault / 网关注册面），看门狗只读不写
- **不查 L1**：hub 容器不可达本机家目录（compose 挂载仅 sock + `/data/pa`），本机 CLI 形态漂移归本机使用面自查——无强理由破例
- **不查 L2**：hub 容器无该文件（build context = `hub/`），且容器形态按 spec: per-agent-model-routing「容器形态限定」**有意不读**（`.dockerignore` 固化排除）；L2 漂移（含 git HEAD 曾长期停留死值）是仓侧 ops 事，归 RUNBOOK 口径
- 不做派发阻断/降级：发现漂移也不拦派发（派发前 fail-fast 已有 placeholder 检查，看门狗是事后观察面——「watchdog 挂了不影响派发」的反向也成立）
- 不建日报/面板展示面（第一版 MM 私信 + stderr；面板化留 follow-up）

## Decisions

### D1 断言范围 = hub 可达四层（L3/L4/L5/L6）
可机械断言的必要条件是 hub 能读到两侧。L1/L2 排除理由见 Non-Goals（锚点：`hub/deploy/docker-compose.yml` volumes 仅 sock + `/data/pa`；`hub/.dockerignore`/根 `.dockerignore` 排除 `config/`；per-agent-model-routing 容器形态限定 requirement）。四层中 L3 快照（进程 env）、L4（vault 同机文件）、L5（base_url HTTP）天然可达；L3 **意图文件**（盘上 hub.env）是唯一缺的读面——compose 已引用该文件（env_file），追加一行 ro 挂载不引入新暴露面（容器进程 env 本就持有同文件全部生效值）。

**挂载面权限对齐（评审 1 轮修正）**：bind mount 保留宿主属主与权限——部署机 hub.env 实测 0600 属主部署用户（uid≠1001），而容器进程 `user: "1001:1001"`（docker-compose.yml），uid 1001 打开恒 EACCES：照挂不对齐则 R1 逐轮 PermissionError、检测名存实亡。部署对齐行：`chown "$(id -un)":1001 hub.env && chmod 0640 hub.env`（文件属组对齐容器 gid，思路同 docker socket 的 `group_add` 属主对齐、方向相反；宿主 gid 1001 被占用时改 ACL `setfacl -m u:1001:r hub.env`，保留 0600 基面）。安全论证：新增可读方恰是 hub 进程自身（env_file 在容器创建时已把生效值注入同一进程），不扩大信任域；compose CLI 以宿主属主身份读 env_file，不受影响。**「挂载了但读不了」是独立可报告状态**：intent 路径已配但打开失败（EACCES 等）≠ 逐规则静默吞——一次性高声告警（含对齐修法，进 MM）+ 心跳行标注「R1 降级（intent 不可读）」，绝不逐轮刷异常。

### D2 检查器落点：daemon 第三低频槽（stale_check / 探针同型）
- `daemon.run_forever` 已有两个 N-tick 低频钩：stale_check（`tick % cfg.stale_check_ticks`）与 runner 池探针（`tick % cfg.probe_ticks` → `lc.probe_runners()`），各自整体 try/except 兜底（「观察面绝不崩执行面」）。看门狗 = **第三槽** `tick % cfg.drift_ticks == 0 → lc.check_config_drift()`，同型兜底。
- 逻辑归新模块 `hub/config_drift.py`，`Lifecycle.check_config_drift()` 只做薄挂点装配（`probe_runners` → `health_probe.probe_enabled_runners` 同构先例）；cfg 增 `drift_ticks`（`HUB_DRIFT_TICKS`，默认 720 ≈ 5s tick 下每小时，≥1 整数 fail fast——stale/probe 同型校验，0 炸 modulo 教训）与 `drift_env_intent`（`HUB_DRIFT_ENV_INTENT`，Path|None；None = R1 关闭零行为——`image_template_repo` 不配即检测关闭的先例）。
- 周期取每小时而非更密：漂移是低频 ops 事故（分钟级发现无增益），且 R3 有出站 HTTP、R4 有 docker exec，开销对齐 stale_check 的 gh 调用。

### D3 最小断言规则集（四条，全部零误报设计）
**R1「L3 意图 vs 快照」**：intent 文件逐键（`KEY=VALUE`，# 注释/空行跳过，= 后取字面值——compose env_file 语义）vs hub 进程 env；值不等或进程 env 缺键 = 漂移项「盘上 hub.env 已改、容器未重建」，修复指引 `docker compose up -d`。边界：compose `environment:` 覆盖键（现状 HUB_DATA_ROOT / HUB_TICK_INTERVAL，均不在 hub.env 内）与文件键无交集；若运维把同键写进 hub.env 会被报——报文指明「compose environment 覆盖」，这是部署配错，报是对的。
**R2「L4 vs L3 兜底源」**：仅 `cfg.user_id` 的 vault 记录（会话用户记录与 hub.env 无对应关系，不比对）。**比对键分两档（评审 1 轮修正）**：
- **凭据键**（ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / GH_TOKEN / GITLAB_TOKEN / GITCODE_TOKEN）双侧共有且值不等 = 漂移项「vault 旧快照：派发正走 vault 旧值（vault 优先），改 hub.env 不生效即此因」，修复指引 `vault-import --from-env`（merge 幂等）。零误报依据：cfg.user_id 记录的写入源只有 vault-import（面板录入以本人 mm_user_id 为 key 写另一键空间——spec: hub-credential-vault「会话用户凭据录入」），凭据键分歧只能是「导入后 hub.env 又改」。
- **模型键**（vault ANTHROPIC_MODEL ↔ env HUB_USER_MODEL）双侧不等 = **信息项，不进漂移项**：vault 模型按派发既有语义是「用户显式选择，优先于 hub 级缺省」（`lifecycle._credentials` #56 修复注释明文——恒覆盖曾致面板 model 框失效），与 hub.env 缺省不同是完全合法状态；hub 机械不可辨「显式选择」与「导入早于 hub.env 改模型」，按零误报原则降档。信息项报文中性：注明派发以 vault 值优先（合法）、确系旧快照可重导，并**警示 vault-import 的 merge 是 env 非空值覆盖——重导会以 hub.env 值覆盖该显式选择**（cli.py merge 实现锚点），面板找回需重录入。
- **单侧独有键不报**：vault 有而 env 无 = merge「只增不减」by-design 保留；env 有而 vault 无 = 派发走兜底注入（行为等于意图）。vault 已配但无该用户记录 = 信息项（派发恒走 env 兜底 + warn）。vault 未配置（master key 缺）= env-only 合法形态，R2 整体关闭零报。
事故主因（L4 旧值 + 改了无数次的 L3）正是本条可断言面。
**R3「L5 membership（三态）+ alias 形态预检」**：消费面模型名集合 = hub 进程 env 的 `PA_PERSONA_MODEL_*` / `PA_DEV_MODEL`（`lifecycle` 的 `PA_*` 透传面）+ 派发源 `ANTHROPIC_MODEL`（vault 优先、`HUB_USER_MODEL` 兜底——**与 `_credentials` 同序解析，绝不另立真相源**）；base_url/token 同源解析。探测 `GET {base_url}/v1/models`：200 → 逐名**精确匹配（大小写敏感）**——roc 认 Qwen3.8-27B 不认 qwen3.8-27b-fast 的分家实证，精确性就是断言点；缺名 = 漂移项，修复指引「LiteLLM admin API 注册/修正（/model/new 需 master key = litellm 容器 env LITELLM_MASTER_KEY）」。连接错/超时/5xx → UNKNOWN 不入漂移项（瞬时，stale_check 三态同判）；401/403 → UNKNOWN + 一次性降级提示（virtual key 限 llm_api_routes，列表路由可能不在内）。**alias 负向预检**（纯形态零网络）：将进容器的模型值 ∈ claude CLI 常量 alias 集 {haiku, sonnet, opus, fable} = 漂移项（容器 CLI 把 alias 本地解析成裸 Anthropic id → 网关 400，run 93 实证；容器值须裸模型名）。与 per-agent-model-routing「代码层 MUST NOT 维护 alias 白名单」约束的关系：禁的是**正向合法性白名单**（随 roc 演化腐化）；本判据是**负向必死黑名单**——四 alias 是 claude CLI 常量不随 roc 变，封闭集不腐化，方向相反不冲突。
**R4「L6 在场 + 形态」**：存在 running run 时取最老一行（`db.runs_in_status("running")`），按行内快照 `runner_docker_host` 路由 `docker exec <container_ref> printenv`（tick 对账同口径路由；`docker exec printenv` 是 RUNBOOK 已验证的 L6 检查通道），断言必需键在场（ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN）且形态合法（复用 `vault_store.placeholder_issues` 纯形态判据）；缺键/形态非法 = 漂移项（注入链断裂）。**不比对具体值**：env-file 即弃已删、vault 跑中变更是 by-design 快照语义（spec「跑中 vault 变更不影响在跑容器」），值比对必假阳。exec 失败/容器已终态 → 跳过本轮（1:N 即弃常态，不误报）。
取舍记录：更深的 L6 断言（派发时 env-file sha256 摘要入 run 行、检查时重算比对）可做真值比对，但引入 DB 列 + 派发路径改动，超出最小起步；若 R4 在场断言实战不够，再立后续 change。

### D4 报告面选型：MM 私信（notify_admin 复用）+ stderr；不建日报
- 通道：漂移项经 `Notifier.notify_admin`（管理面告警、无 project 归属、fail-open——镜像陈旧检测同通道同语义）；未配 bot = 降级 stderr（可见的丢，对齐既有降级）。每轮 stderr 一行心跳摘要（全绿也记——看门狗活着的证据）。
- **报告三分（评审 1 轮补）**：① 漂移项（真层间分歧，需人工修复）→ MM 私信 + stderr，去抖一次；② 前提故障告警（R1 intent 面挂载但不可读——看门狗自身部署前提坏）→ MM 私信一次性 + 心跳行标注降级；③ 信息项（R2 vault 无记录 / 模型键分歧——合法或免动作状态）→ **仅 stderr 去抖，不进 MM 私信**（值班私信只承载真漂移，防告警疲劳）。
- 去抖 + 消解：同漂移项（规则 + 键名）进程生命周期至多报一次（内存 seen，重启重发可接受——stale_check 去抖同款）；当轮零漂移且 seen 非空 → 报一次「已消解（N 项）」并清 seen。比 stale 的只报不清进一步：漂移是可修复态，消解回执是排障闭环终点。
- **值绝不入面**：报文只含规则/层对/键名/长度差（`placeholder_issues`「只回显键名/长度」先例——BASE_URL 是误贴 token 的常见通道，片段也不进日志/通知）；R4 的 printenv 输出仅在内存断言，不落日志。
- 不选日报：hub 无日报机制（pa `run_daily` 的 report 是容器内执行产物，与 hub 观察面不同轴）；MM 私信已达值班即时性。不做面板节：观察面已有 stderr + MM，面板化留 follow-up。

### D5 fail-open 语义（观察面纪律全承）
- 绝不进派发路径：`_credentials` / `tick_dispatch` / env-file 写面零改动——`check_config_drift` 只读（`get_user_env` / `os.environ` / intent 文件 / HTTP GET / `docker exec printenv`）。
- daemon 槽整体 try/except 兜底 + 模块内**逐规则**吞异常（R3 网络挂不连坐 R1/R2 本地比对；R4 exec 失败不连坐 R3）。
- 关闭态零行为：intent 路径未配 → R1 关 + 一次性 warn；vault None → R2 关；无可解析凭据 → R3 跳过；无 running run → R4 跳过。整体卸载（不部署本 change）不影响任何既有行为。

### D6 vault 第三读点（spec 修改点）
hub-credential-vault「原子写与读取面」现定读取面仅两个（派发解密 + bootstrap 导入）。R2/R3 需读 `cfg.user_id` 记录——同用 `get_user_env` 原语（无新查询形态、无枚举面、「列出全部明文」禁令不动），但读取面计数 2→3 是契约变化，MODIFIED requirement 显式落档；会话用户其他记录不读（最小面）。

## Risks / Trade-offs

- [intent ro 挂载两段前提：未挂载 / 挂了读不了] → 未挂载 = R1 关 + 一次性 warn（零误报零行为）；挂载但不可读（bind mount 保留宿主属主：0600 属主 uid≠1001 vs 容器 user "1001:1001" → EACCES）= 独立前提故障告警（MM 一次性 + 心跳标注「R1 降级」），绝不逐轮 PermissionError 刷屏；权限对齐行（`chown "$(id -un)":1001 hub.env && chmod 0640`，宿主 gid 1001 被占时 `setfacl -m u:1001:r`）进 compose 注释与 RUNBOOK 部署节
- [/v1/models 在 virtual key 下可能恒 403] → R3 membership 降 UNKNOWN + 一次性提示，alias 预检与 R1/R2/R4 不受影响（R3 是唯一外部探测）；apply 期真机核实（Open Questions）
- [R1 报 compose 覆盖键类「误报」] → 现状无交集；真发生时报文指明双侧来源，修复 = 从 hub.env 删键——部署配错被抓住是目的不是副作用
- [告警疲劳] → 全规则零误报设计 + 去抖一次 + 消解才清——同项不刷屏；UNKNOWN 态完全不进漂移项
- [R4 exec 打扰运行容器] → printenv 只读零侵入；每小时至多一次、至多一个容器（最老 running）
- [报文泄漏凭据] → 值不入面纪律（键名/长度/规则 id）；R4 printenv 输出不落日志

## Migration Plan

1. 部署即生效：无 DB 迁移、无新必需 env；compose 增一行 ro intent 挂载（可选，不挂 R1 关）+ `up -d` 重建（本变更自身就示范 L3 写法）。
2. 回滚：旧代码不读新键/新模块，残留挂载与 env 键无害。
3. RUNBOOK：新增「配置六层漂移模型」节（层表 + 各层正确读/写方式 + 2026-10-31 事故案例）+ hub 部署节补 intent 挂载行。

## Open Questions

（不阻塞起案）R3 探测端点在 virtual key（llm_api_routes 限定）下的实际可达性——apply 期真机核实 `/v1/models` 是否可返回列表；若恒 403，R3 降级为「alias 形态预检 + 网关不可达 UNKNOWN」并评估 master-key 侧探测替代（`LITELLM_MASTER_KEY` 走运维通道自查，不进 hub——master key 的暴露面必须停在 litellm 容器）。
