# tasks：add-hub-slice-4b

## 1. 面板（hub 侧，D1/D2）

- [x] 1.1 `hub/panel.py`：render 函数族——`_esc`（html.escape 集中转义）+ 布局函数 + 提交页 / 队列页 / run 列表页 / run 详情页渲染（f-string 拼 HTML，零新依赖）
- [x] 1.2 `hub/api.py`：`GET /panel`（提交页）、`GET /panel/queue`、`GET /panel/runs`、`GET /panel/runs/{id}`——会话守卫（未登录 302 到 authorize）；auth 未配置态 503（面板以会话为前提）
- [x] 1.3 `hub/api.py`：`POST /panel/prd`——会话身份（mm_user_id + 显示名）→ evidence `strip()` 空 422 → PRD 内容落盘 `data_root/<project>/prd/`（复用 deliver 落盘 helper，stamp 前缀唯一名）→ `engine.enqueue(enqueued_by=mm_user_id, initiator=显示名)`；project 清单 = data_root 下有 profiles 目录 + sources.yaml 的子目录扫描（与派发 fail fast 同判据）
- [x] 1.4 run 详情页报告渲染：读 host `<state_root>/reports/项目推进报告_<stamp>.md`（stamp 对齐 run_dir 前缀），存在则原文展示、缺失显示「无报告」
- [x] 1.5 `hub/test_panel.py`：TestClient 全套——提交闭环（落盘+入队+发起人 mm_user_id）、evidence 空 422、未登录 302/401、auth 未配置 503、只读页事实渲染、XSS（PRD 内容 `<script>` 转义断言）、project 清单扫描

## 2. 事件出口（pa 侧，D3/D5）

- [x] 2.1 `pa/scripts/event_outlet.py`：`append_event(state_root, project, stamp, event, **facts)`——JSONL append + fsync + run 内 seq 单调（对齐 journal.py 写纪律）；出口路径 `<state_root>/runs/<project>/<stamp>_*.events.jsonl` 伴生 journal
- [x] 2.2 run_daily.py 三挂点：critic verdict 落定处（`critic_verdict`：verdict + 摘要）、dispatch PR 开出成功处（`pr_opened`：pr_url）、verify verdict 落定处（`verify_verdict`：verdict + 摘要）——读既有变量机械转发零新语义；vault 形态同写（零行为变化）
- [x] 2.3 report 段双写：`PA_REPORT_DIR` 非空时报告 md 双写（原落点不变 + 该目录追加）；未设零变化
- [x] 2.4 pa 侧测试：writer 单测（JSONL/seq/fsync）+ 挂点产出形态 + 双写两态；`cd pa && bash scripts/quality.sh` 全绿（compileall ✓ + ruff 零告警 ✓ + pytest 2069 passed；唯一 failed = test_bootstrap 冒烟 pre-existing 环境耦合——stash 干净树对照复现同失败，与 4b 无关）

## 3. 事件出口（hub 侧，D3）

- [x] 3.1 `hub/events.py`：读 `.events.jsonl` 全量行解析 + 契约防御——`v != 1` 整文件跳过 + stderr 告警、未知 `event` 枚举行跳过记日志、坏 JSON 行跳过
- [x] 3.2 `hub/lifecycle.py`：`reconcile_running` 对 running run 读伴生事件文件 → 进程内存 `set[(stamp, seq)]` 去重 → 新行转发通知（文本 = 事实字段机械拼接；critic drop / verify 红进频道 + 私信，其余仅私信——复用 4a 通知面）；run 终态后不读（读取面在 running 扫描集内）
- [x] 3.3 `hub/lifecycle.py`：容器 env 注入 `PA_REPORT_DIR=<CONTAINER_STATE>/reports`（docker -e 直传，与凭据 env-file 分离）
- [x] 3.4 `hub/test_events.py`：fixtures 四形态（正常行/未知 event/v≠1/坏 JSON）转发与跳过断言、(stamp,seq) 去重、终态后不读、频道规则

## 4. 无 run 放弃告警（D4）

- [x] 4.1 `hub/queue_engine.py`/`hub/lifecycle.py`：create_run 前放弃路径收敛点挂 `notify_admin`（含 project + 原因摘要；fail-open；通道关闭 stderr 降级）
- [x] 4.2 测试：必读面缺失路径 FakeMMClient 断言 notify_admin 收到；通道关闭 stderr 可见

## 5. 收口

- [x] 5.1 RUNBOOK §17（面板使用入口 + events.jsonl 契约一页 + PA_REPORT_DIR 说明）+ CONTEXT.md 词条（面板 (Panel) / 事件出口 (event outlet)）
- [x] 5.2 双质量门全绿（`bash hub/quality.sh` + pa `bash scripts/quality.sh`）+ `openspec validate add-hub-slice-4b` 通过（hub 196 passed 全绿；pa 2069 passed + 5 xfailed，唯一 failed = test_bootstrap 冒烟 pre-existing 环境耦合——stash 干净树对照复现同失败[本机装了 claude CLI 破坏测试假设]，与 4b 无关；openspec validate ✓）
