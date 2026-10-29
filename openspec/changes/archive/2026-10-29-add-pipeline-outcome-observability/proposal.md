# Proposal: add-pipeline-outcome-observability

## Why

hub 容器形态跑通全链后（run #85，2026-10-28），「容器退出码」与「业务终态」两个世界的裂缝暴露四轮真机标本：

- **T1 静默全绿标本 ×4**：run 92（verify 会话 max_turns 死 → dispatch 异常被吞 → report「全绿不发邮件」→ hub done rc=0）；#83/#84（dispatch clone 段死 → hub done rc=0）；#85（verify verdict=red → PR 留 review → hub 通知仍是「succeeded, exit 0」）。用户亲历发问「为什么 MM 收到的是 run #89 → succeeded, exit 0」——**通知面只带容器退出码，业务终态（绿/红/PR/失败原因）不上通知**。
- **容器 stdout 即焚**：编排器 stdout 只在 `docker logs`，run 容器销毁即丢；run_dir 产物残缺不齐（m11 实证：容器跑完 run_dir 只剩 meta.json，靠 docker logs 才还原现场；m16 靠容器还活着才核到 report 段分类行）。
- **record/report 小缺口同族**：dispatch record `pr_number=None` 但 `pr_url` 有（同一次 PR 身份两字段填写不对称，2c0c646 的姊妹缺口）；verify-report `commands.results[].stdout` 空指针——失败命令的真实输出只在 10360 行 stream 里，报告面无指针（人查红因必须翻原始流）。

根因是**业务终态没有结构化出口**：report 段的分类（review/failing/drop/阻断）只活在人读的报告 markdown 与容器 stdout 里，机器可读的单文件契约不存在，通知侧与 hub 侧只能退回容器退出码。

## What Changes

1. **`pipeline_outcome.json` 契约**（核心）：编排器 report 段终笔，单文件业务终态快照落挂载卷——run 身份（stamp/project/镜像 tag）、逐 PRD 终态（status、verify verdict 三态、PR 指针、失败原因分类）、聚合计数（绿/红/invalid/blocked）。机器可读、run_dir 根级、hub 侧与通知侧消费它。
2. **容器 stdout 落盘**：编排器 stdout 全量落 run_dir `.stdout.log`（tee 面），容器销毁后宿主可复核全链段落行。
3. **hub 完成通知带业务终态**：hub run 完成通知（MM 通道）从 pipeline_outcome.json 取业务摘要（计数 + PR 链接 + 红因首行），容器退出码降级为附录。
4. **同族小收口**：dispatch record `pr_number` 与 `pr_url` 填写对称；verify-report 逐命令 stdout 指针（test_log 分命令或段指针，不再只留末命令全文）。

## Impact

- **Specs**：新 capability `pipeline-outcome`（outcome 契约 + stdout 落盘 + 完成通知消费业务终态 + PR 对称 + 报告逐命令指针，五 requirement 一面收口）。`execution-hub` 与 `verified-dev-execution` 主 spec 不动（通知消费与报告指针都从 outcome 新面立法，不改旧 capability 语义）。
- **代码**：`run_daily.py`（report 段终笔 outcome + stdout tee + record 对称）；`hub/runner.py` 或 hub 通知面（读 outcome）；测试先红。
- **非目标**：不改 verify 判定语义、不改 dispatch 流程、不做 metrics/时序库（单文件契约够用，YAGNI）。
