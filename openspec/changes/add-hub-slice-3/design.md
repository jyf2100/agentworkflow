## Context

服务器执行链（切片 1-2：队列/排度/容器/OAuth 身份）已落地，但缺合法喂入口——PRD 产出在 vault（mac cron），执行在服务器 hub，两机间无交接协议。F11 裁决（design doc §9 L147）：投递 API = state ownership 交接协议，`--to-stage` 物理分家 + 独立投递器 + hub 幂等端点；F12 裁决：critic「有据 = 可追溯依据」单标准。约束：durable-loop-state「Immutable PRD source」（PRD 产出后不可变）；execution-hub「F10 写面钉死为仅 meta 署名文件」（本设计 D6 扩展它）；fail-safe 三态不变式推广到投递响应。

本体论审核修正（2026-10-22，四位专家 + 核实）：①`--to-stage` 是 run_daily 既有参数（初始 commit 即有，`default="dispatch"`，cron 显式 `--to-stage report`）——D5 原表述「argparse 新参数」失实，已改写；②执行链原方案「`--from-stage critic` 直达落盘 PRD」断链（manifest 无人生产：`_manifest_disk_fallback` 只认 inject/prd 段产物，critic 空 manifest 静默空 gate）——D6 选定案改为复用既有 inject 桥。

## Goals / Non-Goals

**Goals:**
- mac cron 只跑前半段（fetch→radar→prd），执行链权威唯一且物理保证
- PRD 交接 exactly-once：全崩溃窗口零协调机制收敛（重扫重投 + db/文件双层幂等去重）
- vault 视角 PRD 生命周期 `produced → delivered` 止步，结果回流不做（vault 不需要知道）
- critic 单标准把关，服务器手写 PRD（切片 4 入口）与自动 PRD 同闸

**Non-Goals:**
- 手写 PRD 面板/Mattermost 提交入口、evidence 字段机械必填校验（切片 4）
- report 段产物服务器落卷的 vault 可见性（T2a 张力未裁，不阻塞）
- 投递批量化/并发（日量级个位数，串行够）
- 镜像 build 自动化（F13 已裁手动 YAGNI）

## Decisions

### D1: PRD 传输 = 正文内嵌 POST body，sha256 声明值 + hub 落盘前校验
vault 与 hub 异机，路径引用无效。body = `{project, filename, sha256, content}`（UTF-8 JSON）。sha256 由 vault 计算传入，hub 落盘前校验 `sha256(content) == 声明值`（不匹配 400 确定性拒绝）——幂等键的 sha 是声明值，校验防传输损坏 + 防键撒谎。body 上限 1MB（超限 400）。弃案：传路径（跨机无效）；两阶段先传后拉（复杂度无收益）。

### D2: 幂等键存储 = queue_items 加 `deliver_key` 列 + UNIQUE 索引
幂等键生命周期 = 队列项生命周期，同表最短路径。`db.py` 复用 2b 的 `_migrate` ALTER 模式（2a/2b 既有库不丢）。`deliver_key = "{project}:{filename}:{sha256}"`，UNIQUE 索引使并发撞键在 db 层兜底（IntegrityError → 查既有项返回重复响应）。文件层另有同名同容幂等复用（见 D6/a-1 修复：db 层去重命中时校验落盘文件存在且 sha 一致，缺失/异容补写——防「db-first 崩溃窗口产生队列项存在但 PRD 永不补写」的不可恢复态）。弃案：独立 deliver_keys 表（生命周期分叉——队列项终态后键悬空，语义混乱）。

### D3: delivered 标记 = vault 侧独立 append-only ledger
「Immutable PRD source」钉死 PRD 产出后不可变 → 标记不能写进 PRD 文件。`<state_dir>/deliver_ledger.jsonl`：一行 `{project, filename, sha256, delivered_at}` 或死信行 `{status:"failed", error_class, ...}`；扫描 = PRD 产出集 − ledger delivered 集。append-only jsonl 半行损坏可跳过（该 PRD 视为未投递重投，hub 幂等收敛——与 D4 同一收敛闭环）。弃案：改 PRD frontmatter（违反 immutable）；per-PRD sidecar 文件（N 文件散落无单审计面）。

### D4: 投递三态 = 确定性成功才标记；UNKNOWN 重投（带计数上限）；4xx 死信
- **2xx + 回执** → 写 ledger delivered；
- **UNKNOWN**（超时/连接断/未收响应/5xx 类不可判）→ 不标记，下轮重扫重投（spec 钉死）；同一 PRD 连续 UNKNOWN 超 K=5 次 → 转 failed 死信行（error_class=unknown_exhausted）+ 告警——防确定性 5xx（handler bug 等）无界重投且零持久痕迹（无静默失败）；
- **确定性 4xx 拒绝**（sha 不匹配/body 超限/格式坏/401/404/405）→ 不标记且记 `failed` 死信行（带 `error_class`：`data_4xx` 坏数据 / `config_4xx` 部署或凭据配错——后者高声告警，token 失配或 hub 未升级时批量死信须一眼可辨），不再自动重投，人工修后 `--retry` 显式重投。
弃案：全部重投（4xx/确定性 5xx 死循环噪声）；全部死信（UNKNOWN 是可恢复的，违背三态不变式）。

### D5: `--to-stage` = 既有参数复用，部署取值变更（report → prd）
`--to-stage` 是 run_daily 自初始 commit 即有的参数（`default="dispatch"`——不传时本就不跑 report；cron 经 `run_cron.sh` 显式 `--to-stage report`）。本 change **零代码改动**复用它为部署边界：roc `run_cron.sh` 的取值从 `--to-stage report` 改为 `--to-stage prd`（fetch→radar→prd 产出即停，正常退出），其后追加 `deliver_prd.py` 调用（先编排后投递，顺序串行）。默认值与既有行为零变化（测试钉死既有语义）。弃案：投递钩子嵌 run_daily（F11 弃案 b：delivered 写失败双跑风险 + 职责耦合）；独立 cron 行（两行时序耦合）。

### D6: hub 落盘位 = `<卷根>/<project>/prd/`；执行链 = 复用既有 inject 桥；写面钉死枚举式扩展（meta + 投递 PRD）
**执行链选定案（本体论审核翻案）**：容器内经**既有 inject 桥**消费投递 PRD——投递端点落盘 PRD 至 `<卷根>/<project>/prd/<文件名>`，创建队列项时其 PRD 路径即落盘位；派发照旧挂载 `/input/` + `--from-stage inject --inject-prd`（lifecycle 既有机制零改动），inject 段（纯机械、零 LLM：读挂载 md → 校验 profile → 规范化落 `state/prd/<project>/` → 产 `prd_manifest_<stamp>.json`）承接，critic 段经既有 manifest 拾取消费。原方案的「`--from-stage critic` 直达落盘 PRD」断链（manifest 无生产者——`_manifest_disk_fallback` 只认 inject/prd 段产物，critic 空 manifest 静默空 gate）；原弃案理由两条经核实均不成立：F11 原文（design doc L147）无「不经 inject」裁决；「多跑 inject 机械活」实为零 LLM 纯机械复制，「自动 PRD 降格手写通道」的身份顾虑已被 F12 critic 不看出身单标准消解。落盘顺序钉死 **file-first**（先落盘含 sha 校验、再持久化队列项；重复投递命中幂等键时校验落盘完整并补写）。
**写面冲突与裁定**：F10 写面钉死「仅 meta 署名文件」挡投递落盘。裁定：写面从单一枚举扩为**两类枚举**——① meta 署名（既有四元组 + 文件名模式）；② 投递 PRD（限 `<卷根>/<project>/prd/` 目录、文件名匹配 `^\d{8}(m\d*)?_[a-z0-9-]+(-[a-f0-9]{8})?\.md$`、sha 校验、同名同容复用/撞名异容 `-<sha8>` 后缀）。写面钉死的本体是「hub 无自由写 state」（防膨胀滑坡），枚举新增一类带模式校验的写不破本体——仍不存在通用写函数、仍拒绝一切模式外路径。容器内 pa 写自己 state（inject 规范化 + manifest）是 pa 既有写面，不属 hub 枚举管辖。落地 = `execution-hub` spec MODIFIED F10 requirement（本 change delta 已含）。弃案：inbox 目录 + 独立挂载（多一目录概念）；hub 派发时合成 manifest 文件（manifest 文件名含 stamp，投递时 stamp 未定——时序矛盾）。

### D7: 投递端点守卫 = 可选共享 token（HUB_DELIVER_TOKEN）
机机通道非人身份，MM OAuth 不适用。`HUB_DELIVER_TOKEN` 配置则要求 `Authorization: Bearer`（`hmac.compare_digest`）；不配 = 内网无守卫（对齐 GET 面现状）。对齐 F2 口径：公网/多租户暴露前 token + socket proxy 是硬门槛。token 失配 401 在投递器侧归 `config_4xx` 死信类高声告警（D4）。弃案：mTLS（部署重，量级不符）；OAuth client credentials（引身份概念，机机通道过度）。

### D8: critic prompt 适配 = 单点最小 diff + 镜像重建
`pa-prd-critic.md` 的「有据」判定段一处改写：可追溯依据 = source_path **或** PRD 自带依据链接/背景，删「无 source_path → 无据」隐含单通道，加「不看出身」。不动其他评审维度（可执行标准零变化）。spec scenario 双向钉（有自带依据放行 / 无任何依据仍拦）。**镜像重建硬约束（executor-image D8）**：persona prompt 打进 pa-executor 镜像，prompt 改动须切 template tag 重建镜像并升 `HUB_IMAGE_TAG_DEFAULT` 才对容器内生效——mac 侧本机路径即时生效，服务器侧不重建镜像则容器内仍是旧 prompt（静默不生效），重建步骤入 tasks。

## Risks / Trade-offs

- [写面扩展滑坡——「顺手多写一类」正是 F10 警惕的起点] → 两类均模式钉死 + 目录 guard 测试（模式外路径拒绝的测试钉死）；spec 枚举封闭，第三类写入须过 change 评审。
- [ledger 与产出漂移：PRD 重产同名异容] → 键含 sha256，同名异容 = 新键 = 合法重投（新 PRD），落盘 `-<sha8>` 后缀避碰。
- [投递 body 大/恶意] → 1MB 上限 + sha 校验 + token（配则线性比较防时序）。
- [token 失配 / hub 未升级 → 批量 401/404 死信] → 死信带 error_class，config_4xx 类高声告警（非零退出），一眼可辨部署错而非坏数据。
- [mac 未升 --to-stage prd 前投递器已跑 = 双执行] → 部署顺序钉死：服务器端点先上（幂等无害），mac `--to-stage prd` 与 `deliver_prd.py` 同一 cron 变更启用（物理分家与投递同时生效，无双跑窗口）。
- [回退期双执行权威并存] → 回退须按 RUNBOOK §15 完整序列（停投递 → drain running → 清 pending → 回退 cron → 当日已投 PRD 补跑或明确放弃）；「服务器与本机执行互不干扰」不可依赖——两侧写同一目标仓，仅有的交汇去重（GitHub `already_dispatched`）只在对端已推送后生效，in-flight 双跑仍会撞 branch 命名空间。

## Migration Plan

1. hub 侧端点 + db 迁移 + 清队列原语上服务器（纯新增，不配 token = 内网无守卫，现有调用零影响）；
2. pa 侧 `deliver_prd.py` + critic prompt + 镜像重建升 tag 入 main；
3. roc `run_cron.sh` 一次变更：`--to-stage report` → `--to-stage prd` + 追加 deliver 调用（D5 部署顺序钉死）。
回退完整序列（RUNBOOK §15）：①停投递（cron 去 deliver 行）②drain running（等 running 队列项终态或停容器——「清 pending」不覆盖 running，mac 与服务器并发双跑同一 PRD 会撞目标仓 branch 命名空间）③清服务器 pending 队列项（hub 清队列原语）④回退 cron `--to-stage prd` → `report` ⑤当日已投未执行 PRD 于 mac `--from-stage inject --inject-prd` 逐份补跑，或明确放弃并记录（ledger 已 delivered，不补跑则这些 PRD 两侧都不执行 = 静默丢失）。

## Open Questions

- 无阻塞项。T2a（report 落服务器卷的 vault 可见性）登记未裁，切片 4 一并处理。CONTEXT.md 术语表补 deliver 词族（deliver_key / deliver_ledger / delivered 标记 / 死信 / error_class）随 tasks 5.5 落地。
