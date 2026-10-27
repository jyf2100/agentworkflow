# pa-executor 镜像（执行 hub 派发的容器内 pa；spec: execution-hub）
# 2026-10-23 重建归档：原始 build context 已失（一次性目录），本文件从 v0.1.3 镜像
# docker history 层序反提取重建，行为对齐（层序一致，RUN 命令逐字取自 history）。
# build context = pa template 投影 checkout（RUNBOOK §15 部署顺序第 2 步：
#   docker build -f hub/deploy/pa-executor.Dockerfile -t pa-executor:<tag> <pa-instance路径>）
# 基础 node:22-slim（v0.1.3 实际基 = node 22.23.3 bookworm；本机已拉 22-slim 同系）。
FROM node:22-slim

ARG CLAUDE_CODE_VERSION=2.1.220
ARG GH_VERSION=2.102.0

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && arch="$(dpkg --print-architecture)" \
    && curl -fsSL --retry 5 --retry-all-errors --connect-timeout 30 \
       "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${arch}.tar.gz" \
       | tar -xz -C /usr/local/bin --strip-components=2 "gh_${GH_VERSION}_linux_${arch}/bin/gh" \
    && git config --system user.name "pa-executor" \
    && git config --system user.email "pa-executor@invalid"

# 依赖层先于源码 COPY（build 缓存命中：pa 源码改动不重装依赖）
COPY Projects/项目推进流水线/pyproject.toml /opt/pa/Projects/项目推进流水线/
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir "/opt/pa/Projects/项目推进流水线[dev]"

RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force

COPY . /opt/pa

ENV PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# 工程技能包（add-executor-eng-skills）：快照随 COPY . 进投影 /opt/pa/hub/deploy/executor-skills，
# 拷至容器用户级技能目录供 dev loop 会话发现（persona 工具面由 agent 定义 tools: 收窄，不受影响）
RUN useradd -m -s /bin/bash pa \
    && mkdir -p /home/pa/.claude/agents \
    && cp /opt/pa/.claude/agents/*.md /home/pa/.claude/agents/ \
    && mkdir -p /home/pa/.claude/skills \
    && cp -r /opt/pa/hub/deploy/executor-skills/. /home/pa/.claude/skills/ \
    && mkdir -p /workspace \
    && chown -R pa:pa /home/pa /workspace /opt/pa/Projects/项目推进流水线

USER pa
ENV HOME=/home/pa
# onboarding 预置：claude --version 冒烟 + 跳过首次交互向导（headless 前提）
RUN claude --version >/dev/null \
    && printf '{"hasCompletedOnboarding": true}\n' > "$HOME/.claude.json"

WORKDIR /workspace
ENTRYPOINT ["python3", "/opt/pa/Projects/项目推进流水线/scripts/run_daily.py"]
CMD ["--help"]
