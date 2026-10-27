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
# forge 形态（fix-hub-form-parity ④，spec: hub-panel「建项目表单」）：repo = forge 全路径
# 字符串——gitlab 多级（组/子组/仓 ≥2 段），gitcode 恰两级 owner/repo；均不走 GitHub 归一。
_FORGE_SEG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_GITLAB_HOST_RE = re.compile(r"^https?://[A-Za-z0-9._-]+(?::\d{1,5})?$")  # 带scheme 可带端口（实例地址，无子路径）


class ProjectSetupError(Exception):
    """建档失败（校验/落盘）——调用方 422/500 回显，不静默。"""


def normalize_repo(raw: str) -> str:
    """owner/repo 或完整 URL → 规范化 https://github.com/<owner>/<repo>；非法即抛。"""
    m = _REPO_RE.fullmatch(raw.strip())
    if not m:
        raise ProjectSetupError(f"repo 形态非法（须 owner/repo 或 github URL）：{raw!r}")
    return f"https://github.com/{m.group(1)}/{m.group(2)}"


def normalize_forge_repo(raw: str, *, what: str, min_segments: int, max_segments: int | None = None) -> str:
    """forge 全路径 repo 归一（gitlab/gitcode 语义）：段字符收敛，首/尾/连续 / 全拒。"""
    s = raw.strip()
    if s.startswith("/") or s.endswith("/") or "//" in s:
        raise ProjectSetupError(
            f"{what} repo 不得含首/尾或连续 /（/abs/path 是 github 缺省形态的本地 clone 路径语义，"
            f"两种形态撞车即拒；{what} 形态填全路径字符串）：{raw!r}"
        )
    parts = s.split("/")
    if len(parts) < min_segments or (max_segments is not None and len(parts) > max_segments):
        shape = "组/仓 起、可多级子组" if max_segments is None else "恰两级 owner/repo"
        raise ProjectSetupError(f"{what} repo 须为 forge 全路径字符串（{shape}）：{raw!r}")
    if any(not _FORGE_SEG_RE.fullmatch(p) for p in parts):
        raise ProjectSetupError(f"{what} repo 段含非法字符（字母/数字/_/.-/ 开头须字母数字_）：{raw!r}")
    return "/".join(parts)


def _scalar(v: str) -> str:
    """单行安全 YAML 双引号 scalar：转义 \\ 与 "；含换行/超长即抛。"""
    if "\n" in v or "\r" in v:
        raise ProjectSetupError("字段含换行——仅收单行文本")
    b = v.encode("utf-8")
    if len(b) > _MAX_TEXT:
        raise ProjectSetupError(f"字段超 {_MAX_TEXT} 字节上限")
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def create_project(data_root: Path, name: str, repo: str, goal: str, surface: str,
                   forge: str = "", gitlab_host: str = "") -> Path:
    """建档：校验 + 落盘两文件，返回项目目录。重名/非法/落盘失败抛 ProjectSetupError。

    forge 形态（fix-hub-form-parity ④）：""/"github"=缺省（落盘键集与既有逐字节一致，
    回归零变化）；gitlab 须 gitlab_host（带 scheme 可带端口）+ repo 全路径 ≥2 段（多级）；
    gitcode 无 host 键 + repo 恰两级。非法即抛（fail-fast，不落半写目录）。"""
    forge = (forge or "").strip()
    if forge == "github":  # 表单缺省 value=""，显式 github 容错归一为缺省
        forge = ""
    gitlab_host = (gitlab_host or "").strip()
    if forge not in ("", "gitlab", "gitcode"):
        raise ProjectSetupError(f"forge 非法（github 缺省 / gitlab / gitcode）：{forge!r}")
    if not PROJECT_RE.fullmatch(name):
        raise ProjectSetupError(f"project 名非法（须匹配 {PROJECT_RE.pattern}）：{name!r}")
    if not goal.strip() or not surface.strip():
        raise ProjectSetupError("goal 与 match_surface（one_liner）均必填")
    proj_dir = data_root / name
    if proj_dir.exists():
        raise ProjectSetupError(f"项目已存在（目录或文件占位）：{name}")

    forge_block = ""
    if forge == "gitlab":
        if not _GITLAB_HOST_RE.fullmatch(gitlab_host):
            raise ProjectSetupError(
                f"gitlab 形态 gitlab_host 必填（带 scheme 可带端口，如 http://172.32.153.183:8180）：{gitlab_host!r}"
            )
        forge_block = f"forge: gitlab\ngitlab_host: {_scalar(gitlab_host)}\n"
        repo_norm = normalize_forge_repo(repo, what="gitlab", min_segments=2)
    elif forge == "gitcode":
        if gitlab_host:
            raise ProjectSetupError("gitcode 无 host 键（固定 SaaS api.gitcode.com）——gitlab_host 须留空")
        forge_block = "forge: gitcode\n"
        repo_norm = normalize_forge_repo(repo, what="gitcode", min_segments=2, max_segments=2)
    else:
        repo_norm = normalize_repo(repo)

    profile = (
        f"name: {name}\n"
        "admission: true\n"
        f"{forge_block}"
        f"repo: {_scalar(repo_norm)}\n"
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
