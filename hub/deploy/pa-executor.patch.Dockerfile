# pa-executor 补丁版构建：FROM 已有 tag + COPY 变更文件（全量重建用 pa-executor.Dockerfile）
# 2026-10-23 v0.1.4：pa 侧错误可见性加固（exit_diag）进镜像——hub 首跑 #3 实证
# claude "Not logged in" 只在 stdout 信封、stderr 为空，旧镜像内 run_daily 吞成空错误。
# 补丁只动两文件（run_daily.py / persona_call.py 的 exit_diag + raise 行），
# 其余层与 v0.1.3 byte-identical（增量 build 不重跑 apt/npm/pip 网络层，行为等价性风险最低）。
# build context = pa template 投影 checkout（补丁先打进去再 build）：
#   docker build -f hub/deploy/pa-executor.patch.Dockerfile \
#     -t pa-executor:v0.1.4 /mnt/disk01/workspaces/worksummary/pa-instance
FROM pa-executor:v0.1.3

USER root
COPY Projects/项目推进流水线/scripts/run_daily.py /opt/pa/Projects/项目推进流水线/scripts/run_daily.py
COPY Projects/项目推进流水线/scripts/persona_call.py /opt/pa/Projects/项目推进流水线/scripts/persona_call.py
USER pa
