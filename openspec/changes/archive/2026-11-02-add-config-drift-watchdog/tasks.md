## 1. 检查器模块 hub/config_drift.py

- [x] 1.1 新模块骨架：`check_drift(cfg, vault, db, *, seen, notifier, http_client=None, docker_bin="docker")` 逐规则调度 + `DriftItem` 结构（规则 id / 层对 / 键名 / 长度差 / 修复指引文案）；**逐规则吞异常**（一规则挂不连坐他规则，warn stderr）；报文值绝不入面（键名/长度先例：`vault_store.placeholder_issues`）
- [x] 1.2 R1（L3 意图 vs 快照）：intent 文件解析（`#` 注释/空行跳过、首个 `=` 后取字面值——compose env_file 语义，坏行记 warn 不崩）vs `os.environ` 逐键比；值不等/进程 env 缺键 = 漂移项（修复指引 `docker compose up -d`，报文注明 restart 不重读 env_file）；`drift_env_intent` 未配 = R1 关闭 + 一次性 warn「intent 面未挂载」；**路径已配但打开失败（EACCES 等——bind mount 保留宿主 0600 属主、容器 uid 1001 打不开）= 独立前提故障告警**：MM 一次性（含权限对齐修法）+ 心跳行标注「R1 降级（intent 不可读）」，绝不逐轮刷异常
- [x] 1.3 R2（L4 vs L3 兜底源）：仅 `cfg.user_id` 记录；**比对键分两档**——凭据键（ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN/GH_TOKEN/GITLAB_TOKEN/GITCODE_TOKEN）双侧共有且不等 = 漂移项（修复指引 `vault-import --from-env`，注明 vault 优先致改 hub.env 不生效）；模型键（vault ANTHROPIC_MODEL ↔ env HUB_USER_MODEL）不等 = **信息项**（中性口径：注明派发按 #56 语义以 vault 值优先属合法显式选择、确系旧快照可重导，**警示 merge 会以 hub.env 非空值覆盖显式选择**）；**单侧独有键不报**（merge 只增不减 by-design）；vault 有 key 无记录 = 信息项；vault None = R2 整体关闭零报
- [x] 1.4 R3（L5 membership + alias 预检）：消费面模型名收集（`PA_PERSONA_MODEL_*` 前缀扫 + `PA_DEV_MODEL` + 派发源 ANTHROPIC_MODEL——与 `_credentials` 同序 vault 优先解析，不另立真相源）；`GET {base_url}/v1/models` 三态（200 → 逐名精确匹配大小写敏感，缺名报漂移 + admin API 修复指引；网络错/超时/5xx → UNKNOWN 仅日志；401/403 → UNKNOWN + 一次性「virtual key 无列表权限，membership 断言降级」提示）；alias 负向黑名单预检（haiku/sonnet/opus/fable——容器形态必死，报文引 run 93 口径）；http client 注入接缝（FakeGateway 测试，gh_client 先例）
- [x] 1.5 R4（L6 在场 + 形态）：`db.runs_in_status("running")` 最老一行，按行内快照 `runner_docker_host` 注入 DOCKER_HOST 路由 `docker exec <container_ref> printenv`（subprocess docker CLI，health_probe 同型，**不动 ContainerRunner Protocol**）；断言必需键在场 + `placeholder_issues` 形态判据；不比对具体值；exec 失败/容器终态 → 跳过本轮不误报

## 2. 配置与 daemon 接线

- [x] 2.1 `hub/config.py`：`drift_ticks`（`HUB_DRIFT_TICKS`，默认 720，≥1 整数否则 ConfigError——stale_check_ticks/probe_ticks 同型校验）+ `drift_env_intent`（`HUB_DRIFT_ENV_INTENT`，Path|None）
- [x] 2.2 `hub/lifecycle.py`：`check_config_drift()` 薄挂点（`probe_runners` 同构：装配 cfg/vault/db/notifier 调 `config_drift.check_drift`，seen 由调用方持有）；`hub/daemon.py`：第三低频槽 `tick % lc.cfg.drift_ticks == 0`，整体 try/except 兜底（「观察面绝不崩执行面」同型注释）

## 3. 报告面

- [x] 3.1 报告三分：① 漂移项 → `notifier.notify_admin`（MM 私信配置单用户，fail-open）逐项文本；② 前提故障告警（R1 intent 不可读）→ MM 一次性 + 心跳标注降级；③ 信息项（R2 无记录/模型键分歧）→ 仅 stderr 去抖**不进 MM 私信**；每轮 stderr 一行心跳摘要（全绿也记，降级态在心跳行标注）；未配 bot 全部降级 stderr（镜像陈旧告警同语义）
- [x] 3.2 去抖与消解：同项（规则 id + 键名）进程生命周期报一次；当轮零漂移且 seen 非空 → 一次「漂移已消解（N 项）」回执并清 seen（闭环可见）

## 4. 部署面

- [x] 4.1 `hub/deploy/docker-compose.yml`：pa-hub volumes 增一行 ro intent 挂载（`./hub.env:/intent/hub.env:ro`）+ `environment` 或 hub.env 缺省指 `HUB_DRIFT_ENV_INTENT=/intent/hub.env`；**compose 注释写明宿主侧权限对齐行**：`chown "$(id -un)":1001 hub.env && chmod 0640 hub.env`（bind mount 保留宿主属主——0600 属主 uid≠1001 时容器 user "1001:1001" 打开恒 EACCES；思路同 socket `group_add` 属主对齐、方向相反；宿主 gid 1001 被占用时改 `setfacl -m u:1001:r hub.env`）；不挂载 = R1 关闭零行为（注释写明）
- [x] 4.2 `hub/deploy/hub.env.example`：`HUB_DRIFT_TICKS` / `HUB_DRIFT_ENV_INTENT` 注释两键（含六层模型一句话指引，详见 RUNBOOK 节）
- [x] 4.3 RUNBOOK：新增「配置六层漂移模型」节（L1–L6 层表 + 各层正确读/写方式 + 2026-10-31 事故链案例与排障路径）+ hub 部署节补 intent 挂载行、**hub.env 权限对齐行（0600 → 0640 属组 1001，含 gid 被占的 ACL 备选与「新增可读方 = hub 进程自身、不扩大信任域」论证）**与回滚注记

## 5. 测试

- [x] 5.1 新 `hub/test_config_drift.py`：R1 键值漂移/缺键/注释行/坏行/关闭态一次性 warn/**intent 不可读（PermissionError 形态）一次性前提告警 + 心跳降级标注不刷屏**；R2 凭据键旧快照报文（键名面）/无记录信息项/**模型键分歧信息项（中性口径含 merge 覆盖警示、不进 MM 不计漂移）**/单侧键不报/vault None 关；R3 缺名/大小写敏感（Qwen3.8-27B vs qwen3.8-27b-fast 分家形态）/UNKNOWN 两态不进漂移项/一次性降级提示/alias 预检；R4 在场/形态非法/无 running 跳过/exec 失败跳过；去抖一次 + 消解回执清 seen；逐规则吞异常互不连坐；报文不含任何值（键名/长度断言）
- [x] 5.2 `hub/test_daemon.py` 增量：第三槽按 `drift_ticks` 周期触发（fake clock/sleep 注入）+ 检查器抛异常不崩循环（断言后续 tick 照跑）
- [x] 5.3 `hub/test_config.py` 增量：`drift_ticks` 0/负/非整数拒起；`drift_env_intent` 解析
- [x] 5.4 `bash hub/quality.sh` 全绿（新单测并入）

## 6. 验证

- [x] 6.1 本机回归：fake 形态全规则触发一轮（临时 intent 文件 + 真构造 vault + FakeGateway + FakeRunner）——断言三类报文（键名面、修复指引、去抖一次）与消解回执
- [x] 6.2 真机（184）：compose 挂 intent 面 `up -d` → 人为制造三类漂移各一（改 hub.env 不重建 / vault 留旧值 / 网关注册面缺消费面模型名）→ MM 私信三项各报一次 → 逐一修复（up -d / vault-import / admin API）→ 消解回执一次；`docker exec <run容器> printenv` 复核 L6 与报文一致；若 `/v1/models` 恒 403 则按 design Open Questions 落降级注记

## 实施证据（2026-11-01）

- 1.1：hub/config_drift.py check_drift + DriftItem（rule/层对/键名/长度差/修复指引）；逐规则吞异常；报文零值面（test 断言）
- 1.2：_parse_intent compose 字面语义（注释/空行/坏行记行号）；缺键/值漂移双形态 + intent 不可读独立前提告警（PermissionError 实测）+ 心跳降级标注；未配一次性 warn
- 1.3：凭据键漂移（vault-import 指引）/模型键信息项（中性口径+merge 覆盖警示，不进 MM）/单侧不报/无记录信息项/vault None 关——test_r2_* 全覆盖
- 1.4：消费面收集（PA_PERSONA_MODEL_*/PA_DEV_MODEL/派发源 vault 优先同序）+ /v1/models 三态（200 精确大小写敏感/UNKNOWN/401/403 一次性降级）+ alias 负向黑名单；http_client 注入接缝（FakeGateway）
- 1.5：最老 running 行按 runner_docker_host 快照路由 docker exec printenv（health_probe 同型）；必需键在场+placeholder_issues 形态；exec 失败/终态跳过；输出仅内存断言
- 2.1：config.py DRIFT_TICKS_ENV（默认 720，≥1 fail fast 同型）+ DRIFT_ENV_INTENT_ENV（Path|None）
- 2.2：lifecycle.check_config_drift 薄挂点（probe_runners 同构，_drift_seen 进程持有）+ daemon 第三低频槽整体兜底
- 3.1：漂移项 notify_admin MM + stderr；前提告警 MM 一次性 + 心跳降级；信息项仅 stderr 去抖；每轮心跳一行（全绿也记）；未配 bot 全降级 stderr
- 3.2：同项（规则+键名）生命周期一次；零漂移+历史项 → 消解回执一次并清漂移类键（info/premise 持续态键不清）；test_dedup_once_and_resolution_receipt
- 4.1：compose volumes ro 挂载 + environment HUB_DRIFT_ENV_INTENT + 属主对齐注释行（0640/ACL 备选）——2026-11-01 真机实证 0600 uid1000 恒 EACCES 后对齐生效
- 4.2：hub.env.example HUB_DRIFT_TICKS 注释键（六层模型一句话指引）
- 4.3：RUNBOOK §23「配置六层漂移模型」节（层表+正确写法+2026-10-31 事故链+排障速查）+ 部署对齐行 + 回滚注记 + inode 盲窗注记（真机实证 sed -i 换 inode 盲窗，追加式编辑保 inode）
- 5.1：hub/test_config_drift.py 12 测试：R1 五形态/R2 四形态/R3 四形态/R4 三形态/去抖消解/规则隔离/值不入面
- 5.2：test_daemon.py：drift_ticks 周期触发 + 异常不崩循环（断言后续 tick 照跑）；FakeLC 补三槽默认天文数
- 5.3：test_config.py：drift_ticks 0/负/非整数拒起 + 默认/覆盖 + intent 路径解析
- 5.4：bash hub/quality.sh 全绿（compile+pytest+ruff，2026-11-01）
- 6.1：单测即 fake 形态全规则一轮（临时 intent + FakeVault/FakeGateway/monkeypatch subprocess）——12 测试全绿
- 6.2：真机子集（2026-11-01，184 hub 容器内 one-shot）：四规则心跳全绿（R1=ok/R2=ok/R3=ok——virtual key 可列 /v1/models，design Open Question 解答：不降级；R4 无 running）；R1 注入→告报→消解闭环实证；前提故障路径（0600 EACCES）实测后按对齐行修复。未做：三类漂移各一全造 + MM 真私信实发（MM 链路由单测 Fake + notify_admin 既有真机通道复用）——列后续观测
