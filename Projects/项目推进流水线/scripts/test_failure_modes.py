#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_failure_modes.py — dispatch 远程查询失败模式单测（OpenSpec fail-safe-dispatch / tasks 4.5）。

对每个 dispatch 关键查询，覆盖五种失败模式，断言一律 ExtResult.UNKNOWN（fail-safe），
且 auth 失败的 reason 不泄漏 token（脱敏）：
    timeout（TimeoutExpired）/ non-zero exit / missing command（FileNotFoundError）
    / authentication failure（401 + ghp_）/ invalid JSON（JSONDecodeError）。

覆盖查询：check_branch_protection / count_inflight_prs / already_dispatched
（含 gh PR list 与 git ls-remote 两子查询）/ _has_commits / _lookup_pr。

跑：python3 -m pytest scripts/test_failure_modes.py -q
AAA 结构。
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_daily  # noqa: E402
from external_state import ExtState  # noqa: E402


@dataclass
class _FakeProc:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


def _raise(exc):
    def _f(*a, **k):
        raise exc
    return _f


def _ret(proc):
    def _f(*a, **k):
        return proc
    return _f


# 五种失败模式（统一 subprocess.run 替身）
TIMEOUT = _raise(subprocess.TimeoutExpired(cmd=["gh"], timeout=20))
NO_CMD = _raise(FileNotFoundError("gh"))
NON_ZERO = _ret(_FakeProc(1, stderr="boom"))
BAD_JSON = _ret(_FakeProc(0, stdout="{not json"))
AUTH_FAIL = _ret(_FakeProc(1, stderr="HTTP 401: Bad credentials (ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890)"))


# ─── check_branch_protection（无 JSON 解析：看 returncode / 404）────
def test_branch_protection_timeout(monkeypatch):
    monkeypatch.setattr(run_daily.subprocess, "run", TIMEOUT)
    assert run_daily.check_branch_protection("o/r", "main").state is ExtState.UNKNOWN


def test_branch_protection_nonzero(monkeypatch):
    monkeypatch.setattr(run_daily.subprocess, "run", NON_ZERO)
    assert run_daily.check_branch_protection("o/r", "main").state is ExtState.UNKNOWN


def test_branch_protection_missing_command(monkeypatch):
    monkeypatch.setattr(run_daily.subprocess, "run", NO_CMD)
    assert run_daily.check_branch_protection("o/r", "main").state is ExtState.UNKNOWN


def test_branch_protection_auth_fail_sanitized(monkeypatch):
    monkeypatch.setattr(run_daily.subprocess, "run", AUTH_FAIL)
    r = run_daily.check_branch_protection("o/r", "main")
    assert r.state is ExtState.UNKNOWN
    assert "ghp_aBcDeFg" not in r.reason          # token 脱敏，不进 state/报告


# ─── count_inflight_prs（JSON 解析）─────────────────────────────────
def test_inflight_timeout(monkeypatch):
    monkeypatch.setattr(run_daily.subprocess, "run", TIMEOUT)
    assert run_daily.count_inflight_prs("o/r").state is ExtState.UNKNOWN


def test_inflight_nonzero(monkeypatch):
    monkeypatch.setattr(run_daily.subprocess, "run", NON_ZERO)
    assert run_daily.count_inflight_prs("o/r").state is ExtState.UNKNOWN


def test_inflight_missing_command(monkeypatch):
    monkeypatch.setattr(run_daily.subprocess, "run", NO_CMD)
    assert run_daily.count_inflight_prs("o/r").state is ExtState.UNKNOWN


def test_inflight_invalid_json(monkeypatch):
    monkeypatch.setattr(run_daily.subprocess, "run", BAD_JSON)
    assert run_daily.count_inflight_prs("o/r").state is ExtState.UNKNOWN


def test_inflight_auth_fail_sanitized(monkeypatch):
    monkeypatch.setattr(run_daily.subprocess, "run", AUTH_FAIL)
    r = run_daily.count_inflight_prs("o/r")
    assert r.state is ExtState.UNKNOWN
    assert "ghp_aBcDeFg" not in r.reason


# ─── already_dispatched（gh PR list + git ls-remote 双查询；任一失败→UNKNOWN）──
def test_idempotency_gh_timeout(monkeypatch):
    monkeypatch.setattr(run_daily.subprocess, "run", TIMEOUT)
    assert run_daily.already_dispatched("o/r", "/repo", "slug").state is ExtState.UNKNOWN


def test_idempotency_gh_invalid_json(monkeypatch):
    monkeypatch.setattr(run_daily.subprocess, "run", BAD_JSON)
    assert run_daily.already_dispatched("o/r", "/repo", "slug").state is ExtState.UNKNOWN


def test_idempotency_auth_fail_sanitized(monkeypatch):
    monkeypatch.setattr(run_daily.subprocess, "run", AUTH_FAIL)
    r = run_daily.already_dispatched("o/r", "/repo", "slug")
    assert r.state is ExtState.UNKNOWN
    assert "ghp_aBcDeFg" not in r.reason


def test_idempotency_lsremote_failure(monkeypatch):
    # gh 成功（空 PR 列表）但 git ls-remote 失败 → UNKNOWN（任一查询不明即 fail-safe）
    calls = iter([_FakeProc(0, stdout="[]"), None])

    def _f(*a, **k):
        nxt = next(calls)
        if nxt is None:
            raise subprocess.TimeoutExpired(cmd=["git"], timeout=20)
        return nxt
    monkeypatch.setattr(run_daily.subprocess, "run", _f)
    assert run_daily.already_dispatched("o/r", "/repo", "slug").state is ExtState.UNKNOWN


# ─── _has_commits（无 JSON：看 stdout）──────────────────────────────
def test_has_commits_timeout(monkeypatch):
    monkeypatch.setattr(run_daily.subprocess, "run", TIMEOUT)
    assert run_daily._has_commits("/repo", "main", "auto/x").state is ExtState.UNKNOWN


def test_has_commits_nonzero(monkeypatch):
    monkeypatch.setattr(run_daily.subprocess, "run", NON_ZERO)
    assert run_daily._has_commits("/repo", "main", "auto/x").state is ExtState.UNKNOWN


def test_has_commits_missing_command(monkeypatch):
    monkeypatch.setattr(run_daily.subprocess, "run", NO_CMD)
    assert run_daily._has_commits("/repo", "main", "auto/x").state is ExtState.UNKNOWN


# ─── _lookup_pr（reconcile 用，JSON 解析）──────────────────────────
def test_lookup_pr_timeout(monkeypatch):
    monkeypatch.setattr(run_daily.subprocess, "run", TIMEOUT)
    assert run_daily._lookup_pr("o/r", "auto/x").state is ExtState.UNKNOWN


def test_lookup_pr_invalid_json(monkeypatch):
    monkeypatch.setattr(run_daily.subprocess, "run", BAD_JSON)
    assert run_daily._lookup_pr("o/r", "auto/x").state is ExtState.UNKNOWN


def test_lookup_pr_auth_fail_sanitized(monkeypatch):
    monkeypatch.setattr(run_daily.subprocess, "run", AUTH_FAIL)
    r = run_daily._lookup_pr("o/r", "auto/x")
    assert r.state is ExtState.UNKNOWN
    assert "ghp_aBcDeFg" not in r.reason


def test_lookup_pr_not_found_when_empty(monkeypatch):
    # 对照：明确无 PR（空列表，rc=0）→ NOT_FOUND（可决断，非 UNKNOWN）——锁定 fail-safe 边界
    monkeypatch.setattr(run_daily.subprocess, "run", _ret(_FakeProc(0, stdout="[]")))
    assert run_daily._lookup_pr("o/r", "auto/x").state is ExtState.NOT_FOUND
