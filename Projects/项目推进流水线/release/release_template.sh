#!/usr/bin/env bash
# release_template.sh — vault → pa-pipeline-template 版本化只读投影发布链（task 2.2，D1/D2）。
#
# 唯一合法的 template 内容来源（specs/template-release「版本化只读投影发布」）：
#   [1] 展开 manifest（git ls-files pathspec → 精确文件清单，gitignore 天然排除）
#   [2] 机械转换（transform.py：规则文件去个人化；其余 passthrough 直拷；失配即退出 2 阻断）
#   [3] 内容自检：禁入路径负例（SPEC.md/docs/reviews/Knowledge/.project-auto）＋
#       规约层逐字节 diff（openspec/specs+docs/adr 转换无规则 → 必须与源逐字节一致）＋
#       通道指导必备（template CLAUDE.md 在 manifest 时强制校验标记节）
#   [4] 发布门：副本内 quality.sh 全绿 ＋ 去个人化 grep 门禁零命中
#   [5] RELEASE_NOTE.md：上游状态义务（commit、未完成 openspec changes 的 in-progress 注记）
#   [6] --publish <remote> <tag>：git init+commit+tag（独立投影仓）；默认干跑（只产副本目录）
#
# 用法：
#   bash release_template.sh --out /tmp/pa-template-dry            # 干跑（2.5 验收）
#   bash release_template.sh --out <dir> --publish <git_url> v0.1.0  # 真发布
# 退出码：0 全绿 | 2 转换失配/自检或门禁失败 | 3 用法错误
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PA_HOME="$(cd "$HERE/.." && pwd)"
VAULT_ROOT_DEFAULT="$(cd "$PA_HOME/../.." && pwd)"
MANIFEST="$HERE/manifest.txt"
TRANSFORM="$HERE/transform/transform.py"
SPEC_LAYER_SH="$HERE/check_spec_layer.sh"

# ── 参数 ────────────────────────────────────────────────────────────────────
OUT="" ; REMOTE="" ; TAG="" ; VAULT_ROOT="" ; VAULT_OVR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --publish) REMOTE="$2"; TAG="$3"; shift 3 ;;
    --vault) VAULT_OVR="$2"; shift 2 ;;       # 测试/异构 checkout 覆盖（默认=脚本所在 vault）
    --manifest) MANIFEST="$2"; shift 2 ;;     # 内容契约清单覆盖（单测 fixture 用）
    *) echo "✗ 未知参数：$1" >&2; exit 3 ;;
  esac
done
VAULT_ROOT="${VAULT_OVR:-$VAULT_ROOT_DEFAULT}"
[[ -n "$OUT" ]] || { echo "用法：release_template.sh --out <dir> [--publish <remote> <tag>]" >&2; exit 3; }
[[ -z "$REMOTE" || -n "$TAG" ]] || { echo "✗ --publish 须同时给 remote 与 tag" >&2; exit 3; }

# 去个人化 grep 门禁 pattern 集（命中任一 = 发布失败；pattern 本身是契约，增删须过 change 评审）
PERSONAL_PATTERNS=(
  '/Users/roc' 'smtp\.newland\.com\.cn' 'vip\.sina\.com' 'juyf@' 'dvs@'
  'newland-smtp' 'sina-smtp' '部门工作跟踪' 'okwinds' '/Users/[a-z]'
  'newland' '\bsina\b' '居燕峰' 'jyf2100' '/mnt/disk01'
)
# 规约层（openspec/specs、docs/adr）零转换规则 → 逐字节一致门禁在 check_spec_layer.sh
# 禁入路径（template 内容契约负例）
FORBIDDEN_PATHS=("Projects/项目推进流水线/SPEC.md" "docs/reviews/" "Knowledge/" ".project-auto/")

fail() { echo "✗ [release] $*" >&2; exit 2; }

# ── [1] manifest 展开 ───────────────────────────────────────────────────────
echo "▶ [1/6] 展开 manifest"
FILES=()
while IFS= read -r line; do
  [[ -z "$line" || "$line" == \#* ]] && continue
  while IFS= read -r f; do FILES+=("$f"); done < <(git -C "$VAULT_ROOT" -c core.quotepath=off ls-files -- "$line")
done < "$MANIFEST"
[[ ${#FILES[@]} -gt 0 ]] || fail "manifest 展开为空"
echo "  共 ${#FILES[@]} 文件"
# F12 差集校验（add-executor-image task 2.2；本体论审核 F2 裁决 warning→阻断）：
# manifest 覆盖范围内未跟踪文件 = 将静默漏出投影（v0.1.2 教训）。纳管目录下
# 「未 stage 未 ignore」几乎必是遗漏（故意不发该 gitignore）→ 阻断发布；
# 告警器自身失败同样阻断（fail-closed：工具坏了本就不该发布）
python3 "$HERE/check_release_gates.py" --untracked-diff "$MANIFEST" --vault "$VAULT_ROOT" \
  || fail "F12 差集非空或告警器失败——先 git add 新文件或移出 manifest 范围"

# ── [2] 转换（抽取+去个人化一体）──────────────────────────────────────────
echo "▶ [2/6] 机械转换 → $OUT"
rm -rf "$OUT"; mkdir -p "$OUT"
# argparse action="append"：每个文件前都须 --file（不能一次 --file 后跟多文件）
TRANSFORM_ARGS=(--vault "$VAULT_ROOT" --out "$OUT")
for f in "${FILES[@]}"; do TRANSFORM_ARGS+=(--file "$f"); done
python3 "$TRANSFORM" "${TRANSFORM_ARGS[@]}" || fail "转换失配（规则须人工更新）"

# ── [3] 内容自检 ─────────────────────────────────────────────────────────────
echo "▶ [3/6] 内容自检"
# D6 落位：bootstrap 入口复到 template 根（真源 <pa>/bootstrap/bootstrap.sh，同事 clone 后根目录直跑）
if [[ -f "$OUT/Projects/项目推进流水线/bootstrap/bootstrap.sh" ]]; then
  cp "$OUT/Projects/项目推进流水线/bootstrap/bootstrap.sh" "$OUT/bootstrap.sh"
fi
# task 6.1 落位：使用版 CLAUDE.md 复到 template 根（真源 <pa>/release/docs/CLAUDE.template.md；
# vault 开发视角 CLAUDE.md 不进 manifest，template 使用视角由独立真源承担）
if [[ -f "$OUT/Projects/项目推进流水线/release/docs/CLAUDE.template.md" ]]; then
  cp "$OUT/Projects/项目推进流水线/release/docs/CLAUDE.template.md" "$OUT/CLAUDE.md"
fi
# add-executor-image task 1.2 落位：执行镜像 Dockerfile 复到 template 根（真源
# <pa>/release/Dockerfile；build context 惯例 Dockerfile 在根——spec: executor-image
# 「template tag 即 build context」）
if [[ -f "$OUT/Projects/项目推进流水线/release/Dockerfile" ]]; then
  cp "$OUT/Projects/项目推进流水线/release/Dockerfile" "$OUT/Dockerfile"
  cp "$OUT/Projects/项目推进流水线/release/.dockerignore" "$OUT/.dockerignore"
fi
# task 2.3 接线：Dockerfile 四项静态检查（存在/tag/非root/无凭据）——在 manifest 则强制过
# （与 CLAUDE.template.md 通道指导检查同构：manifest 条件 + 缺失即 fail）
DOCKERFILE_SRC="Projects/项目推进流水线/release/Dockerfile"
if printf '%s\n' "${FILES[@]}" | grep -qxF "$DOCKERFILE_SRC"; then
  [[ -f "$OUT/Dockerfile" ]] || fail "根 Dockerfile 复刻失败（$DOCKERFILE_SRC 在 manifest）"
  python3 "$HERE/check_release_gates.py" --dockerfile "$OUT/Dockerfile" \
    || fail "Dockerfile 四项静态检查未过（spec: template-release 发布门禁 + executor-image）"
else
  echo "  ⚠ Dockerfile 未纳管 manifest——executor-image in-progress，见 RELEASE_NOTE"
fi
for f in "${FILES[@]}"; do
  for fb in "${FORBIDDEN_PATHS[@]}"; do
    [[ "$f" == *"$fb"* ]] && fail "禁入路径进入副本：$f"
  done
done
# 规约层逐字节 diff（独立门禁脚本，check_spec_layer.sh；pytest 篡改负例直接驱动同一脚本）
bash "$SPEC_LAYER_SH" "$VAULT_ROOT" "$OUT" || fail "规约层与源不一致——投影被污染"
# 通道指导必备（template 使用版 CLAUDE.md 交付后强制；未交付期 warning，由 RELEASE_NOTE 注明 in-progress）
TMPL_CLAUDE_SRC="Projects/项目推进流水线/release/docs/CLAUDE.template.md"
if printf '%s\n' "${FILES[@]}" | grep -qxF "$TMPL_CLAUDE_SRC"; then
  [[ -f "$OUT/CLAUDE.md" ]] || fail "根 CLAUDE.md 复刻失败（$TMPL_CLAUDE_SRC 在 manifest）"
  grep -q '任务通道指导' "$OUT/CLAUDE.md" || fail "template CLAUDE.md 缺任务通道指导节"
else
  echo "  ⚠ template CLAUDE.md（使用版）未交付——in-progress，见 RELEASE_NOTE"
fi

# ── [4] 发布门 ───────────────────────────────────────────────────────────────
echo "▶ [4/6] 发布门：quality.sh（副本内）+ 去个人化 grep"
( cd "$OUT/Projects/项目推进流水线" && bash scripts/quality.sh ) || fail "副本 quality.sh 未全绿"
for pat in "${PERSONAL_PATTERNS[@]}"; do
  hits=$(grep -rIl "$pat" "$OUT" 2>/dev/null || true)
  [[ -z "$hits" ]] || { echo "✗ grep 门禁命中 [$pat]：" >&2; echo "$hits" >&2; exit 2; }
done

# ── [5] RELEASE_NOTE（上游状态义务）────────────────────────────────────────
echo "▶ [5/6] RELEASE_NOTE"
UPSTREAM_SHA="$(git -C "$VAULT_ROOT" rev-parse --short HEAD 2>/dev/null || echo uncommitted)"
INPROGRESS="$(cd "$VAULT_ROOT/openspec/changes" 2>/dev/null && for d in */; do
  d="${d%/}"; t="$d/tasks.md"
  [[ -f "$t" ]] && grep -q '^- \[ \]' "$t" && echo "  - $d（tasks 未全勾）"
done)"
cat > "$OUT/RELEASE_NOTE.md" << NOTE
# Release Note：pa-pipeline-template

- 上游：agentworkflow @ $UPSTREAM_SHA（$(date +%F) 发布）
- 门禁：quality.sh 全绿（compile+pytest+ruff）＋ 去个人化 grep 零命中＋规约层逐字节一致
- 内容契约：release/manifest.txt（vault 侧）为本次投影文件清单

## 上游 in-progress 状态（防对照困惑）
本投影截取自上游进行中状态，以下变更**尚未在上游完成**，其已交付部分随行、语义以上游规约为准：
$INPROGRESS
NOTE
echo "  RELEASE_NOTE.md 已生成"

# ── [6] publish（可选）──────────────────────────────────────────────────────
if [[ -n "$REMOTE" ]]; then
  echo "▶ [6/6] publish：git init + commit + tag $TAG"
  # 清发布门在副本内产生的运行时残渣（quality.sh 的 compile/pytest 缓存）——
  # 不清会被下方 add -A 打进发布仓（模板内容契约只含 manifest 投影）
  find "$OUT" -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \) \
    -prune -exec rm -rf {} + 2>/dev/null || true
  git -C "$OUT" init -q -b main && git -C "$OUT" add -A
  git -C "$OUT" -c user.name="pa-release" -c user.email="release@pa-local" \
    commit -qm "release: pa-pipeline-template $TAG (upstream $UPSTREAM_SHA)"
  git -C "$OUT" tag "$TAG"
  echo "  副本已 init+tag（$TAG）。推送：git -C $OUT push $REMOTE main --tags"
fi

echo "✓ release 全绿：$OUT（upstream $UPSTREAM_SHA）"
