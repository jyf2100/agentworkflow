"""hub 面板项目建档——机械落盘模块（add-hub-panel-console D1，spec: hub-panel）。

项目档案定性为**运行时配置**（真相源 = HUB_DATA_ROOT，proposal 边界定性）：本模块
只做表单值校验 + 两文件落盘（profiles/<name>.yaml + sources.yaml），零语义判定。

零 YAML 库（hub 最小依赖面不动，D1）：动态值一律双引号 scalar + 转义（\\ → \\\\、
" → \\"），且只收单行文本（含 \\n 即拒）——注入面为零。失败清理：任一步失败
rmtree 刚建的目录再抛（目录名全新，无误删面）。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from lifecycle import PROJECT_RE

_REPO_RE = re.compile(r"^(?:https://github\.com/)?([A-Za-z0-9][A-Za-z0-9_.-]*)/([A-Za-z0-9][A-Za-z0-9_.-]*)/?$")
_MAX_TEXT = 500  # goal/surface 单字段字节上限（单行）


class ProjectSetupError(Exception):
    """建档失败（校验/落盘）——调用方 422/500 回显，不静默。"""


def normalize_repo(raw: str) -> str:
    """owner/repo 或完整 URL → 规范化 https://github.com/<owner>/<repo>；非法即抛。"""
    m = _REPO_RE.fullmatch(raw.strip())
    if not m:
        raise ProjectSetupError(f"repo 形态非法（须 owner/repo 或 github URL）：{raw!r}")
    return f"https://github.com/{m.group(1)}/{m.group(2)}"


def _scalar(v: str) -> str:
    """单行安全 YAML 双引号 scalar：转义 \\ 与 "；含换行/超长即抛。"""
    if "\n" in v or "\r" in v:
        raise ProjectSetupError("字段含换行——仅收单行文本")
    b = v.encode("utf-8")
    if len(b) > _MAX_TEXT:
        raise ProjectSetupError(f"字段超 {_MAX_TEXT} 字节上限")
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def create_project(data_root: Path, name: str, repo: str, goal: str, surface: str) -> Path:
    """建档：校验 + 落盘两文件，返回项目目录。重名/非法/落盘失败抛 ProjectSetupError。"""
    if not PROJECT_RE.fullmatch(name):
        raise ProjectSetupError(f"project 名非法（须匹配 {PROJECT_RE.pattern}）：{name!r}")
    if not goal.strip() or not surface.strip():
        raise ProjectSetupError("goal 与 match_surface（one_liner）均必填")
    proj_dir = data_root / name
    if proj_dir.exists():
        raise ProjectSetupError(f"项目已存在（目录或文件占位）：{name}")

    profile = (
        f"name: {name}\n"
        "admission: true\n"
        f"repo: {_scalar(normalize_repo(repo))}\n"
        f"goal: {_scalar(goal.strip())}\n"
        "match_surface:\n"
        f"  one_liner: {_scalar(surface.strip())}\n"
    )
    try:
        (proj_dir / "profiles").mkdir(parents=True)
        (proj_dir / "profiles" / f"{name}.yaml").write_text(profile, encoding="utf-8")
        (proj_dir / "sources.yaml").write_text("sources: []\n", encoding="utf-8")
    except OSError as e:
        shutil.rmtree(proj_dir, ignore_errors=True)  # 只清自己刚建的（入口已保证全新）
        raise ProjectSetupError(f"落盘失败（已清理半写目录）：{e}") from None
    return proj_dir
