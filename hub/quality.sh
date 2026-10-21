#!/usr/bin/env bash
# hub/quality.sh — 执行 hub·单一本地质量命令（add-hub-skeleton 任务 9.2，spec: execution-hub）。
#
# 与 pa 的 Projects/项目推进流水线/scripts/quality.sh **并列**（A8 裁决：pa scripts/
# 零改动）——hub 有自己的质量口径，CI 与本地同一条命令。三段形态对齐 pa：
#   [1/3] compileall  hub/ 语法编译（仅 byte-compile，不 import）
#   [2/3] pytest      hub/ 全量单测（含 E2E 干跑；real-docker 标记测试本机无 daemon 自动 skip）
#   [3/3] ruff        E9+F 实缺陷规则（hub/ruff.toml，与 pa 同口径）
#
# 依赖：fastapi、httpx、pypinyin（vault 环境已随 pa dev 依赖就位；独立部署时补装）。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT_ROOT="$(cd "$HERE/.." && pwd)"   # pytest/ruff 以 vault 根为 CWD（hub/pytest.ini 生效）
cd "$VAULT_ROOT"

PY="${PYTHON:-python3}"

echo "▶ [1/3] compileall（hub 语法编译）"
"$PY" -m compileall -q hub

echo "▶ [2/3] pytest（hub 全量单测 + E2E 干跑）"
"$PY" -m pytest hub

echo "▶ [3/3] ruff（E9+F 实缺陷）"
ruff check hub

echo "✓ hub quality 绿（compile + pytest + ruff）"
