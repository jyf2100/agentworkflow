#!/usr/bin/env bash
# check_spec_layer.sh — 规约层逐字节一致门禁（template-release，task 2.4 测试接缝）。
# 规约层（openspec/specs、docs/adr）在 transform 中零规则 = passthrough，故投影必须与源
# 逐字节一致；任何漂移 = 投影被污染，发布阻断。从 release_template.sh 抽出为独立脚本，
# 供发布链调用与 pytest 篡改负例直接驱动。
# 用法：check_spec_layer.sh <vault_root> <template_root>
# 退出码：0 一致 | 2 不一致或参数错误
set -euo pipefail

[[ $# -eq 2 ]] || { echo "用法：check_spec_layer.sh <vault_root> <template_root>" >&2; exit 2; }
vault="$1"; out="$2"
[[ -d "$vault" ]] || { echo "✗ [spec-layer] vault 目录不存在：$vault" >&2; exit 2; }
[[ -d "$out" ]] || { echo "✗ [spec-layer] template 目录不存在：$out" >&2; exit 2; }

status=0
for p in openspec/specs docs/adr; do
  [[ -d "$vault/$p" ]] || continue   # 源无此层（稀疏投影 fixture）→ 跳过
  if ! diff -r "$vault/$p" "$out/$p" >/dev/null 2>&1; then
    echo "✗ [spec-layer] $p 与源不一致（漂移/缺失）：" >&2
    diff -r "$vault/$p" "$out/$p" >&2 || true
    status=2
  fi
done
exit $status
