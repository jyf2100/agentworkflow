# 提案：add-forge-adapter —— Git 托管平台适配层（GitLab / GitCode）

## Why

pa 流水线的 Git 远程面（开 PR、PR 对账、仓库监控采集）目前硬绑 GitHub（gh CLI），团队放在本地 GitLab 与国内 GitCode 上的仓库无法进入流水线：dispatch 的「开 PR + 对账」对非 GitHub 仓直接失败，fetch 采集只认 `gh api repos/…`。随着目标面仓库分布走向多平台，这是能力边界而非可配置项——gh CLI 无 GitCode 官方对应物（GitLab 有 glab 但引入第二个 CLI 只增镜像负担），REST 适配是唯一统一解。

## What Changes

- **新增 forge adapter 层**（纯机械 Python）：`pr_list / pr_create / pr_merge / branch_protection` 四操作 × 三平台（github 走既有 gh CLI、gitlab 走 API v4、gitcode 走 api.gitcode.com/api/v5），按 per-project `forge` 声明分流（非法值 fail-fast 不回落 github）；三态 fail-safe 语义（FOUND / NOT_FOUND / UNKNOWN）从「gh stderr 文案判据」收进 adapter 契约（每实现必须返回三态；REST 分支按端点分型——列表端点 404 = UNKNOWN 看不见项目、单对象端点 404 = NOT_FOUND、401/403 = permanent 单项目终态）。
- **fetch 采集泛化**：sources.yaml 新 `kind: forge-repo`（带 `forge` / `host` / `watch: [commits, pulls, issues]` 维度声明；既有 `github-repo` 保留兼容）；平台 API 调用收进机械脚本（token 经 env narrow-inject 给预取子进程，不进 persona 环境），persona 只做 summarize（语义活）。**issue 监控为全新维度**——现状连 GitHub 侧都只采 commits+pulls。采集失败为源级二元失败（failed 源清单登记、report 可见）。
- **dispatch 路径适配**：`dev-agent.py` PR auto-merge 与 `run_daily.py` dispatch 面 ×4（准入分支保护 `check_branch_protection` + 对账 `_lookup_pr` / `count_inflight_prs` / 分支查重）按 forge 分流；git 远程操作天然 host 无关，全部（clone/fetch/ls-remote/push）经 GIT_ASKPASS 注入凭据（token 不进命令行/remote URL/日志）；声明 forge 而缺对应 token 在准入早期 fail-fast；dispatch 身份键 = host × 仓库全路径。
- **凭据面增键**：vault 增可选键 `GITLAB_TOKEN` / `GITCODE_TOKEN`（`GH_TOKEN` 语义不漂移——每键=每平台，但从无条件必填改为按项目 forge 条件必需：gitlab/gitcode-only 用户不被迫造假 GH_TOKEN）；hub 面板凭据页加平台 token 可选框，**留空 = 保留既有键值**（重提交不清已存 token）；`lifecycle._credentials` 必需键按项目 forge 判定。
- **仓库路径概念修正**：GitLab 仓库路径可多级（`组/子组/仓`），现 `repo_owner_repo` 正则按两级 `owner/repo` 写死——引入统一的「仓库路径」提取/归一（GitLab API 侧 URL-encode 全路径）。
- hub 队列 / 容器生命周期 / 镜像钉定 / stale_check 全平台无关，**零改动**；pa-executor 镜像**零新增依赖**（REST 用 stdlib）。

## Capabilities

**New Capabilities**（各成新 spec）：
- `forge-adapter` —— 三平台 Git 托管 API 适配层：三操作契约 + 三态 fail-safe 语义 + 凭据 env 约定 + 仓库路径归一。
- `forge-watch` —— 多平台仓库监控采集：sources.yaml `forge-repo` 源、`watch` 维度（commits/pulls/issues）、机械预取 + persona summarize 分工、token env 注入链。

**Modified Capabilities**（需 delta spec）：
- `fail-safe-dispatch` —— 三态判据从 gh CLI 特有实现细节上升为 forge adapter 契约（行为语义不变，判据来源与归属变更；非 GitHub 平台获得同语义保障；REST 401/403 不属三态——单项目终态 skip + 高声暴露，不炸整段）。
- `hub-credential-vault` —— payload 增可选键 `GITLAB_TOKEN` / `GITCODE_TOKEN`；必需键判定从固定三键改为「按项目 forge 要求对应键」；录入留空 = 保留既有键值。
- `execution-hub` —— 容器凭据明确为派发时点快照（跑中 vault 变更不影响在跑 run）。

## Impact

- **代码**：新增 `scripts/forge_api.py`（adapter）+ `scripts/forge_fetch.py`（采集预取）；改 `dev-agent.py`（PR path 分流）、`run_daily.py`（dispatch 面 ×4 分流 + `repo_owner_repo` 泛化（身份键 = host × 全路径）+ fetch 配置）、hub `vault_store.py` / `lifecycle.py` / `panel.py` / `api.py`（凭据面）；pa-instance 镜像重建为**确定步骤**（dev-agent.py 变更必入镜像，不重建则容器内跑旧 dev-agent 无 forge 支持）。
- **风险**：GitCode 限速 400 次/分、4000 次/时（对账轮询与采集分页需节流，采集串行缺省 1s 间隔可配）；GitLab v4 **无请求级 merge_method 参数**（merge method 是项目级设置——派发前置校验项目设置 + merge 后双 parent 验证）；GitCode PR 响应为 MR 底层混血（state 枚举 `opened/merged/closed` ≠ GitHub `open/closed`，adapter 内归一到封闭枚举 `{open, merged, closed}`；GitLab `locked` 保守归 open 算在途，防 single-flight 低报超额投递）。
- **兼容**：GitHub 路径现产**行为等价**（gh CLI 分支保留原实现与测试基线，github 调用面按参数快照直比锚定——装载代码因新增 forge 字段解析而变，无 forge 声明时行为不变）；`github-repo` 采集 kind 不删不改。
- **不涉及**：hub 队列/容器编排、镜像构建链、MM 通知、stale_check（GitHub template repo 专用）、控制面/目标面边界（ADR-0001 不变）。
