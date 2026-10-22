# add-hub-slice-2b 实施验证与收口审核

## 实施验证（2026-10-22）

- `bash hub/quality.sh` 全绿：compileall + pytest **120 passed**（test_auth 23：基元 12 + OAuth 接缝 5 + 端点 10 + 未配置 logout 例外 1 并入既有条；test_lifecycle 新增派发链贯通 3）+ ruff E9/F 零告警
- `openspec validate add-hub-slice-2b` passed；全量 specs validate issues 0
- hub 镜像 build 通过 + 镜像内 `httpx 0.28.1 / auth / api` 可导入（任务 1.3 挂账兑现，验证 tag 已清）
- 实施中发现并钉住的技术事实：Pydantic body 模型定义在 `create_app` 闭包内会被 FastAPI（`from __future__ import annotations` 下按模块全局解析注解）退化成 query 参数——422 实证后提到模块级（`hub/api.py:40` 注释钉死）

## 收口后本体论审核报告（2026-10-22）

**审核面**：delta specs 三份（hub-oauth-session 新增 / execution-hub 修改 / hub-credential-vault 修改）、实现（auth.py / api.py auth 端点群 + queue 会话优先 / config MM 五 env / db initiator 列 + 迁移 / queue_engine 透传 / lifecycle `_credentials(user)` 参数化 + sign_meta initiator）、部署物（hub.env.example / compose 注释 / Dockerfile）、RUNBOOK §13 auth 段 + F2 复审两笔。

**总判**：实现与规约咬合度高——spec 声明的全部不变式（CSRF state 校验、不产半会话、凭据不回显、原子录入、按发起人解密、2a 回退态）逐条有实现事实 + 测试对应。身份三物（state / 会话 / 保险箱记录）范畴边界清晰，`verify_session` 半会话校验构成 state≠会话的范畴界且有测试。

**分级发现与处置**（用户裁决：B 全部落地修正 + C 随手带上）：

| 级 | 发现 | 处置（已落地） |
|---|---|---|
| B1 | logout 未配置时 200，与「auth 群整群 503」SHALL 相悖（实现侧幂等更合理但 spec 未裁例外） | spec 钉例外（logout 幂等清 cookie 无 MM 依赖，未配置 200）+ 巡检 scenario 对齐 + 测试补钉（`test_auth_endpoints_503_when_unconfigured`） |
| B2 | design D5「schema 零变化」与实现 DB schema 变更（queue_items +initiator 列 + ALTER 迁移）矛盾且无决策记载 | D5 补修订：标题改「meta schema 零变化；DB 层加人读名列」，正文记载加列决策与 2a 库迁移语义 |
| C1 | state 的范畴未钉术语（docstring「同机制」读似「短期会话」） | auth.py docstring + hub-oauth-session spec 各钉一句：state = 一次性 CSRF nonce 载体，非身份非会话 |
| C2 | 「2a 行为零变化」未限定范围（显式 enqueued_by 路径实为语义修正） | execution-hub scenario 钉界：默认兜底路径零变化；显式路径 = meta 署名与发起人对齐（D4） |

修正后复验：quality.sh 全绿（120 passed）+ change validate passed。
