# Design: add-pipeline-outcome-observability

## Context

四轮真机标本（run 92 / #83 / #84 / #85）证实：容器退出码 ≠ 业务终态，而通知与 hub 观测面只看得到前者。report 段已产出正确的业务分类（#85 实证「review 0 / failing 1 / drop 0 / 阻断 0」+ PR 🔴标注），缺的是把它变成机器可读的结构化出口，并让消费方（通知）改读它。

## Goals / Non-Goals

- Goals：业务终态单文件契约；stdout 落盘可复核；hub 通知带业务摘要；record/report 同族小缺口收口。
- Non-Goals：metrics/时序库、web 面板（YAGNI）；verify 判定语义与 dispatch 流程零改动；graph_pa 双轨面不在本期（flag-gated 渐进，outcome 写入点在 report 段共享出口，legacy/graph 两入口都过同一函数则天然覆盖——见 D1）。

## D1: outcome 写入点 = report 段共享出口，两编排器入口零分叉

`pipeline_outcome.json` 由 `run_daily.py` report 段末尾单点写入（`_write_pipeline_outcome(records, meta)`），graph_pa 聚合层的 report 包装 node 直调同一函数。不引入第三处手拼。写入位置 = 挂载卷 state 根（`<state>/pipeline_outcome_<stamp>.json`），与 dispatch record 同目录，run_dir 根级一份副本（符号链接或双写，取双写——容器内 cp 一次，挂载卷语义自包含）。

**Alternatives**：hub 侧从 runs 表 + run_dir 文件拼装业务终态——拒。hub 不该懂 pa 内部产物语义（控制面/目标面边界），拼装逻辑会随 pa 演进漂移。

## D2: outcome 契约最小集（v1）

```json
{
  "schema": "pa.pipeline_outcome.v1",
  "run": {"stamp": "20261028m16", "project": "cc-web-control", "image_tag": "v0.1.24", "finished_at": "…"},
  "summary": {"green": 0, "red": 1, "invalid": 0, "blocked": 0, "review": 0, "drop": 0, "no_dispatch": 0},
  "prs": [
    {"prd_id": "…", "status": "pr_open", "verify_verdict": "red",
     "pr_url": "https://github.com/o/r/pull/60", "pr_number": 60,
     "reason_headline": "playwright declared 命令 exit 1（dev-reported 命令缺 --config）"}
  ],
  "container": {"exit_code": 0}
}
```

要点：①verify verdict 用三态原值（green/red/invalid），不折算——对齐 verified-dev-execution 的三态契约；②`reason_headline` 单行人读摘要（skip_reason/note/triage_reason 首个非空者截断 200 字符）；③容器退出码降级附录（消费方不再用它判业务）；④`pr_number`/`pr_url` 同源写入（修对称缺口——从 gh create/lookup 回执同一次解析两字段）。

## D3: stdout 落盘走编排器自 tee，不靠 hub 抓 docker logs

`run_daily.py` 的 stdout 段落行打印函数（`[stage:label]▶ …` 的发射点）包 tee——每行 print 同时 append 到 run 根 `.stdout.log`。**Alternatives**：hub runner 在容器退出后 `docker logs > run_dir/stdout.log`——拒：容器清理竞态（runner 退出路径多，崩溃时 logs 未抓容器先删）；且 m11 实证靠 docker logs 还原现场是补救不是保障。编排器自 tee 的代价是 run 根级文件多一个，但生命周期与 state 产物一致（崩溃时已写部分仍在）。

`.stdout.log` 粒度：编排器段落行 + `_run_capture` 的 `$ cmd / [stdout] / [stderr]` 全量（与终端形态一致），LLM 会话 transcript 不进（已在 .stream.jsonl）。

## D4: hub 通知面改造点 = hub 侧读 outcome 文件，容器退出码只兜底

hub runner 完成路径：run_dir（挂载卷）存在 `pipeline_outcome_*.json` → 通知文本取 summary 计数 + prs 链接/红因首行；不存在（编排器旧版/写失败）→ 回退现行为（exit code 文本），回退事件本身记一行 hub log。hub 不解析 pa 内部其他产物（D1 边界）。

**通知文本形态**（对齐用户实际收到的 MM 消息）：
- 旧：`run #85 → succeeded, exit 0`
- 新：`run #85 done · 红1 绿0 · PR #60 [🔴failing] playwright declared 命令 exit 1（缺 --config）· exit 0`

## D5: verify-report 逐命令 stdout 指针

`commands.results[]` 增 `output_ref`：分命令 testout 文件（`<stem>.cmd<k>.out`）或同文件段指针（offset/length）。v1 取分命令小文件——机械、无解析歧义；文件数 ≤ declared 数（≤ 数个），run_dir 膨胀可控。现 `test_log` 单文件语义保留（pa-verify 行锚解析输入面不变）。

## Risks / Trade-offs

- **outcome 写失败不拦 run 收尾**：终笔前写失败 → warn + 通知走回退路径（fail-open，观测面不拦业务）；但**写成功是通知新路径的前提**，测试断言两路径。
- **stdout tee 的性能**：行级 append + 每 run 一次 open（run 开始 open 句柄持有），零 fsync 压力（与 journal 的 fsync 语义不同——stdout 是观测面非真相源）。
- **镜像 tag 从哪来**：hub 侧 env 注入（HUB_IMAGE_TAG 或等价）或容器内不可得则 run.image_tag 省略——D2 契约里该字段 optional。

## Migration Plan

1. tasks 先红（outcome 契约/tee/通知两路径/record 对称/report 指针）→ 实现 → quality.sh 绿。
2. 镜像 v0.1.25 叠带；hub 通知面 hub 侧发版（不重启 hub 的路径走通知代码在 runner 容器内——需 hub 侧小发版，另走 hub quality 门）。
3. 真机坐实：重跑一轮，MM 收到业务终态文本 + run_dir 有 .stdout.log + outcome 文件在挂载卷。

## Open Questions

无——四标本已把语义钉死，契约字段在 D2 定版。实现期若 verify verdict 面出现第四态（如 triage 中间态）按三态原值透传 + summary 增键处理，契约版本号不变（v1 容加法）。
