# test_build_executor_image.py — build 脚手 dry-run 计划序列单测（add-executor-image tasks 3.1/3.2）。
#
# D8 接缝：build_executor_image.sh 的 --dry-run 输出是稳定契约（PLAN 行序列），
# 本文件断言参数与顺序；镜像真实验证属本服务器 E2E（task 5.1，verification 文档）。
# build 脚手不进投影（manifest 不纳管）——module-level skip 保 template 副本绿。
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent
PA_ROOT = SCRIPTS_DIR.parent
BUILD_SH = PA_ROOT / "release" / "build_executor_image.sh"

if not BUILD_SH.exists():
    pytest.skip("release/ 工具链不在本 checkout（template 投影）——release 单测仅 vault 侧运行",
                allow_module_level=True)


def _make_context(tmp_path: Path, *, with_note_sha: str | None = None) -> Path:
    """minimal 合法 build context：Dockerfile（过四项静态检查）+ 可选 RELEASE_NOTE。"""
    ctx = tmp_path / "ctx"; (ctx / "Projects/项目推进流水线/scripts").mkdir(parents=True)
    (ctx / "Dockerfile").write_text(
        "FROM node:22-slim\n"
        "ARG CLAUDE_CODE_VERSION=9.9.9\n"
        "ARG GH_VERSION=8.8.8\n"
        "RUN echo ok\n"
        "USER pa\n"
        "ENTRYPOINT [\"echo\"]\n", encoding="utf-8")
    (ctx / "Projects/项目推进流水线/scripts/x.py").write_text("a=1\n", encoding="utf-8")
    if with_note_sha:
        (ctx / "RELEASE_NOTE.md").write_text(
            f"# Release Note\n\n- 上游：agentworkflow @ {with_note_sha}（2026-10-20 发布）\n",
            encoding="utf-8")
    return ctx


def _dry_run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(BUILD_SH), "--dry-run", *args],
                          capture_output=True, text=True)


def _plan_lines(out: str) -> list[str]:
    return [l for l in out.splitlines() if l.startswith("PLAN ")]


def test_dry_run_plan_sequence(tmp_path):
    """计划序列顺序契约：checkout ≤ verify-static ≤ build ≤ 七项 check（顺序固定）。"""
    ctx = _make_context(tmp_path)
    proc = _dry_run("--tag", "v0.1.3-test", "--context", str(ctx))
    assert proc.returncode == 0, proc.stderr
    plans = _plan_lines(proc.stdout)
    steps = [p.split(":")[0].removeprefix("PLAN ") for p in plans]
    assert steps[:3] == ["checkout", "verify-static", "build"]
    assert steps[3] == "check id"
    checks = [s.removeprefix("check ") for s in steps[3:]]
    assert checks == ["id", "python", "claude", "gh", "pytest", "compileall", "secrets"]


def test_dry_run_build_labels_and_tag(tmp_path):
    """build 行：-t <image>:<tag> + label 含 template-tag / 组件钉扎版本（ARG 解析）。"""
    ctx = _make_context(tmp_path)
    proc = _dry_run("--tag", "v0.1.3-test", "--context", str(ctx))
    build_line = next(l for l in _plan_lines(proc.stdout) if l.startswith("PLAN build"))
    assert "-t pa-executor:v0.1.3-test" in build_line
    assert "--label io.pa.template-tag=v0.1.3-test" in build_line
    assert "--label io.pa.claude-code-version=9.9.9" in build_line   # ARG 从 Dockerfile 解析
    assert "--label io.pa.gh-version=8.8.8" in build_line


def test_dry_run_upstream_commit_from_release_note(tmp_path):
    """upstream commit 取自 RELEASE_NOTE 锚定（D3）；无 note 则 unknown。"""
    ctx = _make_context(tmp_path, with_note_sha="abc1234")
    out = _dry_run("--tag", "v0.1.3-test", "--context", str(ctx)).stdout
    assert "--label io.pa.upstream-commit=abc1234" in out
    ctx2 = _make_context(tmp_path / "ctx2")
    out2 = _dry_run("--tag", "v0.1.3-test", "--context", str(ctx2)).stdout
    assert "--label io.pa.upstream-commit=unknown" in out2


def test_context_without_dockerfile_fails_fast(tmp_path):
    ctx = _make_context(tmp_path)
    (ctx / "Dockerfile").unlink()
    proc = _dry_run("--tag", "v0.1.3-test", "--context", str(ctx))
    assert proc.returncode == 2


def test_static_check_violation_blocks_plan(tmp_path):
    """四项静态检查不过 → 无 build 计划（前置校验先于一切 docker 动作）。"""
    ctx = _make_context(tmp_path)
    (ctx / "Dockerfile").write_text("FROM node:latest\nUSER root\n", encoding="utf-8")
    proc = _dry_run("--tag", "v0.1.3-test", "--context", str(ctx))
    assert proc.returncode == 2
    assert not any(l.startswith("PLAN build") for l in _plan_lines(proc.stdout))


def test_missing_tag_is_usage_error(tmp_path):
    ctx = _make_context(tmp_path)
    proc = _dry_run("--context", str(ctx))
    assert proc.returncode == 3


# ── build context 内容校验（本体论审核 F1 方案 b：git 差集）───────────────────

def _git_init_ctx(ctx: Path) -> None:
    """把 context 变成干净 git checkout（tag 物化形态——真发布场景）。"""
    for args in (
        ["git", "-C", str(ctx), "init", "-q", "-b", "main"],
        ["git", "-C", str(ctx), "add", "-A"],
        ["git", "-C", str(ctx), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-qm", "x"],
    ):
        subprocess.run(args, check=True, capture_output=True)

def test_git_context_stray_file_blocks_build(tmp_path):
    """git checkout 混入未跟踪文件（tag 内容清单外）→ fail，无 build 计划（F1b）。"""
    ctx = _make_context(tmp_path)
    _git_init_ctx(ctx)
    (ctx / "stray.txt").write_text("x\n", encoding="utf-8")   # 未 add 未 ignore——混入
    proc = _dry_run("--tag", "v0.1.3-test", "--context", str(ctx))
    assert proc.returncode == 2
    assert "混入" in (proc.stderr + proc.stdout)
    assert not any(l.startswith("PLAN build") for l in _plan_lines(proc.stdout))

def test_git_context_clean_proceeds(tmp_path):
    """干净 git checkout（= 真 tag checkout 形态）→ 校验过，build 计划照常。"""
    ctx = _make_context(tmp_path)
    _git_init_ctx(ctx)
    proc = _dry_run("--tag", "v0.1.3-test", "--context", str(ctx))
    assert proc.returncode == 0, proc.stderr
    assert any(l.startswith("PLAN build") for l in _plan_lines(proc.stdout))
