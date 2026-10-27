# Tasks: add-forge-adapter

排序依据 design D7：GitLab → GitCode → GitHub issue；每平台独立可交付。（本体论审核 2026-10-23 修订：分支保护第 4 操作、凭据全 git 操作覆盖、三态按端点分型、locked 归一、窗口口径钉死等已并入）

## 1. forge adapter 基座（纯新增，零触旧路径）

- [x] 1.1 `scripts/forge_api.py`：`ForgeConfigError`（permanent）+ 三态结果（FOUND/NOT_FOUND/UNKNOWN）+ `ForgeApi.for_profile(prof)` 工厂（forge 缺省 github；非法 forge 值 fail-fast 不回落 github；gitlab 缺 host fail-fast）；stdlib urllib 零新依赖【实施回填：三态复用既有 `external_state.ExtState`/`ExtResult`（全 pa 单套三态枚举，未另造 LookupOutcome 实体）】
- [x] 1.2 GitLab REST 实现：`pr_list`（`/api/v4/projects/{urlencoded}/merge_requests?state=…`）/ `pr_create` / `pr_merge` / `branch_protection`（protected-branches 端点）；PAT `PRIVATE-TOKEN` 头；三态按 D2 端点分型判据（列表端点 404 → UNKNOWN）；merge **无请求级参数**——项目 merge method 设置前置校验（≠ merge commit 即 permanent）+ merge 后双 parent 验证
- [x] 1.3 GitCode REST 实现：`/api/v5/repos/{o}/{r}/pulls` 四操作（merge 带 `merge_method=merge`）+ branch_protection；Bearer 头；state 归一到封闭枚举 `{open, merged, closed}`（opened→open；gitlab locked→open 保守算在途）；同 D2 判据
- [x] 1.4 github 分支 thin wrapper：按调用面两命名入口（dev-agent 面 cwd+抛异常契约 / run_daily 面 -R+三态契约）共享命令构造，委托既有 gh 调用序列；直比按调用面参数快照断言（4 种参考形态各锚各的，不做假统一）
- [x] 1.5 多级路径：`repo_full_path`（全路径提取，两级形态与旧 `repo_owner_repo` 逐字节一致）+ 旧名 alias；GitLab URL-encode 全路径；dispatch 身份键 = host × 全路径（DISPATCH_LOCKS / already_dispatched / 查重键改造）
- [x] 1.6 单测：mock urllib（注入状态码/响应体）——三平台 × 四操作 × 三态矩阵；列表端点 404→UNKNOWN、单对象 404→NOT_FOUND；401/403→ForgeConfigError；merge 409→permanent、405→UNKNOWN（组5.1 实证修订：MR mergeability 瞬态窗口 / 已 merged 重放混装，permanent 会误杀可自愈路径——见 spec pr_merge 分类）、406/429/5xx/坏 JSON→UNKNOWN；空列表→FOUND；locked 归一；错误消息不含 token 片段断言

## 2. dispatch 链接入（forge 缺省 = 旧路径行为等价）

- [x] 2.1 profile 装载：`forge` / `gitlab_host` 解析 + 校验（非法值 / 缺 host fail-fast）+ 声明 forge 而缺对应平台 token env 早期 fail-fast（准入期报错，不烧 dev loop）；缺省 github
- [x] 2.2 `dev-agent.py` PR path 分流：`_pr_automerge` / `_lookup_pr` 幂等模板按 forge 走 adapter `pr_create/pr_list/pr_merge`（github 分支 gh 命令序列不变）；merge 双 parent 语义跨平台断言（gitlab 走前置校验 + merge 后验证）
- [x] 2.3 `run_daily.py` dispatch 面分流 ×4：`check_branch_protection` + `_lookup_pr` + `count_inflight_prs` + devslug 分支查重；消费 adapter 三态（FOUND-空 按调用点既有语义映射）；UNKNOWN 阻断路径与现状 gh 分支一致；ForgeConfigError → 单项目 skip 终态 + 高声暴露（不炸整段）【实施回填：`reconcile_pr` 补开 PR 触点同步分流（pr_create REST 化 + pr_state 大小写归一），UNKNOWN → blocked_external_state 保留分支】
- [x] 2.4 GIT_ASKPASS 注入：askpass 覆盖 run 内全部远程 git 操作（clone/fetch/ls-remote/push，进程级 env + `GIT_TERMINAL_PROMPT=0`）；脚本本体只读 env 不含 token；hub 形态落容器内非挂载路径（真随容器弃）、本机形态落 state 目录 run 终态清理（0700 幂等覆盖）；remote URL 保持无凭据
- [x] 2.5 单测：对账三态消费（UNKNOWN → blocked_external_state）；401/403 → 单项目终态（不影响同批其他项目）；push/命令面无 token 断言；github 路径按调用面直比（无 forge 声明时参数快照一致）

## 3. 凭据面（vault 增键 + 派发判定）

- [x] 3.1 `vault_store.py`：placeholder 判据覆盖 GITLAB_TOKEN / GITCODE_TOKEN（同阈值黑名单口径）；`forge_required_keys(forge)` 映射（GH_TOKEN 仅 github/未声明项目）
- [x] 3.2 `lifecycle._credentials`：必需键 = ANTHROPIC 两值 ∪ 按项目 forge 的平台 token（GH_TOKEN 仅 github/未声明项目必需，gitlab/gitcode-only 用户不被迫造假 GH_TOKEN）；缺 → fail-fast abandon 列缺失键
- [x] 3.3 面板 + PUT/POST 录入：GH_TOKEN 与两平台 token 可选 password 框；留空 = **保留既有键值**（非 replace 抹除）+ 面板提示；占位拦截；响应只显字段名
- [x] 3.4 hub 单测：gitlab 项目缺 GITLAB_TOKEN → abandon 明示；重提交留空保留既有平台 token；gitlab-only 用户无 GH_TOKEN 可正常派发；占位形态拒绝

## 4. fetch 采集（forge-repo 源 + issue 维度）

- [x] 4.1 `sources.yaml` schema：`kind: forge-repo`（forge/gitlab_host/repos/watch/window）；`github-repo` 原样保留；装载校验（非法 forge 值 / 缺 host fail-fast；gitcode+issues 在 fixture 锚定前报暂不支持）
- [x] 4.2 `scripts/forge_fetch.py` 机械预取：三平台 × commits/pulls/issues 拉取 → 窗口过滤（时间戳口径钉死：commits=author date、pulls/issues=updated_at）→ state 归一 → 归一 markdown 落盘；token narrow-inject 给预取子进程（不进 persona env）；gitcode 串行节流（缺省 1s 可配）+ 429 退避一次；受 fetch 段 TIMEOUT 约束、超时按该源失败；失败非零退出（净化消息）
- [x] 4.3 混入与敏感过滤：GitHub issues 剔除 `pull_request` 字段条目；GitLab 排除 `confidential=true` 条目
- [x] 4.4 fetch persona 接线：预取产物 → summarize（persona 工具面收紧 Read-only 或复用既有，实施时定——若复用 Bash，平台 token 仍不进 persona 环境）；输出契约一行 JSON items 不变【实施回填：新建 pa-fetch-forge-repo persona（tools: Read），run_persona 三层 env 穿参；tools-only 调用形状保持原 5 参零变化（env 仅 forge-repo 面出现）】
- [x] 4.5 fetch 失败登记：fetch 汇总 json 增 failed 源清单字段（源级二元失败，不借三态术语），report 渲染可见【实施回填：既有 kind 的 per-source exception 也进 failed_sources（泛化）；无失败时报告零新增行】
- [x] 4.6 单测：源装载分支（含非法值 / gitcode+issues 拒绝）；预取归一（三平台 state → 封闭枚举）；窗口口径（updated_at 边界）；混入/confidential 过滤；节流/退避；radar 消费形态（items 结构与既有同构）【scripts/test_forge_fetch.py：40 测，quality 全绿（仅预存环境性失败）】

## 5. 端到端验收

- [x] 5.1 GitLab 真实仓端到端：profile（forge: gitlab + host + 多级路径仓 + token env）→ hub 建项目 → 手写 PRD 入队 → dispatch 开 MR → 分支保护/对账三态 → merge 双 parent（含 merge method 设置前置校验路径）
  - 2026-10-26 **adapter 级**全过（术语：adapter 级 = forge_api 直调契约面；hub 全链 = hub 建项目→PRD 入队→容器内 dispatch）：183 自建 GitLab `http://172.32.153.183:8180`，`pa-qa/sub/proj` 多级路径，12/12（fetch 三维 + dispatch 四操作含分支保护 / MR merge 双 parent + 三态负例）。真实验收产出三修，契约细节已回填工件、此处只留指针：① `gitlab_host` scheme 形态（spec「三平台统一操作契约」Requirement）；② 405 permanent→UNKNOWN（spec pr_merge 失败分类 + tasks 1.6）；③ pr_merge 空 title 不发 merge_commit_message 字段（实现内注释锚定）。
  - 2026-10-26 **hub 全链**全绿收官（queue 40 一次过 attempt=0，pa-executor:v0.1.7 digest ff2e57b4）：hub 入队 → 容器拉起 → 容器内 inject→critic（REVISE 回环→round 2 pass）→ dev loop → 测试门 → 独立验证 → pa-verify r1 PASS → rebase clean → MR !9 → **容器内自主 merge 6990c538 双 parent（f14a470e + f3f996a8）** → post-merge main 全量测试绿；API 对账：MR merged / parent_ids==2 / main tip==merge_commit / 交付物内容逐段命中 / queue done。hub 全链排障产出三修（镜像内验证 + 单测锚定）：#26 `remote_host_and_path` 带端口 URL 正则（端口曾沦为路径首段→dev-agent 面 404，run_daily 面幸免——两面构造差异暴露）；#27 run 锁加 host 字段跨容器判陈旧（hub 每 run 新容器，残锁 pid=1 在新 PID namespace 恒判活→90min 全堵）；#28 `_merge_405_converge`（MR create 后 mergeability 异步窗口轮询收敛，405 同码混装瞬态/幂等）。运维教训：**同 PRD 复跑 + main 已含验收产物 → dev 空转两轮 off_track 止损（judge 判定正确非误判，20 turn 零 Write）→ 复跑须换增量型 PRD**；`docker start` 已退出的 run 容器会经 entrypoint 重跑 run_daily（自伤性拿锁/lease），诊断只 `docker logs`/`docker cp`。
- [ ] 5.2 GitCode 真实仓端到端（含限速实测）+ GitCode issues API 字段快照 fixture（先人工 curl 验证 design.md Open Question「GitCode issue 端点字段形态未实测」，锚定后放开 gitcode+issues）
- [x] 5.3 GitHub issue 监控冒烟：既有仓 + watch: [commits, pulls, issues]（与 github-repo kind 同窗口结果一致）
  - 2026-10-27 冒烟全过（jyf2100/agentworkflow，7d 窗口，本机直跑 forge_fetch）：REST 三维 **38/0/2 == gh CLI 同口径直查完全对齐**（commits 按 author date / pulls+issues 按 updated_at 同 jq 过滤对照）；issues 剔 PR 混排 + state 归一真机确认（#2 closed / #3 open，无 PR 混入）；M8 截断声明两次真机验证（limit=30 与 100 均拉满即声明——判据为**过滤前**原始返回达 per_page 上限，窗口过滤后条数可小于上限，fail-safe「可能」语义保守正确）；L2 forge 前缀文件名 ✓；token narrow-inject 流式 env，坏 token 首跑时错误消息不含 token 片段（净化面真机确认）。运维注记：本机 gh 无 `auth token` 子命令（版本老），取 token 走 `~/.config/gh/hosts.yml` 的 `oauth_token` 字段。
- [x] 5.4 回归：GitHub 全链（无 forge 声明）行为与基线一致（canary/既有测试套）；pa + hub 双质量门全绿；pa-executor 镜像重建（**pa 变更面全量入镜像**：dev-agent.py + forge_api.py + forge_fetch.py + run_daily.py + persona 定义 + specs——缺一即容器内 ImportError；v0.1.5 早于 forge 三修，需重建 v0.1.6）后 hub 冒烟，冒烟含两点：①容器内 forge 面（forge_api import + 三态判据抽查）②**派发时点快照不变式**（execution-hub spec 声明：起容器后 vault 凭据记录变更不影响在跑 run，只影响后续派发——跑中改 vault 记录、在跑 run 行为不变的显式验收）
  - 2026-10-27 全过（本机即 184：hub 容器 deploy-pa-hub-1 在本机跑，无需 ssh）。**双质量门**：pa + hub quality.sh 全绿。**①容器内 forge 面**：pa-executor **v0.1.7 现成直接验**（tasks 原文「需重建 v0.1.6」已过时——v0.1.7 已含 #26/#27/#28 三修）：forge_api/forge_fetch import + for_fetch 工厂分派（github→_GithubRestApi）+ gitlab 缺 host → ForgeConfigError + github 缺 GH_TOKEN fail-fast + ExtState 三态枚举 + fetch_commits/pulls/issues 三维方法面全在。**②派发时点快照不变式**（编造断言 PRD 吃 critic drop 的便宜形态，零外发零 dev loop，$0.065/run）：queue #43 跑中 vault-import 换 GH_TOKEN 为 rotated 值 → 在跑容器 env 指纹恒旧值（len40）+ run 正常终态 done（critic 3 major drop，exit 0）；紧接 #44 后续派发 → 新容器 env=新值（len36，tail 比对命中）→ **「不影响在跑、只影响后续派发」双向闭环**；验后 vault 已恢复原值（指纹比对后幂等导入）。**GitHub 全链回归**：既有测试套（无 forge 声明参数快照直比单测锚定 + 全套 pytest 绿）+ 5.3 真机 REST 三维与 gh CLI 对齐；canary（shadow-run）属 roc 真身机运行面——本机冷 checkout 无 `.project-auto/` 运行前提（prep 即「profile 缺」），环境事实非回归，不以本机 canary 为验收门。运维注记三则：PRD 入队必须落 `/data/pa`（内外同路径 bind；容器内 /tmp 路径传 docker daemon = 空 bind → 「--inject-prd 文件不存在」attempt 三连秒败，#41 实证）；`--inject-prd` PRD frontmatter.project 必填且须命中 profile 名（#42 实证）；hub.env TAG_DEFAULT v0.1.6→**v0.1.7 已升**（文件面，gitignore 不入仓；需重启 deploy-pa-hub-1 生效，此前队列继续固化 v0.1.6 派发）。
  - 2026-10-27 补遗·**重启后真全链冒烟**（drop 探针不覆盖 dispatch/dev/MR 段，补真链路）：hub.env 升 v0.1.7 + compose recreate 生效后，queue **#47 增量型 PRD 一次过全链**（attempt=0）：critic **PASS 0 issue**（首轮）→ dispatch single-flight 串行投递 → dev loop → 测试门 → **verify r1 PASS**（turns=4）→ rebase CLEAN → **容器内自主 merge MR !10（merge commit 4741b1de）** → post-merge main 全量测试绿 → done。API 对账：MR 10 merged、merge commit 双 parent（6990c538 main 旧 tip + b2287957 dev 分支）、main 上 docs run45 record 段 + test_run45_record_section 函数逐字命中。**前序排障两笔**：①queue #46 首版 PRD 照 5.1 v2 模板被 critic 两轮 REVISE 卡 traceability——「MR9/run40 现状/v0.1.7 派发」全为无佐证断言，按 revisions_needed 真机采四段佐证（MR9 API merged+sha / main docs+tests raw 片段 / #45#46 固化 tag+inspect 记录）落 `.project-auto/state/inbox/`，PRD frontmatter `source_path` 指向附件后 0 issue 过闸——**QA 仓冒烟 PRD 的可过闸形态 = source_path 佐证附件 + 验收锚定佐证原文**（5.1 v2 无佐证过闸属当时 critic 宽松窗口，不可复现不作为模板）；②宿主 /data/pa 属主 hiroute，ubuntu 无写权，inbox/佐证与 PRD 均经 `docker cp` + 容器内 cp 落 /data/pa（同路径 bind，hub 与 run 容器均可见）。
