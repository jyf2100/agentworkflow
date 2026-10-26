# pa-executor 补丁版构建：FROM 已有 tag + COPY 变更文件（全量重建用 pa-executor.Dockerfile）
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
FROM pa-executor:v0.1.4

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
USER pa
