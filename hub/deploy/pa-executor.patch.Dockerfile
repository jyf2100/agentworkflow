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
FROM pa-executor:v0.1.8

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
