# forge-watch

## Purpose

多平台仓库监控采集：sources.yaml 以 `kind: forge-repo` 源声明多平台监控（`forge` 平台 / `gitlab_host` / 多级 `repos` 全路径 / `watch` 维度 commits·pulls·issues），既有 `kind: github-repo` 原样保留零变化。平台 API 调用（拉取、分页、窗口过滤、state 归一）收进纯 Python 机械预取脚本——token 经 env narrow-inject 给预取子进程、不进 persona 环境——persona 只对归一 markdown 做语义 summarize，radar 不感知来源平台差异。采集失败为源级二元失败（failed 源清单登记、report 渲染可见），不借用 dispatch 面三态术语。

## Requirements

### Requirement: 多平台监控源声明
sources.yaml SHALL 新增 `kind: forge-repo` 监控源：字段 `forge`（github 缺省 / gitlab / gitcode）、`gitlab_host`（gitlab 必填；只管采集侧，与 profile 的 host 互不引用；gitcode 源不接收 host）、`repos`（仓库全路径列表，支持多级）、`watch`（监控维度列表，取值 `commits` / `pulls` / `issues`，缺省 `[commits, pulls]`——与现状对齐）、`window`（沿用既有）。既有 `kind: github-repo` SHALL 原样保留（语义 = forge: github + watch: [commits, pulls] 的历史形态，不删不改）。`forge` 值非法 MUST 装载期 fail-fast（与 forge-adapter 契约一致，不静默回落 github）。

#### Scenario: 声明 GitLab 源带 issue 维度
- **WHEN** sources.yaml 配 `kind: forge-repo`、`forge: gitlab`、`gitlab_host`、`repos: [grp/sub/team]`、`watch: [pulls, issues]`
- **THEN** 采集按窗口拉该仓的 MR 与 issue 活动，产出 radar 可消费的 digest

#### Scenario: 既有 github-repo 源零变化
- **WHEN** sources.yaml 保持既有 `kind: github-repo` 源
- **THEN** 采集行为与本变更前一致（该 kind 的代码路径不触碰）

#### Scenario: forge-repo 缺 gitlab_host
- **WHEN** `forge: gitlab` 的源未提供 host
- **THEN** 源装载 fail-fast 报错（与 forge-adapter 的 host 契约一致）

#### Scenario: 非法 forge 值 fail-fast
- **WHEN** 源声明 `forge: gitlab `（尾空格）或未知平台值
- **THEN** 源装载明确报错，不静默按 github 采集

### Requirement: 机械预取与语义摘要分工
平台 API 调用（commits/pulls/issues 拉取、分页、窗口过滤、state 归一）SHALL 收进纯 Python 机械脚本（零 LLM、可单测）；token 经 env 注入且只 narrow-inject 给预取子进程，MUST NOT 进入 persona 进程环境。persona 只对预取产物做 summarize（语义活，签名与输出契约不变——仍一行 JSON items）。对 forge-repo 源，MUST NOT 把平台 API 细节（拼接 curl、平台端点差异）压进 persona prompt（既有 `github-repo` kind 按其保留契约不变——persona 持 Bash 跑 gh 是该 kind 的既定形态）。三平台预取输出 MUST 归一为统一 markdown 形态（radar 不感知来源平台差异）。

窗口过滤的时间戳口径逐维度钉死（确定性，不因实现者而异）：`commits` = author date；`pulls` = updated_at；`issues` = updated_at——与既有 `github-repo` 采集基线同源一致（同仓同窗口在两种 kind 下结果一致）。

失败语义分界：三态（FOUND / NOT_FOUND / UNKNOWN）只覆盖 dispatch 面；采集面是**源级二元失败**（成功产出 digest / 失败登记），不借用三态术语。

#### Scenario: GitCode 采集走机械脚本
- **WHEN** forge-repo 源的采集执行
- **THEN** persona 收到的是预取脚本产出的归一 markdown，prompt 中无任何 gitcode 端点细节；平台 token 不在 persona 进程环境中

#### Scenario: 预取失败明确登记
- **WHEN** 某平台 API 拉取失败（网络/认证/限速/超时）
- **THEN** 预取脚本以非零退出 + 净化错误消息失败（不静默空结果冒充「无活动」），fetch 汇总 json 的 failed 源清单登记该源，report 渲染可见

#### Scenario: 窗口口径确定性
- **WHEN** 一周前创建、昨日有新评论的 issue 处于窗口边界
- **THEN** 按 issues = updated_at 口径判在窗口内（时间戳口径是 spec 声明，非实现者选择）

### Requirement: issue 监控维度
`watch` 含 `issues` 时 SHALL 拉取窗口内新增/更新的 issue（标题、编号、状态、标签、链接），三平台端点：github `repos/{o}/{r}/issues`、gitlab `projects/{id}/issues`、gitcode `repos/{owner}/{repo}/issues`（v5）。混入与敏感过滤按平台声明：github issues 端点历史混入 PR——MUST 剔除含 `pull_request` 字段的条目；gitlab 预取 MUST 默认排除 `confidential=true` 条目（机密 issue 的标题/标签不进 digest、雷达与日报）；gitcode issues 端点字段形态未以真实仓 fixture 锚定前，`gitcode + issues` 组合不声明可用（源装载明确报「暂不支持」，fixture 锚定后放开——见 design Open Questions）。

#### Scenario: GitHub issues 端点混入 PR 被过滤
- **WHEN** github `repos/{o}/{r}/issues` 响应含 `pull_request` 字段的条目
- **THEN** 该条目不进入 issue digest（它是 PR，归 pulls 维度管）

#### Scenario: GitLab 机密 issue 不进 digest
- **WHEN** gitlab issues 响应含 `confidential=true` 条目
- **THEN** 该条目不进入 issue digest（机密标题/标签不进信号链与日报）

#### Scenario: gitcode+issues 组合在 fixture 锚定前被拒
- **WHEN** 源声明 `forge: gitcode` 且 `watch` 含 `issues`，而 gitcode issues fixture 尚未锚定
- **THEN** 源装载明确报错（暂不支持），不产出含糊 digest

#### Scenario: issue 活动进信号链
- **WHEN** 窗口内某仓有新 issue 且 watch 含 issues
- **THEN** digest 含该 issue 摘要，radar 可将其与项目 match_surface 比对打分

### Requirement: 平台节流
gitcode 平台的采集与对账调用 SHALL 遵守其限速（400 次/分、4000 次/时）：预取脚本对同平台多仓串行 + 请求间最小间隔（缺省 1 秒，可配）；收到 429 时按退避间隔重试一次，仍失败按该源预取失败登记。forge_fetch 预取受 fetch 段 TIMEOUT 约束，超时按该源预取失败处理（不悬垂、不拖垮整段）。gitlab（本地实例）与 github 维持现状节奏。

#### Scenario: 多仓 GitCode 源不触限速
- **WHEN** 一次采集循环遍历 10 个 gitcode 仓 × 多维度
- **THEN** 请求经节流间隔串行发出，全程不触发 429

#### Scenario: 429 退避一次
- **WHEN** gitcode 请求收到 429
- **THEN** 按退避间隔重试一次；再失败则该源失败登记（源级二元失败，不静默）
