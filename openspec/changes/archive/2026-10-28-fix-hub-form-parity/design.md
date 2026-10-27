# Design: fix-hub-form-parity

## Context

hub 形态（服务器集中式跑 pa 后半段）已跑通全链（#47 真机冒烟：inject→critic→dispatch→merge）。本体论对齐审核发现三处声明-实现偏差（详见 proposal）。本 change 是小型修补：一处一行代码（lifecycle 派发命令）+ 两处声明/样例对齐，无架构变化。

## Goals / Non-Goals

**Goals:**
- 容器内执行链补齐 report 段（`inject→report`），使 `pa-event-outlet` 的 PA_REPORT_DIR 双写产物真实存在，面板 run 详情能渲染报告
- `per-agent-model-routing` spec 与实现一致：容器形态 env-only（文件配置点不进镜像）
- `profile.example.yaml` 补 forge 字段教学（gitlab/gitcode 用户有样例可抄）

**Non-Goals:**
- SMTP 容器内透传（通知面已归 hub-notifications MM bot）
- 镜像 COPY `config/` 目录
- mac/vault 本机形态任何行为变化

## Decisions

**D1：report 段接通用 `--to-stage report` 参数，不改 run_daily 默认值。**
派发命令从 `--from-stage inject`（隐含 to=dispatch 默认）改为显式 `--from-stage inject --to-stage report`。run_daily 的 argparse default 保持 `dispatch` 不动（mac 本机 cron 零变更），hub 侧显式传全段范围。
*备选否决*：改 run_daily 默认值为 report——影响 mac 形态行为，违反「本 change 只动 hub」边界。

**D2：SMTP 容器内 fail-open 告警，容忍。**
report 段末尾的 `_smtp_notify` 在容器内必然走「失败退化为告警，不阻塞流水线」分支（SMTP env 不透传，只透 `PA_*`）。这是既定 fail-open 语义，run 终态不受影响；hub 形态通知面是 MM bot（hub-notifications），不补 SMTP env 透传。RUNBOOK 注记即可。

**D3：路由配置点容器形态 env-only（用户裁决 2026-10-27，方案 B）。**
executor 镜像不含 `config/`，容器内文件主点恒缺失。裁决：这是有意的部署面选择——路由随 hub.env 差异化、改后即刻生效（lifecycle `PA_*` 透传已存在），烤进镜像反而把路由钉死在发布时点。spec 加限定使声明与实现一致，零代码。

## Risks / Trade-offs

- [report 段引入新失败面] → report 段纯机械（读 state JSON 落 md + 双写），且 SMTP fail-open；即使 report 落盘异常，dispatch 终态已先持久化，attempt 语义不受影响。
- [历史 run 面板仍「无报告」] → 预期行为：旧 run 从未跑过 report 段，`state/reports/` 无产物；面板 spec 本就允许「无报告」展示，不做回填。
- [env-only 限制未来想改] → spec 限定写明理由；若将来要文件主点，另立 change 讨论 COPY config/ 或挂载方案。

## Migration Plan

1. lifecycle.py 派发命令补 `--to-stage report` + 单测断言 cmd 形态
2. spec/RUNBOOK/样例三处文档对齐
3. 真机验证：enqueue 一个探针 run，确认 `state/reports/` 出现产物 + 面板渲染
4. 回滚：revert 单 commit 即可（一行代码 + 文档）

## Open Questions

无（三项决策均已裁决）。
