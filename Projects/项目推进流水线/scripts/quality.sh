#!/usr/bin/env bash
# quality.sh — 项目推进流水线·单一本地质量命令（OpenSpec reproducible-pipeline-validation）。
#
# 一条命令跑齐三项机械质量检查，任一失败即非零退出（CI 与本地同一命令）：
#   [1/3] compileall  全部 .py 语法编译（仅 byte-compile，不 import，故无需运行时依赖齐全）
#   [2/3] pytest      scripts/ 下完整单测套件
#   [3/3] ruff        E9（语法/缩进）+ F（Pyflakes：未定义名/未用导入/未用变量/空 f-string）实缺陷规则
#
# 干净环境一次性装齐依赖后再跑本命令：
#   cd Projects/项目推进流水线 && pip install -e ".[dev]"
#
# 缺依赖时不会静默跳过：run_daily.py 顶层 `import yaml` 缺 PyYAML 会 sys.exit 报可执行安装提示。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_ROOT="$(cd "$HERE/.." && pwd)"   # pyproject.toml 所在（pytest/ruff 配置根）
cd "$PROJ_ROOT"

PY="${PYTHON:-python3}"   # 需已 pip install -e ".[dev]" 装齐依赖的解释器

echo "▶ [1/3] compileall（语法编译）"
"$PY" -m compileall -q scripts

echo "▶ [2/3] pytest（scripts 全量单测）"
"$PY" -m pytest scripts

echo "▶ [3/3] ruff（E9+F 实缺陷）"
ruff check scripts

echo "✓ quality 绿（compile + pytest + ruff）"
