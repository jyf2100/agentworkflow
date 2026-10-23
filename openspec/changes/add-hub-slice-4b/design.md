# design：add-hub-slice-4b

## 背景

切片 1-4a 落完执行主干与通知通道。本切片两件事：①面板（手写 PRD 入口 + 只读面——design doc §1/§3 面板职责的兑现）②语义事件出口（4a D2 推迟项——critic/PR/verify 事件可见化）+ 无 run 放弃告警（4a D2 同族推迟项）+ T2a 裁决落地（report 落卷）。MM bot 提交经 YAGNI 裁决不做（proposal 已记）。

## 决策

### D1 面板形态：服务端渲染纯 HTML，零新依赖
渲染 = `panel.py` 内 render 函数族（f-string 拼 HTML + `html.escape` 转义一切动态值——XSS 防线机械唯一），`HTMLResponse` 返回；样式一段内联 `<style>`（极简暗色表单+表格）。**否决 Jinja2**：面板页数 ≤5、模板复用面小，引入模板引擎换来的是新依赖 + 新转义纪律面（autoescape 边界）；f-string + 集中 escape helper 更符合 hub 最小依赖哲学。**否决 JS/前端构建链**：提交页纯 form POST（浏览器零安装目标的最小闭环）。会话守卫复用 `auth._session_identity`（面板 GET 未登录 302 到 `/auth/authorize`、POST 401——与 auth 群先例一致）。

### D2 提交路径：deliver 落盘面复用 + 会话身份
`POST /panel/prd`（form: `project`/`prd_content`/`evidence`/可选 `prd_filename`）→ ①会话身份（mm_user_id + initiator 显示名）②`evidence.strip()` 空 → 422 ③PRD 内容落盘 `data_root/<project>/prd/<stamp>_<safe_name>.md`——复用 deliver 的落盘函数（幂等键形态不取：面板人工提交无重放面，文件名 stamp 前缀天然唯一）④`engine.enqueue(project, prd_path, enqueued_by=mm_user_id, initiator=显示名)`。evidence 不写 PRD 正文（同事自己的 PRD 内容自治）——evidence 只做闸前非空校验，值不落盘不入队（最小数据面；hub 无理由存储）。**与 deliver 的关系**：同「内容 → 落盘 → 入队」机械链，不同守卫面（会话 vs 机机 token）与不同幂等策略（无 vs sha256 键）——两入口平行不合并端点，落盘 helper 抽公用。

### D3 事件出口：单向文件契约 + reconcile 增量读
**pa 侧**：`scripts/event_outlet.py`（writer：`append_event(state_root, run_stamp, event_dict)` ——JSONL append + fsync，对齐 journal.py 写纪律）+ 挂点三处（critic verdict 落定处 / dispatch PR 开出成功处 / verify verdict 落定处）——挂点在**既有 verdict/PR 事实已产生的代码位置**（读已有变量机械转发，零新语义）。出口路径 = `<state_root>/runs/<project>/events/<stamp>.jsonl`？——**否决**，与 journal 同目录平铺：`<state_root>/runs/<project>/<stamp>_<slug>.events.jsonl`（run 的所有产物聚在 run 文件族，hub 按 run 找文件零寻址逻辑）。vault 形态同写（无害统一，spec 已钉）。
**hub 侧**：`reconcile_running` 对每个 running run（其 run_dir 已知）读伴生 `.events.jsonl` 全量行 → 进程内存 `set[(stamp, seq)]` 去重 → 新行转发通知（文本 = 事实字段机械拼接；critic drop/verify 红 进频道 + 私信、其余仅私信——复用 4a 频道面）。**崩溃语义**：重启后 set 空 → 活跃 run 事件重发一遍（≤ 数条，可接受——4a「重启重告好过漏发」同判）；run 终态后文件不再读（读取面钉在 running 扫描集，spec 已钉）。**否决持久游标表**：违反 4a「通知零持久写面」不变式，重发成本极低。**契约防御**：`v != 1` 整文件跳过 + stderr 告警；未知 `event` 枚举行跳过记日志（spec: pa-event-outlet）。

### D4 无 run 放弃告警：fail_item 收敛点单挂
`queue_engine.fail_item` / lifecycle 的 create_run 前放弃路径（六调用点）不逐点挂——在**收敛 helper**（lifecycle `_abandon_item(item_id, reason)` 之类，若既有 fail_item 即是收敛点则包一层）挂 `notifier.notify_admin(f"[pa-hub] 队列项 #{id} 放弃：{project}——{reason 摘要}")`（fail-open；通道关闭 stderr 降级——对齐陈旧告警语义）。spec 只钉「无 run 行放弃 → 告警」行为，不钉实现挂点（收敛 helper 是实现选择）。

### D5 report 双写：env 化落点，缺省零变化
pa report 段：`PA_REPORT_DIR` 非空 → 报告 md 在原 `REPORT_DIR` 落点照写 + 该目录再写一份（同一内容两次 write，非 rename）。hub 起容器时挂 state 卷已含 state_root——`PA_REPORT_DIR=<CONTAINER_STATE>/reports` 进容器 env（lifecycle 构造 env 时追加；与凭据 env-file 分离——非凭据，走 docker -e 直传）。面板 run 详情页读 host 侧 `<state_root>/reports/项目推进报告_<stamp>.md` 渲染（run stamp ↔ 报告文件名对齐——run_dir 名即 stamp 前缀）。**否决改 REPORT_DIR 本体**：vault 形态行为必须零变化（spec 钉），双写是最小面。

### D6 MM bot 提交：YAGNI 裁决（记录）
design doc §9 F10 附近「同事面板/Mattermost 提交」的 Mattermost 半句不做：面板是第一入口（本切片落地）；bot 提交需消息监听/交互命令常驻面（新凭据权限 + 新故障面 + bot API 版本耦合），价值（免浏览器）撑不起。有真实需求再立 change（届时先裁决入口形态：slash command vs DM 交互）。

### D7 测试接缝（全离线）
- 面板：FastAPI TestClient + 2b 会话测试既有 cookie 工具——提交闭环（落盘+入队+发起人）、evidence 空 422、未登录 302/401、auth 未配置 503、只读页渲染事实、XSS（PRD 内容含 `<script>` 转义断言）
- 事件出口 hub 侧：FakeRunner 起 run → 手写 `.events.jsonl` fixtures（正常行/未知 event/v≠1/坏 JSON）→ 断言转发与跳过、(stamp,seq) 去重、终态后不读
- 事件出口 pa 侧：writer 单测（JSONL append/fseq 单调/fsync 调用）+ 挂点集成（run_daily 各段 dry 形态产事件行——用既有 stage 测试基建）
- 放弃告警：必读面缺失路径 + FakeMMClient 断言 notify_admin 调用
- report 双写：`PA_REPORT_DIR` set/unset 两态文件存在性
- 质量门：`bash hub/quality.sh` + pa 侧 `bash scripts/quality.sh`（pa 动了须双门全绿）

## 已知缺口（有意）

- 面板无分页/搜索（run 量小时无意义；量大了先加 SQL LIMIT 再说）
- 事件出口无加密/签名（state 卷是 hub 自管面，非公网暴露；公网前有 token+proxy 部署期门挡着）
- 频道广播事件粒度不可配（critic drop/verify 红进频道是钉死枚举；配置化等真实噪音反馈）
