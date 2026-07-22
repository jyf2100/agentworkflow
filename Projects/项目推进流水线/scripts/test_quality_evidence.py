"""test_quality_evidence.py — task 1.2 quality-evidence 命令回归测试。

task 1.2 要求一条**真实** quality-evidence 命令：用声明的 Python 解释器跑 compile/pytest/ruff
三步，从**真实进程结果**记录 interpreter 版本、exact command、test counts、ruff 结果、timestamp、
artifact digest，并归档（design 决策#6；spec runtime-cutover-evidence「Reproducible quality evidence」）。

design 决策#6 明确「fake adapters for deterministic unit tests」——故 subprocess 执行用 DI 注入
的 ``runner``，timestamp/python_exe 亦可注入，保证单测确定可复现；真实执行由 CLI ``__main__`` 承担。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
import artifact_store as AS  # noqa: E402
import quality_evidence as QE  # noqa: E402


def _fake_runner(responses):
    """按 cmd 里的关键 token 分发伪造进程结果。

    responses: {token: (returncode, stdout, stderr)}；token 命中即返回该响应。
    记录每次调用 cmd 以便断言 exact command。
    """
    calls: list[list[str]] = []

    def runner(cmd, cwd):
        calls.append(list(cmd))
        text = " ".join(cmd)
        for token, (rc, out, err) in responses.items():
            if token in text:
                return SimpleNamespace(returncode=rc, stdout=out, stderr=err)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def _green_runner():
    return _fake_runner({
        "--version": (0, "Python 3.11.9\n", ""),
        "compileall": (0, "", ""),
        "pytest": (0, "........\n500 passed in 3.41s", ""),
        "ruff": (0, "All checks passed!", ""),
    })


# ─── RED→GREEN：全绿路径记录完整证据并归档可校验 digest ─────────────────────────
def test_green_run_records_full_evidence_and_archives_verifiable_digests(tmp_path):
    # Arrange
    root = tmp_path / "proj"
    root.mkdir()
    art = tmp_path / "artifacts"
    runner = _green_runner()

    # Act
    ev = QE.run_quality_evidence(
        project_root=str(root), artifact_root=str(art),
        python_exe="/usr/bin/python3.11",
        runner=runner, timestamp="2026-07-22T00:00:00Z",
    )

    # Assert — readiness + 计数 + 元数据齐全
    assert ev.readiness is True
    assert ev.test_counts["passed"] == 500
    assert ev.test_counts["failed"] == 0
    assert ev.ruff_errors == 0
    assert ev.interpreter_version == "3.11.9"
    assert ev.timestamp == "2026-07-22T00:00:00Z"
    assert len(ev.commands) == 3
    # exact command 携带声明的解释器（compile/pytest 两步）
    py_cmds = [" ".join(c) for c in runner.calls if "--version" not in c]
    assert all("/usr/bin/python3.11" in c for c in py_cmds[:2])
    # 每步真实输出归档为内容寻址 artifact，digest 可 round-trip 校验
    assert len(ev.artifact_refs) == 3
    for step, ref in zip(ev.commands, ev.artifact_refs):
        assert step.digest == ref.digest
        loaded = AS.load(art, ref)  # load 内部重算 digest 校验，不抛即匹配
        assert AS.compute_digest(loaded) == step.digest


# ─── Ruff 失败 → readiness False 并精确计数错误 ─────────────────────────────────
def test_ruff_failure_blocks_readiness_and_counts_errors(tmp_path):
    # Arrange
    runner = _fake_runner({
        "--version": (0, "Python 3.11.9\n", ""),
        "compileall": (0, "", ""),
        "pytest": (0, "500 passed in 3s", ""),
        "ruff": (1, "scripts/x.py:1:1: F401 unused\nFound 11 errors.\n", ""),
    })

    # Act
    ev = QE.run_quality_evidence(
        project_root=str(tmp_path), artifact_root=str(tmp_path / "a"),
        python_exe="python3", runner=runner, timestamp="t",
    )

    # Assert
    assert ev.readiness is False
    assert ev.ruff_errors == 11
    assert ev.commands[2].exit_code == 1   # ruff 步非零


# ─── pytest 失败 → readiness False 并分别计数 passed/failed ────────────────────
def test_pytest_failure_counts_failed_and_passed(tmp_path):
    # Arrange
    runner = _fake_runner({
        "--version": (0, "Python 3.11.9\n", ""),
        "compileall": (0, "", ""),
        "pytest": (1, "3 failed, 497 passed in 4.0s", ""),
        "ruff": (0, "All checks passed!", ""),
    })

    # Act
    ev = QE.run_quality_evidence(
        project_root=str(tmp_path), artifact_root=str(tmp_path / "a"),
        python_exe="python3", runner=runner, timestamp="t",
    )

    # Assert
    assert ev.readiness is False
    assert ev.test_counts["failed"] == 3
    assert ev.test_counts["passed"] == 497


# ─── 三步 exact command 形态固定（compileall/pytest/ruff，按序）─────────────────
def test_three_steps_use_compileall_pytest_ruff_in_order(tmp_path):
    # Arrange
    runner = _green_runner()

    # Act
    ev = QE.run_quality_evidence(
        project_root=str(tmp_path), artifact_root=str(tmp_path / "a"),
        python_exe="/p/python3.11", runner=runner, timestamp="t",
    )

    # Assert
    names = tuple(s.name for s in ev.commands)
    assert names == ("compile", "pytest", "ruff")
    assert "compileall" in ev.commands[0].command
    assert "pytest" in ev.commands[1].command
    assert "ruff" in ev.commands[2].command


# ─── 不注入 timestamp 时产出合法 ISO8601（真实 CLI 场景）────────────────────────
def test_default_timestamp_is_parseable_iso8601(tmp_path):
    # Arrange
    runner = _green_runner()

    # Act
    ev = QE.run_quality_evidence(
        project_root=str(tmp_path), artifact_root=str(tmp_path / "a"),
        python_exe="python3", runner=runner,
    )  # 不注入 timestamp

    # Assert
    datetime.fromisoformat(ev.timestamp.replace("Z", "+00:00"))
