# Design: add-forge-adapter

## Context

Git 远程面现状：git 本体操作（clone/push/branch/rebase）天然 host 无关；「开 PR + 对账 + 准入分支保护」走 gh CLI 硬绑 GitHub，触点 5 处——dev-agent PR auto-merge（`gh pr create/list/merge --merge`，ADR-0008 幂等模板）、run_daily dispatch 面 ×4（准入分支保护 `check_branch_protection` + 对账 `_lookup_pr` / `count_inflight_prs` / 分支查重，三态判据 = gh 退出码 + stderr 文案）、fetch 采集（`gh api repos/…/commits|pulls`，persona 内 Bash）、hub stale_check（GitHub template repo 专用，不改）。仓库监控现状仅 commits+pulls 两维度，issue 连 GitHub 侧都没有。

平台事实（2026-10-23 探索验证）：GitLab 自建走 API v4（`/api/v4/projects/{url-encoded-全路径}/merge_requests`，PAT 认证，仓库路径可多级）；GitCode 是 Gitee/AtomGit 血统（`https://api.gitcode.com/api/v5`，PAT 三式认证，`PUT /repos/{o}/{r}/pulls/{n}/merge?merge_method=merge` 请求级 merge 方法，限速 400/分 4000/时，state 枚举 `opened/merged/closed`）；GitCode 无官方 CLI，GitLab 的 glab 引入第二 CLI 只增镜像负担。

约束：ADR-0001 控制面/目标面边界不变；机械活/语义活分工（平台 API 细节是机械活，不进 persona prompt）；线性 git 历史；hub 质量门与 pa 质量门是验收单一真理源；GitHub 现产路径零行为变化（有完整测试基线可锚定）。

## Goals / Non-Goals

**Goals**：三平台统一 PR 操作 + 三态 fail-safe 语义（GitHub 分支保 gh CLI 原实现）；fetch 采集泛化（forge-repo 源 + pulls/issues/commits 维度）；凭据面按平台增键（GH_TOKEN 语义不漂移）；仓库路径多级归一。

**Non-Goals**：glab CLI 引入（镜像加层，REST 已足）；stale_check 泛化（GitHub template repo 专用）；hub 队列/容器编排改动（平台无关）；GitCode/GitLab 的 webhook 实时监控（维持轮询窗口形态）；`kind: github-repo` 移除或语义变更（保留兼容）。

## Decisions

### D1：REST 适配（gitlab/gitcode）+ gh CLI 保留（github），不做全 REST 化
三平台一个 `forge_api.py`（纯 stdlib `urllib`，零新依赖）：`ForgeApi` 工厂 `for_profile(prof)` 按 `forge` 分流（非法值 fail-fast 不回落 github）；github 分支按调用面提供两个命名入口（共享命令构造）：dev-agent 面（cwd 推断 + 抛异常契约）与 run_daily 面（`-R` + 三态契约），委托既有 gh 调用序列——两种调用面参数集本就不同（`--state`/`--limit`/`--json` 字段各异），不做假统一，直比按调用面各锚各的（命令参数面与基线逐 token 一致，行为等价）。
*实施注记（2026-10-23 本体论审核 M7）*：github 两命名入口是能力面（提供 + 测试锚定）；dispatch 链对 github/未声明项目**不接线 wrapper**——短路走既有内联 gh 调用（行为等价的稳妥路径，接线属后续可选收敛）。详见 specs/forge-adapter carve-out 段。
*备选否决*：①全 REST 化（连 GitHub 也换）——破坏现产 gh 路径与全部幂等模板基线，收益仅「统一实现美学」；②glab CLI——GitCode 无官方 CLI，两新平台只有 REST 是公共解，引入 glab 只为 GitLab 不值镜像层。

### D2：三态判据按 HTTP 语义重建（REST 分支），按端点分型；401/403 = permanent
**列表端点**（pr_list / branch_protection）：404 → `UNKNOWN`（404 = 看不见项目——无权限/路径错，GitLab 对无权限故意返 404；绝不等于「无 PR」，否则 fail-open——误判无在途继续投递，reconcile 甚至可能对看不见的仓库做破坏性对账）；网络/超时/5xx/429/非法 JSON → `UNKNOWN`（净化原因）；200（含空列表）→ `FOUND`。**单对象端点**（pr_merge 目标 PR）：404 → `NOT_FOUND`（对象确定不存在）。401/403 → 抛 `ForgeConfigError`（permanent 配置类确定性失败），dispatch 消费面映射为**单项目终态**（skip + 高声暴露）——不进瞬态重试臂、不炸整个 dispatch 段（对齐 run_daily exit_diag 分流的 permanent 语义，2026-10-23 B-MEDIUM 同型）。`FOUND`-空 → 调用点按既有语义消费（`_lookup_pr` 空 = 无 PR；`count_inflight_prs` 空 = 0 在途），映射不在 adapter 内做。github 分支维持 gh 退出码/stderr 判据不动。
*备选否决*：①把 REST 错误也塞进 Transient 臂统一重试——401 重试必败烧窗口，且与信封 is_error 分流刚建立的 permanent 语义冲突；②404 一律 NOT_FOUND——对列表端点就是 fail-open（见上）。

### D3：监控采集 = 机械预取脚本 + persona summarize（不扩 prompt）
新 `forge_fetch.py`（纯 Python）：按源声明拉 commits/pulls/issues → 窗口过滤 → state 归一 → 归一 markdown 落盘；persona（复用既有 fetch persona 或新建 `pa-forge-summarize`）只读落盘产物做 digest。token 经 env（`GITLAB_TOKEN`/`GITCODE_TOKEN`/`GH_TOKEN`）narrow-inject 给预取子进程（不进 persona 环境）。窗口口径逐维度钉死（commits=author date、pulls/issues=updated_at，与 github-repo 基线同源）；采集面失败语义 = 源级二元失败（fetch 汇总 json 登记 failed 源清单，report 可见），不借 dispatch 三态术语；gitlab issues 默认排除 confidential 条目；gitcode+issues 在真实 fixture 锚定前不声明可用。
*备选否决*：①persona 内 Bash 拼 curl（现状 gh 形态的自然延伸）——平台端点/分页/state 差异进 prompt 易错难测，违背机械/语义分工；②新 persona 全包——summarize 语义与既有 fetch persona 同构，无增益。

### D4：路径标识用「仓库全路径」，两级提取结果向后兼容
`repo_owner_repo` 泛化为 `repo_full_path`：正则取 remote URL 尾部完整路径（多级）；GitLab API 侧 URL-encode 全路径；GitHub/GitCode 两级形态提取结果与旧正则逐字节一致。旧名保留 alias（调用面广，改名留 follow-up）。dispatch 身份键（single-flight 锁 / already_dispatched / 分支查重）= **host × 全路径** 二元组（两个 host 同路径是两个仓，不共享锁）；source 的 `gitlab_host` 只管采集、profile 的只管 dispatch，二者独立声明互不引用。
*备选否决*：GitLab 数字 project id 先行探测一次再缓存——多一次 API 往返与缓存失效面，URL-encode 全路径是 v4 官方支持形态。

### D5：全部远程 git 操作凭据经 GIT_ASKPASS 脚本注入
进程级 `GIT_ASKPASS=<脚本>` + `GIT_TERMINAL_PROMPT=0` 覆盖 run 内全部远程 git 操作（clone / fetch / ls-remote / push——私有仓读操作与 push 同通道，缺一即功能残缺）；askpass 脚本只读 env 输出 token（本体不含 token、stdout 仅被 git 消费）；remote URL 保持无凭据。脚本生命周期：hub 形态落容器内**非挂载路径**（/tmp，真随容器弃——state 目录是宿主挂载卷，落那里容器弃了脚本不弃）；本机形态落 state 目录并在 run 终态清理（0700、同名幂等覆盖）。token 来源：hub 链路进容器 env-file（execution-hub 既有 0600 临时 env-file 通道，零新面）；本机 cron 直跑链路经用户/cron 环境注入（run_cron.sh 之上不自建密钥管理）；声明 forge 而缺对应 token env 在**准入早期 fail-fast**（与缺 host 同级，不烧 dev loop 后才在 push 处 401）。
*备选否决*：credential store 落盘（token 明文进 ~/.git-credentials，违背凭据不落盘不变式）；remote URL 内嵌 token（token 进 `git remote -v` 可见面与 journal 风险）。

### D6：vault 增可选键不动必需键结构；派发必需键按 forge 判定
payload 自由 dict 天然支持新键；`VAULT_REQUIRED_KEYS`（三值）不动，新增 `forge_required_keys(forge)` 映射（github→GH_TOKEN / gitlab→GITLAB_TOKEN / gitcode→GITCODE_TOKEN），`lifecycle._credentials` 按项目 forge 取并集判定。面板/PUT 增两个可选 password 框。
*备选否决*：GH_TOKEN 改名 GIT_FORGE_TOKEN——破坏现产 vault 记录与面板契约，且「一键一平台」语义更清晰不漂移。

### D7：实施排序 GitLab → GitCode → GitHub issue
GitLab 先（API 稳定、本地可控无限速、可即时端到端验证）；GitCode 次（限速与响应混血细节多，需真实仓冒烟）；GitHub issue 维度最后顺带（gh api 一行端点，风险最低）。每平台独立可交付：GitLab 合入即产生价值，不依赖后两者。

## Risks / Trade-offs

- [GitCode 限速 400/分] → 采集与对账同平台串行 + 最小间隔；429 退避一次仍败按 UNKNOWN/预取失败暴露（绝不静默空结果）
- [GitCode PR 响应 MR 混血（state/字段名跨血统）] → 归一只在 adapter 边界做一层，配真实仓快照 fixture 锚定（防 API 演进漂移）
- [GitLab 自建版本差异（老版本 v4 字段缺省）] → 只用 v4 核心字段（iid/state/source_branch/target_branch）；GitLab v4 **无请求级 merge_method 参数**（2026-10-23 审核纠偏：merge method 是项目级设置）——派发前置校验项目设置（≠ merge commit 即 permanent 报错）+ merge 后验证双 parent（单 parent 即报错暴露；项目若配 squash 会静默产单 parent，正是 revert 对账最怕的形态）
- [gh CLI 包装层遗漏（github 分支偷偷走 REST）] → 按调用面参数快照直比锁死（dev-agent 面 / run_daily 面各自参数集，见 D1），不虚构单一统一形态
- [askpass 脚本残留] → hub 形态落容器内非挂载路径（/tmp）真随容器弃（state 目录是宿主挂载卷，落那里容器弃了脚本不弃）；本机形态落 state 目录（gitignore）run 终态清理、0700 幂等覆盖；脚本本体不含 token（只引用 env 键名）

## Migration Plan

1. forge_api + forge_fetch 纯新增（零触旧路径）→ 2. dev-agent/run_daily dispatch 面分流（forge 缺省 github = 旧路径行为等价，flag-free）→ 3. vault/面板/lifecycle 凭据面 → 4. pa-instance 镜像重建（**确定步骤**：dev-agent.py 变更必入镜像，hub 形态生效依赖重建——不重建则容器内跑旧 dev-agent 无 forge 支持）→ 每步质量门（pa + hub）全绿再进。回滚 = profile 不声明 forge 即回 github 旧路径（秒回退，无数据迁移）。

## Open Questions

- GitLab 项目的 `gitlab_host` 放 profile 还是集中配置（多项目同实例时避免重复声明）？倾向 profile（与 repo 同源，自包含），集中化待真实多项目形态出现再说。（审核补充 2026-10-23：另有一轴——source 侧 host 只管采集、profile 侧只管 dispatch，二者独立互不引用；dispatch 身份键 = host × 全路径，见 D4。）
- GitCode issue 端点字段形态未实测（v5 issues API 文档粒度粗）——实施时以真实仓 curl 快照为 fixture 前先跑一次人工验证；锚定前 `gitcode + issues` 组合源装载即报「暂不支持」（已进 forge-watch spec）。
- fetch persona 是否需要新建 `pa-forge-summarize`（工具面 Read-only）还是复用 `pa-fetch-github-repo`（Bash）——机械预取落盘后 persona 只需 Read，收紧工具面更干净；实施时定。（已定约束：无论选哪个，平台 token 只 narrow-inject 给 forge_fetch 子进程，不进 persona 环境。）
