#!/usr/bin/env bash
# build_executor_image.sh — pa-executor 镜像 build 脚手（add-executor-image tasks 3.1/3.2，D3/D4）。
#
# 维护者工具，不进投影（manifest 不纳管；template 用户拿到 Dockerfile 可自行 build）。
# build context = template tag checkout（投影的物化形态）。
#
# 流程（PLAN 序列 = pytest 断言的稳定契约，scripts/test_build_executor_image.py）：
#   [1] checkout   —— --context 复用现成 checkout；--remote clone --branch <tag>
#   [2] verify     —— 四项静态检查（复用 check_release_gates.py；纯本地，dry-run 下也真跑）
#   [3] build      —— docker build + label（template tag / upstream commit（RELEASE_NOTE 锚定）/
#                     组件钉扎版本（ARG 从 Dockerfile 解析，单一真理源））
#   [4] check ×7   —— 容器内自检（D4：id 非 root / python / claude / gh / pytest 收集非空 /
#                     compileall / 密钥模式扫描（模式集 = CREDENTIAL_PATTERNS 动态生成））
#
# 用法：
#   bash build_executor_image.sh --tag v0.1.3 --context <dir> [--image-name pa-executor]
#   bash build_executor_image.sh --tag v0.1.3 --remote <git_url>
#   …  --dry-run        # 打印 PLAN 序列不执行 docker 动作（本地校验仍真跑）
# 退出码：0 全绿 | 2 前置校验/build/自检失败 | 3 用法错误
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATES_PY="$HERE/check_release_gates.py"

# ── 参数 ────────────────────────────────────────────────────────────────────
TAG="" ; REMOTE="" ; CONTEXT="" ; IMAGE_NAME="pa-executor" ; DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag) TAG="$2"; shift 2 ;;
    --remote) REMOTE="$2"; shift 2 ;;
    --context) CONTEXT="$2"; shift 2 ;;
    --image-name) IMAGE_NAME="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "✗ 未知参数：$1" >&2; exit 3 ;;
  esac
done
[[ -n "$TAG" ]] || { echo "✗ 用法：build_executor_image.sh --tag <vX.Y.Z> (--context <dir> | --remote <url>) [--image-name NAME] [--dry-run]" >&2; exit 3; }
[[ -n "$CONTEXT" || -n "$REMOTE" ]] || { echo "✗ 须给 --context 或 --remote 之一" >&2; exit 3; }

fail() { echo "✗ [build-executor] $*" >&2; exit 2; }
REF="${IMAGE_NAME}:${TAG}"

# ── [1] checkout（build context 就位）──────────────────────────────────────
if [[ -n "$REMOTE" && -z "$CONTEXT" ]]; then
  if [[ $DRY_RUN -eq 1 ]]; then
    CONTEXT="<tmp>/pa-executor-ctx-$TAG"
    PLAN_CHECKOUT="git clone --depth 1 --branch $TAG $REMOTE $CONTEXT"
  else
    CONTEXT="$(mktemp -d)/pa-executor-ctx"
    git clone --depth 1 --branch "$TAG" "$REMOTE" "$CONTEXT" || fail "clone/checkout $TAG 失败"
    PLAN_CHECKOUT="git clone --depth 1 --branch $TAG $REMOTE $CONTEXT"
  fi
else
  PLAN_CHECKOUT="复用现成 context：$CONTEXT"
fi

# ── [2] verify：四项静态检查（纯本地，dry-run 也真跑——fail-fast 先于一切 docker 动作）──
[[ -f "$CONTEXT/Dockerfile" ]] || fail "build context 根无 Dockerfile：$CONTEXT（template tag 未纳管 executor-image？）"
DFL="$CONTEXT/Dockerfile"
if [[ $DRY_RUN -eq 0 ]]; then
  python3 "$GATES_PY" --dockerfile "$DFL" || fail "Dockerfile 四项静态检查未过（spec: executor-image）"
else
  python3 "$GATES_PY" --dockerfile "$DFL" >/dev/null 2>&1 || fail "Dockerfile 四项静态检查未过（spec: executor-image）"
fi

# F1b（本体论审核）：build context 内容校验——spec「build context 混入投影外文件」。
# git checkout（真发布场景，clone 即 git repo）：ls-files 差集（未跟踪未忽略）非空
# = tag 内容清单外散文件 → fail（判据严于 .dockerignore 口径——被 dockerignore
# 排但未 gitignore 的散文件同样拦，宁严勿漏）。等价导出（非 git 目录）：无机械
# 判据（manifest 不随投影），告警跳过、review 把守。纯本地校验，dry-run 也真跑。
if [[ -e "$CONTEXT/.git" ]]; then
  STRAY="$(git -C "$CONTEXT" -c core.quotepath=off ls-files --others --exclude-standard)"
  if [[ -n "$STRAY" ]]; then
    echo "✗ [build-executor] build context 混入投影外文件（未跟踪未忽略）：" >&2
    while IFS= read -r f; do echo "  - $f" >&2; done <<< "$STRAY"
    fail "context 内容偏离 template tag（spec: executor-image）"
  fi
else
  echo "  ⚠ 非 git checkout（等价导出）——context 内容清单差集无法机械校验，review 把守"
fi

# label 素材：组件钉扎版本从 Dockerfile ARG 解析（单一真理源）；upstream commit 从 RELEASE_NOTE 锚定
CLAUDE_VER="$(sed -n 's/^ARG CLAUDE_CODE_VERSION=//p' "$DFL" | head -1)"
GH_VER="$(sed -n 's/^ARG GH_VERSION=//p' "$DFL" | head -1)"
[[ -n "$CLAUDE_VER" && -n "$GH_VER" ]] || fail "Dockerfile 缺 ARG CLAUDE_CODE_VERSION / GH_VERSION 钉扎声明"
UPSTREAM_SHA="$(sed -n 's/^.*@ \([0-9a-f]\{7,40\}\).*$/\1/p' "$CONTEXT/RELEASE_NOTE.md" 2>/dev/null | head -1 || true)"
UPSTREAM_SHA="${UPSTREAM_SHA:-unknown}"
LABELS=(
  --label "org.opencontainers.image.version=$TAG"
  --label "io.pa.template-tag=$TAG"
  --label "io.pa.upstream-commit=$UPSTREAM_SHA"
  --label "io.pa.claude-code-version=$CLAUDE_VER"
  --label "io.pa.gh-version=$GH_VER"
)
# 密钥扫描模式集：与 check_release_gates.CREDENTIAL_PATTERNS 同源动态生成（不重复维护）
CRED_RE="$(python3 -c '
import sys; sys.path.insert(0, sys.argv[1])
import check_release_gates as g
print("|".join(p.pattern for _, p in g.CREDENTIAL_PATTERNS))' "$HERE")"
PA_IN_IMAGE="/opt/pa/Projects/项目推进流水线"

# ── [3] build（dry-run 只打印计划，真跑执行 docker build）─────────────────
if [[ $DRY_RUN -eq 1 ]]; then
  echo "PLAN checkout: $PLAN_CHECKOUT"
  echo "PLAN verify-static: python3 check_release_gates.py --dockerfile $DFL（已实际执行并通过）"
  echo "PLAN build: docker build ${LABELS[*]} -t $REF $CONTEXT"
else
  echo "▶ build：$REF（context $CONTEXT）"
  docker build "${LABELS[@]}" -t "$REF" "$CONTEXT" || fail "docker build 失败"
  echo "  ✓ build 完成"
fi

run_check() {  # run_check <desc> <sh 命令>——dry-run 打印计划，否则逐项 docker run
  local desc="$1" cmd="$2"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "PLAN check $desc: docker run --rm --entrypoint sh $REF -c \"$cmd\""
    return 0
  fi
  echo "▶ check $desc"
  docker run --rm --entrypoint sh "$REF" -c "$cmd" || fail "容器内自检失败：$desc"
  echo "  ✓ $desc"
}

run_check id          '[ "$(id -u)" != 0 ]'
run_check python      'python3 --version 2>&1 | grep -q "Python 3"'
run_check claude      "claude --version | grep -q '$CLAUDE_VER'"
run_check gh          "gh --version | grep -q '$GH_VER'"
run_check pytest      "cd '$PA_IN_IMAGE' && python3 -m pytest scripts --collect-only -q >/dev/null 2>&1"
run_check compileall  "python3 -m compileall -q '$PA_IN_IMAGE/scripts'"
# secrets 扫描排除 test_*.py：sanitize/凭据测试的 fixture 本就是凭据形状的合成串
# （如 AKIA…EXAMPLE）——测试职责所系、非机密。语义 = 「非测试载荷零凭据字面量」；
# 测试文件内的真凭据风险由 code review 把守（spec: executor-image 凭据零入镜像 scenario 注记）。
run_check secrets     "grep -rEl '$CRED_RE' /opt/pa /home/pa --exclude='test_*.py' 2>/dev/null; [ \$? -eq 1 ]"

echo "✓ pa-executor build 脚手：$REF（upstream $UPSTREAM_SHA）$([[ $DRY_RUN -eq 1 ]] && echo '—— dry-run 未执行' || echo '，全绿')"
