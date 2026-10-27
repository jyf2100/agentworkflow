# pa-executor 补丁版构建：FROM 已有 tag + COPY 变更文件（全量重建用 pa-executor.Dockerfile）
# 2026-10-26 v0.1.7（续注，digest ff2e57b4）：同日 hub 全链逐层排障至全绿（MR !9 容器内自主
#   merge 6990c538 双 parent + post-merge 绿），镜像内叠代的关键修复（编号 = 排障序）：
#   #17 dev-agent git_diff_stat 补 untracked（ls-files others 段，纯新增文件 PRD judge 恒空误止损）；
#   #19/#20 is_exec_junk 执行面遥测谓词（publish 与 judge diff 视角共享清单，遥测不挤占产出证据）；
#   #21 semantic_gate read_fn 注入；#26 forge_api remote_host_and_path 带端口 URL 正则（端口曾沦为
#   路径首段 → dev-agent 面 404）；#27 run_daily run 锁加 host 字段跨容器判陈旧（残锁 pid=1 在新
#   容器 PID namespace 恒判活）；#28 forge_api _merge_405_converge（MR mergeability 异步窗口轮询
#   收敛，405 同码混装瞬态/幂等/永久）。运维锚点：诊断已退出容器只 docker logs/cp（start 会经
#   entrypoint 重跑 run_daily 自伤性拿锁）；rsync 目标必须本文件 COPY 对应的
#   /tmp/pa-template-v0*/Projects/项目推进流水线/scripts/（拷错层级 = COPY 层缓存命中出旧镜像）。
# 2026-10-26 v0.1.7：组5.1 hub 全链排障两修（dispatch 链 gitlab 分支首次真跑暴露）：
#   ① persona cwd 错位（容器 WORKDIR=/workspace ≠ vault 根约定 → critic Read 相对路径失败
#     机械 drop）——修在 hub runner --workdir（非镜像面），镜像零变更；
#   ② dispatch 身份语义错位：repo 输入是 profile repo 字符串非本地仓路径，repo_full_path
#     （remote 提取）恒 None → gitlab 准入必炸「缺仓库全路径」。修 _forge_api_for
#     （full_path=repo 字段直用）+ dispatch_key（prof 声明面 host×全路径，D4 真正生效）
#     + forge_api.bare_host（声明形态归一）。变更为 scripts/ 内增量。
# 2026-10-26 v0.1.6：forge gitlab 三修（dabaf4b：gitlab_host scheme 支持 / merge 405→UNKNOWN /
#   空 title 不发 merge_commit_message——组5.1 183 自建实例实证产出）+ dev handoff 工件落地
#   （d07fd6b：handoff.py 新模块 + dev-agent 写侧围栏）。变更为 scripts/ 目录内增量，
#   目录级 COPY 天然覆盖；build context = v0.1.6 投影副本：
#   docker build -f hub/deploy/pa-executor.patch.Dockerfile \
#     -t pa-executor:v0.1.6 /tmp/pa-template-v016
# 2026-10-26 v0.1.5：add-forge-adapter 本体（forge_api/forge_fetch + dispatch/准入/reconcile 接入
# + pa-fetch-forge-repo persona）+ 本体论审核修复（M1 permanent 分桶/M2 exit 14/CONTEXT 词条）+
# critic 双通道第二臂（PA_CRITIC_ARM2 默认 off）进镜像。变更面大（scripts/ 近半动过），
# 按**目录粒度** COPY 防漏文件（逐文件清单漏一个新模块 = 容器内 ImportError）：
#   docker build -f hub/deploy/pa-executor.patch.Dockerfile \
#     -t pa-executor:v0.1.5 /tmp/pa-template-v015
# （build context = v0.1.5 投影副本，门禁已全绿；pa-instance checkout 同步待投影仓推送后 checkout）
# 2026-10-23 v0.1.4：pa 侧错误可见性加固（exit_diag）进镜像——hub 首跑 #3 实证
# claude "Not logged in" 只在 stdout 信封、stderr 为空，旧镜像内 run_daily 吞成空错误。
# 补丁只动两文件（run_daily.py / persona_call.py 的 exit_diag + raise 行），
# 其余层与 v0.1.3 byte-identical（增量 build 不重跑 apt/npm/pip 网络层，行为等价性风险最低）。
# 2026-10-27 v0.1.8（add-executor-eng-skills）：镜像内置工程技能包——mattpocock-skills 1.3.1
#   engineering 面 20 skill 入库快照 hub/deploy/executor-skills/（352K + PROVENANCE），COPY 进
#   投影 /opt/pa 并拷至 /home/pa/.claude/skills/ 供容器内 dev loop 会话发现；persona 工具面
#   （agent 定义 tools: 收窄）不受影响。build context = v0.1.7 树 + skills 增量的投影副本。
# 2026-10-27 v0.1.9（critic 手写 PRD 判据修复）：cc-web-control codex 支持 PRD 三连 drop 实证
#   模型只扫 source_path/inbox 两个机械锚点、不逐条核正文背景交代，违反 F12 单标准「不看出身」
#   （agent 定义 §traceability 原文已写明「背景交代=手写常态形态，任一可溯即有据」）。
#   修两面：①agent 定义加硬规则（信息源缺失不构成无据证据，必须先核正文出处形态）
#   ②run_daily critic_prompt 空源不留裸空白（尾随「信息源原文：」空白正是诱导判无据的形态），
#   显式交代手写形态语义 + 回归测试 test_critic_prompt.py。变更 3 文件（scripts/ 目录级 COPY
#   天然覆盖前两个+测试；agent 定义须 RUN 重刷 /home/pa——base 层拷贝不随 patch 层 COPY 更新）。
#   build context = v0.1.8 树 + 3 文件增量的投影副本：
#   docker build -f hub/deploy/pa-executor.patch.Dockerfile \
#     -t pa-executor:v0.1.9 /tmp/pa-template-v019
# 2026-10-27 v0.1.10（dispatch github 面容器首跑解锁）：cc-web-control 全链测试推进到 dispatch
#   暴露准入死锁——准入 2（2791：fapi is None and not owner_repo → skip）先于仓准备 clone 段
#   （2855），owner_repo 靠 git -C 本地仓提取，容器首跑仓不存在恒 None → github 面必 skip
#   （gitlab 面 fapi≠None 绕过此门直奔 clone 段故 #4-#7 可达；mac 真身 workspace 仓常驻从未暴露）。
#   修 repo_owner_repo：git 提取失败回退 URL 形态正则解析（hub 容器 profile repo 常态是 URL；
#   取末两级对齐 git remote 提取语义）+ test_repo_owner_repo_url.py。2 文件增量。
# 2026-10-27 v0.1.11（github 面容器形态 dispatch 打通）：#12 只给 forge 面补的两通道，
#   github 面对齐——①幂等 ls-remote：repo 为 URL 形态且无 fapi → 直连 URL（容器无本地仓，
#   -C repo 必炸；公开仓无凭据可行，私有 401 → UNKNOWN fail-safe 不变）②clone 落点：repo
#   URL → vault/workspace/<name> 重映射（URL 当 Path 炸 https:/ 协议前缀），仓随容器弃对齐
#   gitlab 面。+ _repo_local_path 抽函数。3 文件增量（run_daily + 2 测试）。
# 2026-10-27 v0.1.12（m10 缺口双修——github 面容器 dispatch 全链首跑到 dev loop 后暴露）：
#   ①push 凭据：止损清场 push --delete 无凭据挂起 30s 超时（GIT_ASKPASS 对 github no-op）。
#     修 _askpass_env_setup/_teardown：github 面容器形态（_in_container）也写 askpass（GH_TOKEN
#     由 hub vault --env-file 注入，脚本按 host 分流已有 github 分支）；本机 github 保持 {} 零变化
#     （mac 真身 ssh/keychain 通道不动）。
#   ②redirect 失忆死循环：dev 30 turn 全在读文件零产出被判 off_track，redirect 新 session（H1
#     全新 query）无前任记忆重读同样文件 → 再 off_track → 止损 exit 15。修 semantic_gate 新增
#     prior_progress_note（run_log tool_use 重放：已读/已写文件 + diff 现状，纯机械零 LLM）注入
#     build_redirect_prompt（prior_note 参数，缺省零变化）+ dev-agent redirect leg 接线。
#   3 文件增量（run_daily + semantic_gate + dev-agent）+ 2 测试文件扩充（test_forge_dispatch
#   github askpass 两态 / test_semantic_gate prior_note 4 用例）。
# 2026-10-27 v0.1.13（m11 缺口：hint 白名单系统性误拦 + 拒因反馈环）：m11 实证 turn 20/30/40
#   连续 3 轮 judge_failopen_bad_hint——progress 写指引爱用内联代码标记（`git diff`），单反引号
#   在禁用表 = 系统性误拦全部 LLM 指引；且拒因不回传无状态 persona → 同样的非法 hint 重复产出。
#   三件套：①白名单放宽（去单反引号——hint 注入面是 `> ` 引用文本语境，markdown 常态无执行
#   语义；命令替换 $() 与围栏 ``` 仍拦；上限 500→2000）②拒因落盘（failopen 事件带 reason +
#   hint_sample，state 带 last_hint_rejection）③反馈环（build_progress_prompt prior_rejection
#   注入 → judge_direction 透传 → dev-agent _judge_fn 取用即清）。2 文件增量
#   （semantic_gate + dev-agent）+ 1 测试扩充（test_semantic_gate 既有用例对齐新语义 + 4 新用例）。
# 2026-10-27 v0.1.14（m11 缺口③：发布门证据采集形态过窄误拦真绿）：m11 终态
#   blocked_test_gate——dev 修完 main 基线 bug（server.cjs 缺 trajectory_scan require，
#   4 测基线红）后以 `rm tmp.js && npm test` 跑出 1132 pass 全绿，但 is_clean_test_cmd
#   对 `&&`/`;` 链一概拒收 → 门手里最后证据停在修复前裸 npm test exit=1 → 误拦。
#   修链式拆段采信（exit code=末端段；`&&` 短路=前置失败码≠0 fail-safe；每段仍拒
#   |><&`/$( ——管道吞 exit code 假绿风险不变）+ node --test 单文件形态补认。
#   2 文件增量（dev-agent + 新测试 test_clean_test_cmd.py）。
# 2026-10-27 v0.1.15（m12 缺口④：零 diff 探索期 off_track 误判）：v0.1.13 白名单
#   修复生效（无 failopen、hint 真注入 redirect 真发生）后反而暴露——redirect 新会话
#   10 turn 纯读（消化前情 + 大仓正常起步）→ 第二次 off_track 耗尽 exit 15；m11 是
#   靠「hint 被拦 fail-open 不计数」活到写码期的。根因：off_track ≡ 有产出且方向错，
#   零 diff = 无方向可判（judge 瞎猜）。修 run_checkpoint K 边界后零 diff 跳过
#   （judge_skip no_diff_yet：不调 judge 不算 round 不污染 verdict）；探索慢/死循环
#   由 maxTurns/dev 时限/N_STALL 兜底。git 失败占位串非空 → 照常 judge（fail-open 保留）。
#   2 文件增量（semantic_gate + 测试扩充）。
# 2026-10-27 v0.1.16（m13 缺口⑤：forge=None 默认 github 面 askpass 短路）：m13 全链历史性
#   走到 merge push（85 turn 全绿 + 独立验证绿）才暴露 push_failed——`_askpass_env_setup`
#   首判定 `not forge → return {}` 把「未声明 forge」当 no-op 短路，但 github 项目 profile 常态
#   恰恰不写 forge（gitlab/gitcode 才显式声明）→ 默认面容器**永远**拿不到凭据（GH_TOKEN 在
#   容器 env 但 git 够不着，could not read Username）。修 setup/teardown 判定：未声明=github
#   面对称处理（容器注入/本机 no-op）+ 补 forge=None 两态测试（旧测试只测显式 forge=github
#   形态——盲区本区）。2 文件增量（run_daily + test_forge_dispatch）。
#   注：v0.1.15 实际从临时 /tmp/patch-flat-v0115.Dockerfile（FROM v0.1.14-flat）build——patch
#   链叠 11 层曾触 containerd max depth exceeded，docker export|import 打平 v0.1.14 为单层
#   flat 基底后续建；本行自此与实际 tag 链对齐（flat 后层深重计，无需再 flat）。
# 2026-10-27 v0.1.17（m14-m16 三连环境偶发双修）：
#   ①思考模式 medium（用户拍板）：persona 调用挂 claude CLI --effort flag（默认 medium，
#     env PA_PERSONA_EFFORT 可覆盖，空串回落 medium 对齐 model routing 语义）——两处 base_cmd
#     镜像（persona_call.run_persona_subproc + run_daily.run_persona），dev loop SDK 会话不经
#     此面不受影响。动机：m14 critic revise 300s 壳内含 PRD 全文重写（正常单跑 900s 量级），
#     长思考+长输出偶发贴爆；压 effort 档换响应稳定。容器 CLI 2.1.220 实证认 --effort。
#   ②幂等 ls-remote 超时重试 1 次（m15 #69 / m16 #70 连续死于同一处）：单次 20s 无重试时
#     时段性网络抖一下 → UNKNOWN → fail-safe 阻断（事后实测 1s 通）。TimeoutExpired 重试 1 次，
#     两次都超时才返错（fail-safe 语义不破）；rc≠0（401 永久形态）不重试。
#   7 文件增量（persona_call + run_daily + 5 测试：test_persona_call/test_run_persona_contract/
#   test_forge_dispatch/test_github_container_dispatch/test_failure_modes——末者旧「单次超时即
#   UNKNOWN」语义对齐「两次都超时」）。
# 2026-10-27 v0.1.18（缺口⑥续：github 面幂等分支扫描改走 gh api）：v0.1.17 的重试上线即验
#   m17 #71 仍阻断——两次都超时（19:50-19:52）。跟进实测破案：同容器内 curl github.com 1s 通、
#   git ls-remote 挂 132s 才 fatal、再下一分钟又秒通——github.com git smart HTTP 端点**分钟级
#   间歇抽**（非封锁非本机配置），重试救不了同时段连挂；而 gh（api.github.com）三轮全稳。
#   修：already_dispatched github 面（fapi=None 且有 owner_repo）分支扫描走 _scan_branches_via_gh
#   （gh api repos/<o>/branches --paginate，与 PR 扫描同通道同凭据，数据同源）；forge 面保留
#   ls-remote+重试（无 gh）。2 文件增量（run_daily + test_github_container_dispatch 4 新用例）。
FROM pa-executor:v0.1.17

USER root
# 运行面（编排器 + persona + 规约）：目录级覆盖
COPY Projects/项目推进流水线/scripts /opt/pa/Projects/项目推进流水线/scripts
COPY .claude/agents /opt/pa/.claude/agents
COPY openspec/specs /opt/pa/openspec/specs
# 文档/样例面
COPY Projects/项目推进流水线/CONTEXT.md /opt/pa/Projects/项目推进流水线/CONTEXT.md
COPY Projects/项目推进流水线/RUNBOOK.md /opt/pa/Projects/项目推进流水线/RUNBOOK.md
COPY Projects/项目推进流水线/bootstrap/samples/profile.example.yaml /opt/pa/Projects/项目推进流水线/bootstrap/samples/profile.example.yaml
# 模板交付物（镜像内不消费，COPY 保与投影一致——下层 patch 的基线一致性）
COPY CLAUDE.md /opt/pa/CLAUDE.md
COPY Dockerfile /opt/pa/Dockerfile
COPY .dockerignore /opt/pa/.dockerignore
# 工程技能包（add-executor-eng-skills，v0.1.8）：进投影 + 拷至容器用户级技能目录（dev loop 可见；
# PROVENANCE.md 随包进镜像，容器内可溯源）
COPY hub/deploy/executor-skills /opt/pa/hub/deploy/executor-skills
RUN mkdir -p /home/pa/.claude/skills \
    && cp -r /opt/pa/hub/deploy/executor-skills/. /home/pa/.claude/skills/ \
    && chown -R pa:pa /home/pa/.claude/skills
# critic 手写 PRD 判据修复（v0.1.9）：/home/pa 侧 agent 定义在 base 层拷贝，
# patch 层目录级 COPY 只覆盖 /opt/pa，须重刷 critic 定义到容器用户级目录。
RUN cp /opt/pa/.claude/agents/pa-prd-critic.md /home/pa/.claude/agents/pa-prd-critic.md \
    && chown pa:pa /home/pa/.claude/agents/pa-prd-critic.md
# workspace 可写（组5.1 hub 全链 #13 实证）：dispatch 的目标仓 clone 落 vault 根下相对路径
# （repo 字段相对 cwd=/opt/pa 解析），COPY 层目录 root 属主 → pa 用户 mkdir Permission denied。
# chown 目录本身即可（内容只读消费；clone 目标子树 pa 自建）。容器形态仓随容器弃（run 级一次性
# clone，state/journal 在挂载卷持久——durable 面不受影响）。
RUN chown pa:pa /opt/pa
# 系统 python 装 pytest（组5.1 hub 全链 #20 实证）：dev 会话的 Bash 工具解析 python3 落
# /usr/bin/python3（venv 前置 PATH 未透传进 claude CLI Bash 工具），`python3 -m pytest` 报
# No module named pytest（52 chars is_error）→ 测试门 test_failed 三连拦。python3-pytest 走
# Debian 包（/usr/bin/python3 默认无 pip），Bash 工具任何 python3 解析都有 pytest。
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends python3-pytest \
    && rm -rf /var/lib/apt/lists/*
USER pa
