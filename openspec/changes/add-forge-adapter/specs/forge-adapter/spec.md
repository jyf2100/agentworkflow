# Delta Spec: forge-adapter（新能力）

## ADDED Requirements

### Requirement: 三平台统一 Git 托管操作契约
forge adapter SHALL 以统一接口提供 `pr_list` / `pr_create` / `pr_merge` / `branch_protection` 四操作，覆盖三个 Git 托管平台：github（实现走既有 gh CLI，行为与现状等价——命令参数面逐 token 一致）、gitlab（REST API v4）、gitcode（api.gitcode.com/api/v5）。平台选择由调用方声明的 `forge` 值决定（`github` 为缺省，兼容既有 profile 不声明 forge 的形态）。`forge` 值非法（拼错 / 大小写漂移 / 尾空格 / 未知平台）MUST 装载期 fail-fast，MUST NOT 静默回落 github（错平台查询与副作用）。gitlab 平台 MUST 支持显式 `host`（本地实例地址）；gitcode 的 API base 固定，不接收 host。

github 分支按调用面提供两个命名入口（共享命令构造，不做假统一）：dev-agent 面（cwd 推断 repo、失败抛异常契约——与既有 dev-agent `gh()` 语义一致）；run_daily 面（`-R` 显式 repo、三态消费契约——与既有对账调用参数集一致）。两种调用面的参数集本就不同（`--state` / `--limit` / `--json` 字段各异），直比锚定按调用面各锚各的。

**实施 carve-out（2026-10-23 本体论审核 M7，工件-实现对齐）**：上述 github 两命名入口是 adapter 的**能力面**（提供并被参数快照测试锚定）；dispatch 链消费面（dev-agent `_pr_automerge` / run_daily 对账四触点）对 github/未声明项目**继续走既有内联 gh 调用序列**（forge 分流短路返回 None），**不接线 adapter wrapper**——这是 D1「github 行为与基线逐 token 等价」的实现路径选择：把 github 基线路径换成新代码路径无行为收益而回归面大。wrapper 接线是后续 change 的可选收敛，不构成本 capability 对 github 的行为要求。

#### Scenario: 未声明 forge 的既有项目走 GitHub 原路径
- **WHEN** profile 未声明 `forge` 字段
- **THEN** adapter 分流到 github 实现（gh CLI），命令参数面与变更前逐 token 一致（行为等价；装载代码因新增字段解析而变，行为不变）

#### Scenario: 非法 forge 值 fail-fast
- **WHEN** profile 声明 `forge: Gitlab`（大小写漂移）、`forge: gitlab `（尾空格）或 `forge: gitee`（未知平台）
- **THEN** 配置装载明确报错，不静默回落 github（避免对错误平台发起查询或副作用）

#### Scenario: gitlab 项目声明 host
- **WHEN** profile 声明 `forge: gitlab` 且提供 `gitlab_host`
- **THEN** adapter 的全部 REST 调用以该 host 为 base（本地实例），不触碰 gitcode.com 或 github.com

#### Scenario: gitlab 项目缺 host
- **WHEN** profile 声明 `forge: gitlab` 但缺 `gitlab_host`
- **THEN** 配置装载明确报错（fail-fast），不猜默认公网地址

### Requirement: 三态 fail-safe 判据收进 adapter 契约
`pr_list` / `branch_protection` 查询 MUST 返回显式 `FOUND` / `NOT_FOUND` / `UNKNOWN` 三态（fail-safe-dispatch capability 的语义不变，判据来源从 gh CLI 特有实现上升为 adapter 契约）。REST 实现（gitlab / gitcode）的判据按端点类型分型：

- **列表端点**（`pr_list` / `branch_protection`）：HTTP 404 → `UNKNOWN`（404 = 看不见项目——无权限 / 路径错 / 实例错；GitLab 对无权限项目故意返 404，绝不等于「确定没有 PR」）；网络错误、超时、5xx、429、响应非法 JSON → `UNKNOWN`（附净化原因，不含凭据）；成功响应（含空列表）→ `FOUND`。
- **单对象端点**（`pr_merge` 的目标 PR 等）：404 → `NOT_FOUND`（对象确定不存在）。
- HTTP 401/403 → 配置类确定性失败（permanent，立即上抛不重试）；dispatch 消费面 MUST 将其映射为**单项目终态**（skip + 高声暴露），不炸整个 dispatch 段、不进瞬态重试臂。

`FOUND`-空的消费语义按调用点既有规则完成（`_lookup_pr` 空 = 无既有 PR；`count_inflight_prs` 空 = 0 在途）；adapter 恒返回 `FOUND`-空、不感知调用语境，映射不在 adapter 内做。github 实现维持既有 gh CLI 退出码/stderr 判据。

`pr_merge` 的失败分类：405/409（不可合并 / 冲突）= permanent 确定性失败（不进瞬态重试）；406（pipeline 未过等暂不可合）= `UNKNOWN`（外部状态未定，稍后可合）；网络/5xx/429 = `UNKNOWN`。

#### Scenario: GitCode 限速 429
- **WHEN** gitcode `pr_list` 收到 HTTP 429
- **THEN** 返回 `UNKNOWN`（外部状态无法建立），dispatch 按 fail-safe 语义阻断而非当作无 PR

#### Scenario: GitLab token 无效
- **WHEN** gitlab 调用收到 HTTP 401
- **THEN** 抛配置类确定性错误（不进入瞬态重试臂），dispatch 消费面映射为该单项目 skip 终态 + 高声暴露（不影响同批其他项目），错误消息不含 token 值

#### Scenario: 列表端点 404 是「看不见」不是「没有」
- **WHEN** gitlab `pr_list` 因 token 权限被收回或路径写错收到 HTTP 404
- **THEN** 返回 `UNKNOWN`（dispatch 按 fail-safe 阻断），MUST NOT 归为 `NOT_FOUND`（否则误判「无在途 PR」继续投递；reconcile 甚至可能对看不见的仓库执行破坏性对账）

#### Scenario: 查询成功但无 PR
- **WHEN** gitlab/gitcode `pr_list` 返回 200 空列表
- **THEN** 返回 `FOUND`（状态已建立、结果为空），不误判 `NOT_FOUND`（空与非空的语义分界由调用点消费）

### Requirement: 平台差异归一
adapter MUST 在边界内归一平台差异，调用方不得感知：state 归一为**封闭枚举 `{open, merged, closed}`**（gitlab/gitcode 的 `opened` → `open`；`merged` / `closed` 保持；gitlab 的 `locked` → `open`——保守归一，locked MR 仍 open 未合并、仍占 single-flight 在途名额）；归一后 PR 对象字段面为 `number / url / state / head / base`（对账消费的稳定契约）。merge 语义：三平台 MUST 均产生双 parent merge commit——gitcode 经请求级 `merge_method=merge`；gitlab **无请求级 merge_method 参数**（merge method 是项目级设置），改为派发**前置校验项目 merge method 设置**（≠ merge commit 即 permanent 报错）+ **merge 后验证结果 commit 双 parent**（单 parent 即报错暴露，绝不静默）；github 维持 `gh pr merge --merge`。PR 数字标识（gitlab 为 iid，语义统一为「平台内 PR 标识」）。

#### Scenario: state 归一
- **WHEN** gitcode 返回 state 为 `opened` 的 PR 列表
- **THEN** 调用方收到归一后的 open 状态，既有对账逻辑无需平台分支

#### Scenario: locked MR 保守算在途
- **WHEN** gitlab 返回 state 为 `locked` 的 MR（仍 open 未合并）
- **THEN** 归一为 open，`count_inflight_prs` 计入在途（保守：防 single-flight 名额低报导致超额投递）

#### Scenario: 双 parent merge 语义跨平台一致
- **WHEN** 任一平台的 `pr_merge` 成功
- **THEN** 目标分支产生双 parent merge commit（与现状 GitHub 路径同语义，revert 流程不感知平台）

#### Scenario: GitLab 项目 merge method 设置不合规被前置拦截
- **WHEN** gitlab 项目的 merge method 设置为 squash 或 fast-forward，派发前置校验发现
- **THEN** permanent 报错（明确指出项目设置需为 merge commit），不发起 merge（防止静默产出单 parent）

### Requirement: 多级仓库路径归一
adapter SHALL 以「仓库全路径」为标识（如 `组/子组/仓`），MUST 支持多级路径（gitlab 子组形态）。既有 `repo_owner_repo` 的两级正则提取 MUST 泛化：从 remote URL 提取完整尾部路径；gitlab REST 调用侧 MUST URL-encode 全路径作 project 标识；两级路径（`owner/repo`）MUST 保持原提取结果（github/gitcode 全兼容）。提取失败维持现状返 None。

dispatch 身份键（single-flight 锁、already_dispatched 幂等查重、分支查重）MUST 使用 **host × 仓库全路径** 二元组（两个 host 上的同路径是两个不同的仓，不得共享锁或查重键）。source 侧（forge-watch 采集）的 `gitlab_host` 只管采集、profile 侧的 `gitlab_host` 只管 dispatch，二者独立声明、互不引用（同一实例通常同值，但机制上无一致性要求）。

#### Scenario: GitLab 三级路径提取
- **WHEN** remote origin 为 `https://gitlab.corp.local/grp/sub/team.git`（组/子组/仓）
- **THEN** 提取仓库全路径 `grp/sub/team`，API 调用使用 `grp%2Fsub%2Fteam`

#### Scenario: GitHub 两级路径不受影响
- **WHEN** remote origin 为 GitHub 形态 `jyf2100/cc-web-control`
- **THEN** 提取结果与既有正则完全一致（`jyf2100/cc-web-control`）

#### Scenario: 双 host 同路径不共享锁
- **WHEN** 项目 A 指向 `gitlab.corp.local/grp/team`，项目 B 指向 `gitlab.other.local/grp/team`（host 不同、路径相同）
- **THEN** 二者 dispatch 身份键不同（host 参与），各自持有独立 single-flight 名额与查重键

### Requirement: 凭据 env 约定与远程 git 操作注入
adapter 与**全部远程 git 操作**（clone / fetch / ls-remote / push）的平台凭据 SHALL 经 env 注入：`GH_TOKEN`（github，语义不变）/ `GITLAB_TOKEN`（gitlab）/ `GITCODE_TOKEN`（gitcode），每键 = 每平台，命名不漂移。凭据 MUST NOT 出现在命令行参数、remote URL、日志或错误消息中。GIT_ASKPASS 助手脚本对 run 内全部远程 git 操作生效（进程级 env 注入 + `GIT_TERMINAL_PROMPT=0`，杜绝无凭据时挂起等 tty）；askpass 脚本本体不含 token（只读 env 输出到 stdout，stdout 仅被 git 消费）；hub 形态脚本落容器内非挂载路径（真随容器弃），本机形态落 state 目录并在 run 终态清理（0700、同名幂等覆盖）。profile 声明了非 github 的 forge 而对应平台 token env 缺失时，MUST 在 dispatch 准入早期 fail-fast（与缺 host 同级），不得烧完 dev loop 才在 push 处 401。

#### Scenario: push 凭据不经 URL
- **WHEN** dispatch 对 gitlab 仓执行 `git push`
- **THEN** remote URL 保持无凭据形态，认证经 GIT_ASKPASS 从 env 取 GITLAB_TOKEN 完成

#### Scenario: 私有仓读操作同享凭据通道
- **WHEN** dispatch 对私有 gitlab 仓执行 `ls-remote` / `fetch` / `clone`
- **THEN** 认证经同一 GIT_ASKPASS 通道完成，不因读操作缺凭据而挂起或 401（私有仓读路径与 push 同等覆盖）

#### Scenario: 声明 forge 缺 token 早期失败
- **WHEN** profile 声明 `forge: gitlab` 而 GITLAB_TOKEN 未注入（env/容器均无）
- **THEN** dispatch 准入早期明确报错（与缺 host 同级，不启动 dev loop），错误消息不含 token 值

#### Scenario: 错误消息不含凭据
- **WHEN** 任一平台 API 调用失败进入日志或用户可见错误
- **THEN** 消息只含平台名、状态码、端点路径，不含任何 token 值或片段
