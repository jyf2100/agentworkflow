# Dockerfile — pa-executor 执行镜像（openspec/changes/add-executor-image）
#
# 本体：pa-sandbox 的正式化替代（design D9）——补内容契约 + 版本锚定的唯一执行容器镜像。
# build context = template tag checkout（投影的物化形态）；行为契约见 spec: executor-image。
# 凭据零入镜像（F9）：per-user 三值（ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / GH_TOKEN）
#   只在运行时经 --env-file/secrets 注入；build 自检含密钥模式扫描。
# 版本钉扎（design D2）：claude CLI 与 gh 经 ARG 锁定（先例：SDK 钉 >=0.2.128,<0.2.130）；
#   实际组件版本由 build 脚手记入镜像 label（可追溯）。升级 = 显式改 ARG，不接受漂移。
FROM node:22-slim

ARG CLAUDE_CODE_VERSION=2.1.220
ARG GH_VERSION=2.102.0

# ── 系统依赖：python3（bookworm 原生 3.11）+ git + gh（钉版二进制，双架构）──
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && arch="$(dpkg --print-architecture)" \
    && curl -fsSL --retry 5 --retry-all-errors --connect-timeout 30 \
       "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${arch}.tar.gz" \
       | tar -xz -C /usr/local/bin --strip-components=2 "gh_${GH_VERSION}_linux_${arch}/bin/gh" \
    && git config --system user.name "pa-executor" \
    && git config --system user.email "pa-executor@invalid"
    # commit 身份占位（中性）；per-user 真实身份的注入方式归 hub 切片裁决（admit 门会查）

# ── Python 依赖层（先 pyproject 后全量 COPY——依赖不变则层缓存命中）─────────
COPY Projects/项目推进流水线/pyproject.toml /opt/pa/Projects/项目推进流水线/
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir "/opt/pa/Projects/项目推进流水线[dev]"

# ── claude CLI（钉版）───────────────────────────────────────────────────────
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force

# ── pa 投影全量 + venv 入 PATH ──────────────────────────────────────────────
COPY . /opt/pa
ENV PATH="/opt/venv/bin:$PATH"

# ── 非 root 用户（design D6）+ persona 到用户级 ─────────────────────────────
# WORKDIR /workspace 是目标仓 worktree 落点（F6：目标面在执行环境中的投影）——
# 项目级 .claude/agents 不存在于目标仓，persona 必须装到用户级 ~/.claude/agents/。
# pa 投影树归运行用户：自检（compileall/pytest 缓存）需在源码树落地 pyc——即弃容器
# 内合法（隔离边界在容器，不在文件属主；/workspace 之外的可写性无安全含义）。
RUN useradd -m -s /bin/bash pa \
    && mkdir -p /home/pa/.claude/agents \
    && cp /opt/pa/.claude/agents/*.md /home/pa/.claude/agents/ \
    && mkdir -p /workspace \
    && chown -R pa:pa /home/pa /workspace /opt/pa/Projects/项目推进流水线

# ── 首跑状态预置（design D5，spec 验收项）───────────────────────────────────
# 以运行用户预初始化 CLI 状态：headless -p 不被 onboarding/trust 交互卡住；
# 实测发现的额外状态项在此补齐（tasks 4.2）。
USER pa
ENV HOME=/home/pa
RUN claude --version >/dev/null \
    && printf '{"hasCompletedOnboarding": true}\n' > "$HOME/.claude.json"

# ── 入口收敛（design D6）：entrypoint=流水线唯一入口 ────────────────────────
WORKDIR /workspace
ENTRYPOINT ["python3", "/opt/pa/Projects/项目推进流水线/scripts/run_daily.py"]
CMD ["--help"]
