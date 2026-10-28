# Design: add-hub-panel-console

## D1 建档 = 机械落盘，零 YAML 库

hub 最小依赖面不动（fastapi/uvicorn/cryptography/pypinyin/httpx），不引 pyyaml。
profile yaml 由受控模板生成：动态值一律双引号 scalar + 转义（`\`→`\\`、`"`→`\"`），
且**只收单行文本**（表单 input 而非 textarea，含 `\n` 即 422）——双引号 + 无换行 +
转义后，YAML 注入面为零。落盘顺序：mkdir 项目目录 → 写 profile → 写 sources.yaml，
任一步失败 `shutil.rmtree` 整目录再抛（目录名全新、只删自己刚建的，无误删面）。

字段集对齐 pa 侧消费面（`run_daily.py` inject/dispatch 读 `name`/`admission`/`repo`/
`goal`/`match_surface.one_liner`）：name、admission: true（固定）、repo（规范化为
`https://github.com/<owner>/<repo>`）、goal、match_surface.one_liner。
`sources.yaml` 固定 `sources: []`（手写 PRD 流不消费 fetch 源）。

## D2 重跑 = 新队列项同 PRD（不复活旧行）

`POST /panel/queue/{id}/retry`：校验原项 `status == failed`（pending/running 拒 409
防双跑，done 拒 409 防重复投递）→ `engine.enqueue(原 project, 原 prd_path, 当前操作者
mm_user_id, initiator=username)`。旧行原样留审计（attempt/last_error 不动），新行走
完整生命周期。不加 queue_engine 新方法（enqueue 既有）；PRD 文件若已被清理 → 404
明确报错（prd_store 的文件是事实源，不存库副本）。

## D3 放弃 = 复用清队列语义，REST 与面板共享一处

既有 `DELETE /queue/{id}`（slice-3 2.5：仅 pending 可清，转终态 failed +
last_error 记因）与面板 `POST /panel/queue/{id}/abandon` 抽共享内部函数
`_abandon_queue_item(item_id, reason)`——两皮一芯，语义漂移不可能。409 文案同源。

## D4 白名单 = 一行 env，空 = 单用户回退态

`HUB_PANEL_ADMINS`（逗号分隔 MM username，如 `rocju`）。判定：admins 非空且
`username ∉ admins` → 403；admins 空 → 任何有效会话放行（单用户部署零配置零摩擦）。
挂载点：建项目 / 重跑 / 放弃三操作（改流水线行为面）；提交 PRD 与读面不变
（`_session_identity` 既有守卫）。用 username 而非 mm_user_id：env 人类可配；
MM 改名风险由运维自担（RUNBOOK 注明）。

## D5 权限/边界小结

不变式全部保持：hub 只做机械活（表单校验 + 文件读写 + 入队原语复用，零语义判定）；
凭据零接触（三操作不涉 vault/凭据）；XSS 防线不变（panel._esc 唯一转义入口）；
auth 未配置态三操作随面板整体 503（复用 `_panel_get`/`_require_configured` 形态）。
