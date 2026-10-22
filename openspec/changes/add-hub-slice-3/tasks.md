## 1. --to-stage prd 既有行为回归钉死（D5，零代码改动）

- [x] 1.1 回归测试：`test_run_daily.py` 钉死 `--to-stage` 既有语义——`--to-stage prd` 跑到 prd 产出即正常退出（critic/dispatch/report 不执行、无其产物、journal 完整前半段）；默认值 `dispatch` 行为照旧（不传时 report 不跑）；`--from-stage` × `--to-stage` 区间组合语义不变。既有参数无新实施项，测试防未来回归即可
- [x] 1.2 回归：`bash scripts/quality.sh` 全绿（零扰动实证——本组不改动产码）

## 2. hub 幂等交接端点（D1/D2/D6/D7）

- [x] 2.1 RED：`test_db.py` 补 deliver_key 测试——`queue_items` 加 `deliver_key` 可空列 + UNIQUE 索引；2a/2b 既有库 ALTER 迁移不丢队列；撞键 IntegrityError → 查既有项返回
- [x] 2.2 GREEN：`db.py` `_SCHEMA` 加列 + `_migrate` ALTER（复用 2b initiator 列模式）+ `enqueue` 带 `deliver_key` 参数
- [x] 2.3 RED：`test_api.py` 补 `POST /deliver` 测试——首次投递 2xx **先落盘再入队**（file-first：落盘成功才建队列项）；同键重复 2xx 返回既有标识标记重复**且校验落盘文件存在、sha 一致（缺失/异容先补写）**；落盘命中同名同容幂等复用不重写；sha256(content) ≠ 声明值 400；body >1MB 400；`HUB_DELIVER_TOKEN` 配置时无/错 Bearer 401、不配 = 无守卫；落盘写面模式钉死（`<卷根>/<project>/prd/` 外路径拒绝、文件名 regex `^\d{8}(m\d*)?_[a-z0-9-]+(-[a-f0-9]{8})?\.md$` 外拒绝）；同文件名异容落盘 `-<sha8>` 后缀；落盘零触碰 journal/其他 state；队列项 PRD 路径 = 落盘位
- [x] 2.4 GREEN：`api.py` 加 `POST /deliver`（token 校验 `hmac.compare_digest`、sha 校验、file-first 落盘 + db/文件双层幂等去重、模式钉死落盘写入口、经 `validate_and_enqueue` 建队列项）
- [x] 2.5 RED+GREEN：清队列原语（回退序列运维依赖）——hub 补删除 pending 队列项的 API/CLI 原语（如 `DELETE /queue/{id}`）+ 测试（仅 pending 可清、running 拒绝并提示先 drain、留审计记录）
- [ ] 2.6 冒烟（real-docker 可选）：投递 → 队列 pending → 派发容器内 `--from-stage inject --inject-prd /input/<name>`（lifecycle 既有机制零改动）→ inject 规范化产 manifest → critic 消费全链走通

## 3. vault 投递器 deliver_prd.py（D3/D4）

- [x] 3.1 RED：`test_deliver_prd.py`——扫描 = PRD 产出集 − ledger 集（已 delivered 跳过）；2xx 回执写 ledger delivered 行；UNKNOWN（超时/连接断/5xx）不写、下轮重投、**同一 PRD 连续 UNKNOWN 超 K=5 转死信行（error_class=unknown_exhausted）+ 告警**；确定性 4xx 写 failed 死信行不再自动重投、**带 error_class（data_4xx / config_4xx），config_4xx（401/404/405）高声告警非零退出**；`--retry` 显式重投 failed 项；串行逐个；ledger 半行损坏跳过（视为未投递）
- [x] 3.2 GREEN：`scripts/deliver_prd.py`（独立脚本独立退出码；ledger = `<state_dir>/deliver_ledger.jsonl` append-only；hub 地址/token 经 env `PA_HUB_URL` / `PA_DELIVER_TOKEN`）
- [x] 3.3 回归：`bash scripts/quality.sh` 全绿

## 4. critic 有据单标准适配（F12，D8）

- [x] 4.1 `.claude/agents/pa-prd-critic.md` 「有据」判定段单点改写：可追溯依据 = source_path **或** PRD 自带依据链接/背景，不看出身；其余评审维度（可执行标准）零变化
- [x] 4.2 验收：spec 双向 scenario 对照（自带依据无 source_path 放行 / 无任何依据仍拦）逐条核 prompt 文本；如仓库有 prompt 快照测试则同步更新（核实：test_run_persona_contract / test_graph_critic 等仅引用 persona 名与调用契约，无 prompt 文本快照——零同步）
- [ ] 4.3 镜像重建（executor-image D8 硬约束）：persona prompt 打进 pa-executor 镜像——切 template tag 重建镜像、升 `HUB_IMAGE_TAG_DEFAULT`，否则服务器容器内仍是旧 prompt（mac 本机即时生效，容器侧静默不生效）【部署期动作：依赖 template tag checkout + 服务器 docker build，步骤入 RUNBOOK §15 部署顺序；本仓无代码变更点】

## 5. 收口与运维面

- [x] 5.1 `hub/deploy/hub.env.example` 补 `HUB_DELIVER_TOKEN` 注释态（不配 = 内网无守卫；公网暴露硬门槛对齐 F2）
- [x] 5.2 `Projects/项目推进流水线/RUNBOOK.md` 追加 §15 投递协议运维节——**完整回退序列**：①停投递（cron 去 deliver 行）②drain running（等 running 队列项终态或停容器；「清 pending」不覆盖 running，双跑撞目标仓 branch 命名空间）③清服务器 pending 队列项（2.5 原语）④回退 cron `--to-stage prd` → `report` ⑤当日已投未执行 PRD 于 mac `--from-stage inject --inject-prd` 逐份补跑或明确放弃并记录（不补跑则两侧都不执行 = 静默丢失）；另含部署顺序（hub 端点先上、mac `--to-stage prd` + deliver 同一 cron 变更启用防双跑窗口）、token 配置、ledger/死信排查（error_class 分类）
- [x] 5.3 `scripts/run_cron.sh` 注释块：`--to-stage prd` + `deliver_prd.py` 调用样例（注释态 + roc 启用指引——物理分家启用是 roc 侧部署动作，本机不替启）
- [x] 5.4 `Projects/项目推进流水线/CONTEXT.md` 术语表补 deliver 词族：deliver_key / deliver_ledger / delivered 标记 / 死信 / error_class / file-first（F3 债，随本 change 落）
- [x] 5.5 全量验证：pa `quality.sh` + hub `bash hub/quality.sh` + `openspec validate`（spec-driven schema 全过）——三级全绿（pa compile+pytest+ruff ✓ / hub compile+pytest+ruff ✓ / openspec valid ✓）
