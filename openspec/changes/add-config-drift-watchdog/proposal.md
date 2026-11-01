## Why

2026-10-31 真机事故实证了模型路由/凭据的**六层漂移模型**——同一逻辑配置在六层各自独立存在、互不知晓：L1 本机 `~/.claude/settings.json` env 块（本机 CLI 直调形态）、L2 `Projects/项目推进流水线/config/model-routing.json`（本机形态路由主点，`.dockerignore` 固化排除出镜像——run 93 教训）、L3 `hub/deploy/hub.env`（compose env_file，**仅容器创建时读入**，restart 不重读）、L4 hub 凭据保险箱、L5 roc LiteLLM 网关注册面、L6 run 容器实况（最终生效面）。事故链 = L4 停留旧快照（派发 vault 优先 → 改 hub.env 不生效）+ L3 改后只 restart 未 `up -d` 重建 + L5 死链（qwen3.6-35b→8896）三者叠加 → run 全挂且「改了无数次都没改回来」——每层单看都对，缺的是**层间一致性断言**；排障靠人肉 `docker exec printenv` 逐层对表。本 change 把层间不一致变成看门狗主动可见的漂移项：只报告 + 修复指引，绝不自动修复、绝不阻断派发。

## What Changes

- **六层漂移模型成文**（design 层表：各层载体/消费形态/正确写法——L3 改后必须 `compose up -d` 重建、L4 用 `vault-import --from-env`（merge 语义幂等）、L5 走 admin API `/model/new`（需 master key，virtual key 只能 llm_api_routes）、L6 无直写修上游重派）；L1/L2 明确排除出断言范围（hub 容器不可达 + 容器形态按 spec 有意不读 L2）
- **最小断言规则集 4 条起步**（全部机械判据、零误报设计）：R1 L3 意图文件 vs 运行快照（「改了 hub.env 没重建」——挂载面须宿主属主对齐，容器 uid 1001 直读 0600 恒 EACCES；不可读形态一次性前提告警非刷屏）；R2 L4 vault 记录 vs L3 兜底源（凭据键「vault 旧快照」——派发 vault 优先，正是「改了没生效」主因；模型键分歧降信息项，保护面板显式选择不被「旧快照」误报与 merge 覆盖误伤）；R3 L5 网关模型注册 membership（三态 UNKNOWN 不误报）+ claude alias 负向黑名单预检（容器形态必死形态，run 93 类）；R4 L6 运行容器凭据键在场 + 形态合法（注入链断裂可见）
- **检查器落点 = daemon 第三低频槽**（stale_check / runner 池探针同型 N-tick 计数，`HUB_DRIFT_TICKS` 默认 720 ≈ 每小时），逻辑归新模块 `hub/config_drift.py` + `Lifecycle.check_config_drift()` 薄挂点（`probe_runners` 同构）；fail-open 纪律全承——观察面绝不崩排度循环、绝不进派发路径
- **报告面只报漂移项**：每项 = 规则 + 层对 + 键名（+长度差）+ 修复指引，**值绝不入面**；通道复用管理面告警 `notify_admin`（MM 私信，fail-open）+ stderr 降级；同项去抖一次、消解回执一次；不建日报/面板面
- **部署面**：compose 把 hub.env 以 ro 挂入容器作意图面（`HUB_DRIFT_ENV_INTENT`，不配 = R1 关闭零行为）+ 宿主侧属主对齐（`chown "$(id -un)":1001 + chmod 0640` 或等效 ACL——bind mount 保留宿主属主，容器 `user: "1001:1001"` 直读 0600 恒 EACCES）；`hub.env.example` 两键 + RUNBOOK 六层模型节与权限对齐行

## Capabilities

### New Capabilities

- `hub-config-drift-watchdog`：六层漂移模型的可观测性契约——断言范围（hub 可达四层）、最小断言规则集（R1–R4）、fail-open 检查器挂点（daemon 低频槽）、漂移报告面（只报漂移项 + 修复指引 + 值不入面）
  - 归属理由：看门狗是横切 L3–L6 的纯观察面，不属 execution-hub（排度/容器生命周期机械面）也不属 per-agent-model-routing（路由配置语义面——其容器形态条款是本 change 的被引用约束而非改动对象）任何单一既有 capability 的职责面；同型先例 = 镜像陈旧告警归 hub-notifications（周期检测 + 三态 + 去抖的观察面独立成 capability），配置一致性是与镜像版本不同的关注轴，故立新 capability 而非塞进 execution-hub

### Modified Capabilities

- `hub-credential-vault`：「原子写与读取面」读取面 2→3——新增配置漂移看门狗的派发源核对读点（仅 `get_user_env` 原语、仅配置单用户一条记录；「列出全部明文」禁令与最小 API 面约束不动）

## Impact

- **代码**：新 `hub/config_drift.py`（规则集 + 三态 + 去抖/消解 + http/docker 注入接缝）、`hub/config.py`（`drift_ticks` / `drift_env_intent` 两键，stale/probe 同型校验）、`hub/daemon.py`（第三低频槽 + try/except 兜底）、`hub/lifecycle.py`（`check_config_drift` 薄挂点）、新 `hub/test_config_drift.py` + `test_daemon.py` 增量
- **契约**：派发与凭据解析语义零改动（`_credentials` / env-file 写面不碰——看门狗另读不改）；vault 读取面 +1 读点（同原语，spec 显式落档）；不动 ContainerRunner Protocol（R4 走 subprocess docker CLI，health_probe 同型）
- **部署**：`hub/deploy/docker-compose.yml` 增一行 ro intent 挂载（可选）；`hub/deploy/hub.env.example` 两键注释；RUNBOOK 新增「配置六层漂移模型」节（层表 + 正确写法 + 2026-10-31 事故案例）
- **测试**：R1–R4 各分支（fake intent 文件 / 真构造 vault / FakeGateway / FakeRunner）、三态分流、去抖与消解回执、fail-open（检查器异常不崩循环）、config 校验拒起
