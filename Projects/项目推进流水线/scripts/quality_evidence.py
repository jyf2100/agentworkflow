#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quality_evidence.py — task 1.2 可重复 quality 证据命令（OpenSpec complete-durable-loop-runtime-integration）。

``quality.sh`` 只在终端打印 compile/pytest/ruff 三步结果；task 1.2 要求一条**可重复**命令，把**真实
进程结果**收敛为结构化、可归档、可校验的证据记录，供 cutover rollout 前归档不可变 passing evidence
（design 决策#6「quality command must run with the declared Python version and archive its exact
output and evidence digests」；spec runtime-cutover-evidence「Reproducible quality evidence」）。

与 ``cutover.run_quality_gate``（task 8.8）的区别：后者是 drill **逻辑层**——接收已算好的
``test_counts``/``evidence_items`` 做聚合判定，不真实执行；本模块**真实**跑 compile/pytest/ruff 三步，
逐条捕获 exit/stdout/stderr，解析计数，归档内容寻址 artifact，返回不可变 ``QualityEvidence``。

design 决策#6「fake adapters for deterministic unit tests」——subprocess 执行经 DI 的 ``runner``
注入（默认真实 ``subprocess.run``），``python_exe``/``timestamp`` 亦可注入，故单测确定可复现；
真实执行由 ``python3 scripts/quality_evidence.py`` CLI 承担（``main`` 默认用 ``sys.executable``）。
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

import artifact_store as AS
from loop_state import ArtifactRef


@dataclass(frozen=True)
class StepResult:
    """单步 quality 执行的真实结果（含其归档输出的 digest）。"""
    name: str                       # compile / pytest / ruff
    command: tuple[str, ...]        # exact command（含声明的解释器）
    exit_code: int
    stdout: str
    stderr: str
    digest: str                     # sha256:<hex>，stdout+stderr 归档后的内容寻址 digest


@dataclass(frozen=True)
class QualityEvidence:
    """一次完整 quality 运行的不可变证据记录。"""
    interpreter_version: str        # 真实执行解释器的版本（来自 ``<py> --version``）
    interpreter_executable: str
    commands: tuple[StepResult, ...]
    test_counts: dict               # {passed, failed, errors, total}，从 pytest stdout 解析
    ruff_errors: int                # 从 ruff stdout 解析（``Found N errors``）
    timestamp: str                  # ISO8601 UTC
    readiness: bool                 # 三步全 exit 0（任一步非零即不 ready）
    artifact_root: str
    artifact_refs: tuple[ArtifactRef, ...]
    evidence_digests: tuple[str, ...]
    detail: str


def _step_commands(py: str) -> list[tuple[str, tuple[str, ...]]]:
    """与 ``quality.sh`` 同源的三步 exact command（compile / pytest / ruff，按序）。"""
    return [
        ("compile", (py, "-m", "compileall", "-q", "scripts")),
        ("pytest", (py, "-m", "pytest", "scripts")),
        ("ruff", ("ruff", "check", "scripts")),
    ]


def _real_runner(cmd, cwd):
    """默认 runner：真实 subprocess 执行，捕获 stdout/stderr/exit。"""
    p = subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True)
    return SimpleNamespace(returncode=p.returncode, stdout=p.stdout or "", stderr=p.stderr or "")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _last_int(text: str, pattern: str) -> int:
    ms = re.findall(pattern, text)
    return int(ms[-1]) if ms else 0


def _parse_pytest_counts(stdout: str) -> dict:
    passed = _last_int(stdout, r"(\d+) passed")
    failed = _last_int(stdout, r"(\d+) failed")
    errors = _last_int(stdout, r"(\d+) errors?")
    return {"passed": passed, "failed": failed, "errors": errors,
            "total": passed + failed + errors}


def _parse_ruff_errors(stdout: str) -> int:
    m = re.search(r"Found (\d+) error", stdout)
    return int(m.group(1)) if m else 0


def _parse_interpreter_version(text: str) -> str:
    m = re.search(r"Python (\d+\.\d+\.\d+)", text)
    return m.group(1) if m else ""


def run_quality_evidence(*, project_root, artifact_root, python_exe=None,
                         runner=None, timestamp=None) -> QualityEvidence:
    """真实跑 compile/pytest/ruff 三步，归档输出并返回不可变 ``QualityEvidence``。

    Args:
        project_root: 含 ``pyproject.toml`` 的根（三步 cwd，同 ``quality.sh`` 的 PROJ_ROOT）。
        artifact_root: 内容寻址工件存储根（每步输出归档于此）。
        python_exe: 声明的解释器可执行文件（默认 ``sys.executable``）。
        runner: DI 进程执行器 ``runner(cmd, cwd) -> SimpleNamespace(returncode, stdout, stderr)``；
                默认真实 ``subprocess.run``，测试注入伪造。
        timestamp: ISO8601 时间戳（默认 ``datetime.now(timezone.utc)``）。
    """
    py = python_exe or sys.executable
    run = runner or _real_runner
    ts = timestamp or _now_iso()

    ver = run((py, "--version"), cwd=str(project_root))
    interpreter_version = _parse_interpreter_version((ver.stdout or "") + (ver.stderr or ""))

    steps: list[StepResult] = []
    refs: list[ArtifactRef] = []
    for name, cmd in _step_commands(py):
        res = run(cmd, cwd=str(project_root))
        combined = (res.stdout or "") + (res.stderr or "")
        ref = AS.store(artifact_root, combined, kind="transcript", sensitivity="internal")
        steps.append(StepResult(name=name, command=tuple(cmd), exit_code=res.returncode,
                                stdout=res.stdout or "", stderr=res.stderr or "",
                                digest=ref.digest))
        refs.append(ref)

    test_counts = _parse_pytest_counts(steps[1].stdout)
    ruff_errors = _parse_ruff_errors(steps[2].stdout)
    readiness = all(s.exit_code == 0 for s in steps)
    detail = (f"python {interpreter_version}; "
              f"tests {test_counts['passed']} passed/{test_counts['failed']} failed; "
              f"ruff {ruff_errors} errors; ready={readiness}")
    return QualityEvidence(
        interpreter_version=interpreter_version,
        interpreter_executable=py,
        commands=tuple(steps),
        test_counts=test_counts,
        ruff_errors=ruff_errors,
        timestamp=ts,
        readiness=readiness,
        artifact_root=str(artifact_root),
        artifact_refs=tuple(refs),
        evidence_digests=tuple(r.digest for r in refs),
        detail=detail,
    )


def main(argv=None) -> int:
    """CLI：在项目根用 ``sys.executable`` 真实跑三步，打印 JSON 证据；readiness False → exit 1。"""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent  # 项目推进流水线/
    art = root / ".quality-evidence"
    ev = run_quality_evidence(project_root=str(root), artifact_root=str(art))
    print(json.dumps({
        "interpreter_version": ev.interpreter_version,
        "interpreter_executable": ev.interpreter_executable,
        "timestamp": ev.timestamp,
        "readiness": ev.readiness,
        "test_counts": ev.test_counts,
        "ruff_errors": ev.ruff_errors,
        "commands": [{"name": s.name, "command": list(s.command),
                      "exit_code": s.exit_code, "digest": s.digest} for s in ev.commands],
        "evidence_digests": list(ev.evidence_digests),
        "artifact_root": ev.artifact_root,
        "detail": ev.detail,
    }, indent=2, ensure_ascii=False))
    return 0 if ev.readiness else 1


if __name__ == "__main__":
    raise SystemExit(main())
