# test_release_gates.py — release 门禁扩展单测（add-executor-image tasks 2.1/2.2）。
#
# 覆盖：
#   ① Dockerfile 四项静态检查（spec: template-release delta「发布门禁」）：
#      存在 / FROM 显式 tag（非 latest）/ USER 非 root / 无凭据字面量模式；
#      另有真源锚自检——vault 侧 release/Dockerfile 本身必须过（漂移在测试期暴露）；
#   ② 工作树 vs git ls-files 差集告警（F12 闭环：新文件未 stage → 静默漏出投影）。
#
# 注意：门禁 pattern 一律片段拼接（_j）——grep 门禁扫描整个副本，测试源码字面量会被自己命中
# （test_release_transform.py 同款约定）。
# template 投影不含 release/ 工具链（manifest 刻意排除）——module-level skip 保副本绿。
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent
PA_ROOT = SCRIPTS_DIR.parent
RELEASE_DIR = PA_ROOT / "release"
GATES_PY = RELEASE_DIR / "check_release_gates.py"
REAL_DOCKERFILE = RELEASE_DIR / "Dockerfile"

if not GATES_PY.exists():
    pytest.skip("release/ 工具链不在本 checkout（template 投影）——release 单测仅 vault 侧运行",
                allow_module_level=True)

sys.path.insert(0, str(RELEASE_DIR))
import check_release_gates as gates  # noqa: E402  # release/ 模块经 sys.path 引入（扁平约定）


def _j(*parts: str) -> str:
    """片段拼接——避免门禁 pattern 在测试源码中成串出现（grep 门禁会扫到测试自身）。"""
    return "".join(parts)


# ── ① Dockerfile 四项静态检查 ────────────────────────────────────────────────

GOOD = """FROM node:22-slim
ARG V=1
USER pa
ENTRYPOINT ["echo"]
"""

def test_good_dockerfile_passes(tmp_path):
    f = tmp_path / "Dockerfile"; f.write_text(GOOD, encoding="utf-8")
    assert gates.check_dockerfile_static(f) == []

def test_missing_dockerfile_reported(tmp_path):
    out = gates.check_dockerfile_static(tmp_path / "nope.Dockerfile")
    assert any("存在" in v for v in out)

def test_from_latest_rejected(tmp_path):
    f = tmp_path / "Dockerfile"
    f.write_text(GOOD.replace("node:22-slim", _j("node:", "latest")), encoding="utf-8")
    assert any("FROM" in v and "latest" in v for v in gates.check_dockerfile_static(f))

def test_missing_user_rejected(tmp_path):
    f = tmp_path / "Dockerfile"
    f.write_text(GOOD.replace("USER pa\n", ""), encoding="utf-8")
    assert any("USER" in v for v in gates.check_dockerfile_static(f))

def test_user_root_rejected(tmp_path):
    f = tmp_path / "Dockerfile"
    f.write_text(GOOD.replace("USER pa", "USER root"), encoding="utf-8")
    assert any("USER" in v for v in gates.check_dockerfile_static(f))

def test_credential_literal_rejected(tmp_path):
    # 凭据前缀模式片段拼接，防测试源码被密钥扫描/门禁自命中
    cases = [
        _j("ghp_", "0123456789abcdefghijklmnop"),
        _j("github_pat_", "11ABCDEFGH0123456789_abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLM"),
        _j("sk-ant-", "api03-0123456789abcdef"),
        _j("AKIA", "0123456789ABCDEF"),
        _j("xoxb-", "0123456789-abcdefghijklmnopqrstuvwxyz"),
    ]
    for cred in cases:
        f = tmp_path / "Dockerfile"
        f.write_text(f"FROM node:22-slim\nENV TOK={cred}\nUSER pa\n", encoding="utf-8")
        assert any("凭据" in v for v in gates.check_dockerfile_static(f)), cred[:8]

def test_vault_real_dockerfile_passes():
    """真源锚自检：vault 侧 release/Dockerfile 必须过四项（漂移测试期暴露，不等发布期）。"""
    assert REAL_DOCKERFILE.is_file()
    assert gates.check_dockerfile_static(REAL_DOCKERFILE) == []


# ── ② 工作树 vs ls-files 差集告警（F12）─────────────────────────────────────

def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)

def test_untracked_new_file_reported(tmp_path):
    repo = tmp_path / "repo"; (repo / "scripts").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "scripts" / "tracked.py").write_text("a=1\n", encoding="utf-8")
    (repo / "scripts" / "fresh.py").write_text("b=2\n", encoding="utf-8")   # 未 add —— F12 场景
    _git(repo, "add", "scripts/tracked.py")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
    untracked = gates.untracked_under_manifest(repo, ["scripts/"])
    assert untracked == ["scripts/fresh.py"]

def test_all_tracked_clean(tmp_path):
    repo = tmp_path / "repo"; (repo / "scripts").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "scripts" / "a.py").write_text("a=1\n", encoding="utf-8")
    _git(repo, "add", "scripts/a.py")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
    assert gates.untracked_under_manifest(repo, ["scripts/"]) == []

def test_ignored_files_not_reported(tmp_path):
    repo = tmp_path / "repo"; (repo / "scripts").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / ".gitignore").write_text("*.local.py\n", encoding="utf-8")
    (repo / "scripts" / "a.py").write_text("a=1\n", encoding="utf-8")
    (repo / "scripts" / "junk.local.py").write_text("j=1\n", encoding="utf-8")  # ignored
    _git(repo, "add", ".gitignore", "scripts/a.py")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
    assert gates.untracked_under_manifest(repo, ["scripts/"]) == []


# ── ②b CLI 阻断语义（本体论审核 F2：warning → block）────────────────────────

def _make_repo_with(tmp_path: Path, *, fresh: bool) -> Path:
    repo = tmp_path / "repo"; (repo / "scripts").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "scripts" / "tracked.py").write_text("a=1\n", encoding="utf-8")
    if fresh:
        (repo / "scripts" / "fresh.py").write_text("b=2\n", encoding="utf-8")  # 未 add——F12 场景
    _git(repo, "add", "scripts/tracked.py")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
    return repo

def test_untracked_diff_cli_blocks_on_delta(tmp_path):
    """差集非空 → CLI exit 2 阻断（本体论审核 F2 裁决：warning 形态可重演 v0.1.2 漏发）。"""
    repo = _make_repo_with(tmp_path, fresh=True)
    mf = tmp_path / "manifest.txt"; mf.write_text("scripts/\n", encoding="utf-8")
    assert gates.main(["--untracked-diff", str(mf), "--vault", str(repo)]) == 2

def test_untracked_diff_cli_clean_exits_zero(tmp_path):
    repo = _make_repo_with(tmp_path, fresh=False)
    mf = tmp_path / "manifest.txt"; mf.write_text("scripts/\n", encoding="utf-8")
    assert gates.main(["--untracked-diff", str(mf), "--vault", str(repo)]) == 0
