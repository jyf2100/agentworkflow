# Tasks — add-pipeline-outcome-observability

## 1. outcome 契约 + stdout tee（run_daily 侧）

- [x] 1.1 `pipeline_outcome.json` 写入（测试先红）：`_write_pipeline_outcome(records, meta)` 单点写 `<state>/pipeline_outcome_<stamp>.json`（schema v1：run 身份 / summary 七计数 / prs[]（status + verify verdict 三态原值 + pr_url + pr_number + reason_headline ≤200 字符）/ container.exit_code 附录）；写失败 warn 不拦 report（fail-open）；graph_pa report 包装 node 直调同函数（两入口零分叉测试）
- [x] 1.2 stdout tee（测试先红）：编排器段落行 + `_run_capture` 输出全量 append run 根 `.stdout.log`（run 开始 open 持有，无 fsync——观测面非真相源）；tee 目标不可写 warn 降级仅终端；LLM stream 不进（.stream.jsonl 既有面不变）
- [x] 1.3 PR 身份对称（测试先红）：PR create/lookup 回执单次解析 `pr_url` + `pr_number` 同写 dispatch record（gh create 输出/`_lookup_pr` 回执两消费点）；断言不存在一有一缺终态
- [x] 1.4 verify-report 逐命令输出指针（测试先红）：`commands.results[]` 增 `output_ref`（分命令 `<stem>.cmd<k>.out` 落 run_dir）；`test_log` 单文件语义保留（pa-verify 行锚输入面不变）；红命令 output 含 stderr/stdout 尾——不翻 10k 行 stream 可诊红因

## 2. hub 通知面

- [x] 2.1 通知消费 outcome（测试先红）：hub run 完成路径读 run_dir `pipeline_outcome_*.json` 存在 → 通知文本 lead 业务摘要（计数 + PR 链接带 🔴/🟢 标记 + 首红 reason_headline）+ 尾附 exit code；文件缺 → 回退现 exit-code 文本 + hub log 记回退（不猜业务终态）
- [ ] 2.2 hub 侧发版：通知改动过 `bash hub/quality.sh` 门；hub 容器小发版（用户授权后执行——生产容器动作逐次授权）

## 3. 真机坐实 + 归档门

- [ ] 3.1 quality.sh 全绿（pa + hub 两门）+ `openspec validate add-pipeline-outcome-observability` 过
- [ ] 3.2 镜像 v0.1.25 叠带入队重跑一轮：MM 收到业务终态文本（红/绿计数 + PR 链接，非裸 exit 0）+ run_dir 有 `.stdout.log` + 挂载卷有 outcome 文件——四处核对（通知文本 / outcome / stdout / record 对称）齐后归档
- [ ] 3.3 `RUNBOOK.md` 观测面小节：outcome 契约路径 + `.stdout.log` 语义 + 通知回退路径；`CONTEXT.md` 术语表补 pipeline_outcome
