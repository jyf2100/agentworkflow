#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_sandbox.py — Section 6（Execution Sandbox）task 6.1-6.6 全套测试。

覆盖：
    * 6.1 ExecutionSandbox 接口 + LocalWorktreeSandbox（lower assurance，真跑 subprocess）；
    * 6.2 ContainerSandbox（higher assurance，注入 FakeContainerRunner，解耦真实 docker）；
    * 6.3 profile-driven network allowlist + 未声明目标 block（NetworkPolicy 纯函数 + container 强制）；
    * 6.4 host-side verified publication（长期凭据留 host，sandbox 零长期凭据）；
    * 6.5 sandbox_blocked 不降级（container 失败绝不偷偷切 local tier）；
    * 6.6 Node + Python fixture repos 跑过 local + container 两 tier（无真实外部服务）。

AAA；模块零 docker / 零 SDK 依赖（LocalWorktreeSandbox.run 的 subprocess 仅运行时）。跑：
    python3 -m pytest scripts/test_sandbox.py -q
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import container_sandbox as CS  # noqa: E402
import sandbox as SB  # noqa: E402
import sandbox_publication as SP  # noqa: E402


# ════════════════════════════════════════════════════════════════════════════
# FakeContainerRunner（测试用，替代真实 docker/podman）
# ════════════════════════════════════════════════════════════════════════════
class FakeContainerRunner:
    """记录 create/exec/remove 调用的假容器运行时。"""

    def __init__(self, *, available=True, create_raises=False, exec_result=(0, "", "")):
        self._available = available
        self._create_raises = create_raises
        self._exec_result = exec_result
        self.created: list[dict] = []
        self.executed: list[tuple] = []
        self.removed: list[str] = []

    def available(self):
        return self._available

    def create(self, **kw):
        if self._create_raises:
            raise RuntimeError("container create boom")
        self.created.append(dict(kw))
        return f"fake_container_{len(self.created)}"

    def exec(self, container_id, command):
        self.executed.append((container_id, tuple(command)))
        return self._exec_result

    def remove(self, container_id):
        self.removed.append(container_id)


# ════════════════════════════════════════════════════════════════════════════
# 6.1 LocalWorktreeSandbox（lower assurance）
# ════════════════════════════════════════════════════════════════════════════
def test_local_worktree_assurance_is_lower():
    lw = SB.LocalWorktreeSandbox()
    assert lw.tier is SB.AssuranceTier.LOCAL_WORKTREE
    assert lw.assurance_level() == "lower"


def test_local_worktree_prepare_existing_dir(tmp_path):
    wt = tmp_path / "worktree"
    wt.mkdir()
    (wt / "f.txt").write_text("x", encoding="utf-8")
    lw = SB.LocalWorktreeSandbox()
    handle = lw.prepare(SB.SandboxSpec(worktree_dir=str(wt)))
    assert isinstance(handle, SB.SandboxHandle)
    assert handle.tier is SB.AssuranceTier.LOCAL_WORKTREE
    assert handle.limits["assurance"] == "lower"
    assert handle.limits["isolation"] == "none"


def test_local_worktree_prepare_missing_dir_blocked(tmp_path):
    lw = SB.LocalWorktreeSandbox()
    result = lw.prepare(SB.SandboxSpec(worktree_dir=str(tmp_path / "nope")))
    assert isinstance(result, SB.SandboxBlocked)
    assert "worktree not found" in result.reason


def test_local_worktree_run_executes_in_worktree(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "f.txt").write_text("hello", encoding="utf-8")
    lw = SB.LocalWorktreeSandbox()
    handle = lw.prepare(SB.SandboxSpec(worktree_dir=str(wt)))
    # 真跑：读 worktree 内文件（证明 cwd=worktree）
    out = lw.run(handle, "cat f.txt")
    assert isinstance(out, SB.SandboxRunResult)
    assert out.exit_code == 0 and out.stdout.strip() == "hello"


def test_local_worktree_run_nonzero_exit(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    lw = SB.LocalWorktreeSandbox()
    handle = lw.prepare(SB.SandboxSpec(worktree_dir=str(wt)))
    out = lw.run(handle, "exit 7")
    assert out.exit_code == 7


def test_local_worktree_teardown_is_noop(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    lw = SB.LocalWorktreeSandbox()
    handle = lw.prepare(SB.SandboxSpec(worktree_dir=str(wt)))
    lw.teardown(handle)   # 不抛即过（worktree 由控制面管）


# ════════════════════════════════════════════════════════════════════════════
# 6.1 resolve_tier + 6.5 open_sandbox 不降级
# ════════════════════════════════════════════════════════════════════════════
def test_resolve_tier_selects_container_when_flag_and_prefer():
    assert SB.resolve_tier(container_sandbox_enabled=True, prefer_container=True) is SB.AssuranceTier.CONTAINER


def test_resolve_tier_defaults_to_local():
    assert SB.resolve_tier(container_sandbox_enabled=False, prefer_container=True) is SB.AssuranceTier.LOCAL_WORKTREE
    assert SB.resolve_tier(container_sandbox_enabled=True, prefer_container=False) is SB.AssuranceTier.LOCAL_WORKTREE


def test_open_sandbox_no_silent_fallback_to_local(tmp_path):
    """6.5 核心：container prepare 失败 → open_sandbox 返 SandboxBlocked，绝不偷偷切 local tier。"""
    spec = SB.SandboxSpec(worktree_dir=str(tmp_path))
    container = CS.ContainerSandbox(FakeContainerRunner(available=False))
    result = SB.open_sandbox(spec, container)
    assert isinstance(result, SB.SandboxBlocked)
    assert result.tier is SB.AssuranceTier.CONTAINER     # 仍是 container tier（未降级）
    assert "unavailable" in result.reason


# ════════════════════════════════════════════════════════════════════════════
# 6.3 NetworkPolicy（纯函数）
# ════════════════════════════════════════════════════════════════════════════
def test_network_policy_normalize_schema_port_path():
    p = CS.NetworkPolicy(allowlist=("api.github.com",))
    assert p.allowed("https://API.GitHub.com/v1/repos")     # schema/path/大小写归一化后命中
    assert p.allowed("api.github.com:443")                   # 去 port 后命中
    assert not p.allowed("evil.example.com")


def test_network_policy_violations_lists_undeclared():
    p = CS.NetworkPolicy(allowlist=("pypi.org", "api.github.com"))
    viol = p.violations(("https://pypi.org/x", "evil.example.com", "127.0.0.1"))
    assert "evil.example.com" in viol and "127.0.0.1" in viol
    assert "pypi.org" not in viol


def test_network_policy_from_profile():
    profile = {"sandbox": {"network_allowlist": ["pypi.org", "registry.npmjs.org"]}}
    p = CS.NetworkPolicy.from_profile(profile)
    assert p.allowed("pypi.org") and p.allowed("registry.npmjs.org")
    assert not p.allowed("evil.com")


def test_network_policy_strict_empty_rejects():
    p = CS.NetworkPolicy(allowlist=(), strict=True)
    assert not p.allowed("anyhost.com")      # strict + 空 allowlist = 拒一切外网


# ════════════════════════════════════════════════════════════════════════════
# 6.2 ContainerSandbox（higher assurance，注入 FakeContainerRunner）
# ════════════════════════════════════════════════════════════════════════════
def test_container_assurance_is_higher():
    c = CS.ContainerSandbox(FakeContainerRunner())
    assert c.tier is SB.AssuranceTier.CONTAINER
    assert c.assurance_level() == "higher"


def test_container_prepare_runner_unavailable_blocked(tmp_path):
    """6.5：container 运行时不可用 → blocked，不降级 local。"""
    c = CS.ContainerSandbox(FakeContainerRunner(available=False))
    result = c.prepare(SB.SandboxSpec(worktree_dir=str(tmp_path)))
    assert isinstance(result, SB.SandboxBlocked)
    assert not result.policy_violation       # 运行时不可用（非策略违例）


def test_container_prepare_mounts_and_limits(tmp_path):
    """6.2：container create 收到 writable worktree + ro PRD/source + 资源限制 + non-root + temp home。"""
    wt = tmp_path / "wt"; wt.mkdir()
    prd = tmp_path / "prd"; prd.mkdir()
    runner = FakeContainerRunner()
    c = CS.ContainerSandbox(runner, image="pa:test")
    handle = c.prepare(SB.SandboxSpec(
        worktree_dir=str(wt), prd_source_dirs=(str(prd),),
        cpu_limit="2.0", memory_limit="1g", process_limit=256,
        temp_home=True, non_root=True,
    ))
    assert isinstance(handle, SB.SandboxHandle)
    assert handle.writable_mounts == (str(wt),)          # 唯一可写 = worktree
    assert handle.readonly_mounts == (str(prd),)         # PRD/source 只读
    rec = runner.created[0]
    assert rec["writable_mounts"] == (str(wt),)
    assert rec["readonly_mounts"] == (str(prd),)
    assert rec["cpu_limit"] == "2.0" and rec["memory_limit"] == "1g"
    assert rec["process_limit"] == 256 and rec["non_root"] is True
    assert rec["temp_home"] is True


def test_container_prepare_network_violation_policy_blocked(tmp_path):
    """6.3 核心：requested_hosts 有未声明目标 → policy_violation blocked（fail-closed）。"""
    wt = tmp_path / "wt"; wt.mkdir()
    c = CS.ContainerSandbox(FakeContainerRunner())
    result = c.prepare(SB.SandboxSpec(
        worktree_dir=str(wt),
        network_allowlist=("pypi.org",),
        requested_hosts=("pypi.org", "evil.example.com"),   # evil 未声明
    ))
    assert isinstance(result, SB.SandboxBlocked)
    assert result.policy_violation is True
    assert "evil.example.com" in result.reason


def test_container_prepare_create_failure_blocked(tmp_path):
    wt = tmp_path / "wt"; wt.mkdir()
    c = CS.ContainerSandbox(FakeContainerRunner(create_raises=True))
    result = c.prepare(SB.SandboxSpec(worktree_dir=str(wt)))
    assert isinstance(result, SB.SandboxBlocked)


def test_container_run_executes_and_enforces_network(tmp_path):
    wt = tmp_path / "wt"; wt.mkdir()
    runner = FakeContainerRunner(exec_result=(0, "ok", ""))
    c = CS.ContainerSandbox(runner)
    handle = c.prepare(SB.SandboxSpec(worktree_dir=str(wt), network_allowlist=("pypi.org",)))
    # requested_hosts 全在 allowlist → 放行 exec
    out = c.run(handle, ["pytest", "-q"], requested_hosts=("pypi.org",))
    assert isinstance(out, SB.SandboxRunResult) and out.exit_code == 0
    assert runner.executed and runner.executed[0][1] == ("pytest", "-q")
    # requested_hosts 有未声明 → run 时 block
    blocked = c.run(handle, ["curl", "evil.com"], requested_hosts=("evil.com",))
    assert isinstance(blocked, SB.SandboxBlocked) and blocked.policy_violation


def test_container_teardown_removes(tmp_path):
    wt = tmp_path / "wt"; wt.mkdir()
    runner = FakeContainerRunner()
    c = CS.ContainerSandbox(runner)
    handle = c.prepare(SB.SandboxSpec(worktree_dir=str(wt)))
    c.teardown(handle)
    assert runner.removed == [handle.runtime_id]


# ════════════════════════════════════════════════════════════════════════════
# 6.4 host-side verified publication（长期凭据留 host）
# ════════════════════════════════════════════════════════════════════════════
def test_host_publish_with_credential_published():
    req = SP.HostPublicationRequest(kind=SP.PUB_GIT_PUSH, target="origin/main",
                                    idempotency_key="idem_abc")
    res = SP.host_side_publish(req, host_credentials={SP.PUB_GIT_PUSH: True})
    assert res.status == "published"
    assert "host-side verified" in res.evidence


def test_host_publish_no_credential_blocked_not_silently_failed():
    """缺宿主凭据 → no_credentials blocked（不静默失败/不降级用 sandbox 残余凭据）。"""
    req = SP.HostPublicationRequest(kind=SP.PUB_PR_CREATE, target="owner/repo:branch")
    res = SP.host_side_publish(req, host_credentials={})   # 无 pr_create 凭据
    assert res.status == "no_credentials"
    assert "never enter the sandbox" in res.evidence


def test_host_publish_already_published_skips_exactly_once():
    req = SP.HostPublicationRequest(kind=SP.PUB_GIT_PUSH, target="b",
                                    idempotency_key="idem_x")
    res = SP.host_side_publish(req, host_credentials={SP.PUB_GIT_PUSH: True},
                               already_published=True)
    assert res.status == "published" and "exactly-once" in res.evidence


def test_host_publish_unknown_kind_error():
    req = SP.HostPublicationRequest(kind="weird_op", target="x")
    res = SP.host_side_publish(req, host_credentials={"weird_op": True})
    assert res.status == "error"


def test_sandbox_credential_never_injects_long_lived():
    """6.4：长期凭据（github/smtp/cloud）无论何种 policy 都不进 sandbox。"""
    assert SP.sandbox_credential_allowed(policy=SP.CredentialPolicy.HOST_ONLY,
                                         kind=SP.PUB_GIT_PUSH) is False
    assert SP.sandbox_credential_allowed(policy=SP.CredentialPolicy.MINIMAL_INJECT,
                                         kind=SP.PUB_SMTP_SEND) is False


# ════════════════════════════════════════════════════════════════════════════
# 6.6 Node + Python fixture repos 跑过 local + container 两 tier（无真实外部服务）
# ════════════════════════════════════════════════════════════════════════════
def _make_python_fixture(repo: Path) -> None:
    """Python fixture repo：纯 stdlib 测试，零外部服务/零网络。"""
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text(
        "from calc import add\n"
        "def test_add():\n"
        "    assert add(1, 2) == 3\n"
        "if __name__ == '__main__':\n"
        "    test_add()\n"
        "    print('PY_OK')\n",
        encoding="utf-8",
    )


def _make_node_fixture(repo: Path) -> None:
    """Node fixture repo：纯 node 内置 assert，零外部服务/零 npm install。"""
    (repo / "calc.js").write_text("module.exports = { add: (a, b) => a + b };\n", encoding="utf-8")
    (repo / "test_calc.js").write_text(
        "const assert = require('assert');\n"
        "const { add } = require('./calc.js');\n"
        "assert.strictEqual(add(1, 2), 3);\n"
        "console.log('NODE_OK');\n",
        encoding="utf-8",
    )


def test_python_fixture_runs_local_tier(tmp_path):
    """6.6：Python fixture 跑过 local tier（真 subprocess，无外部服务）。"""
    repo = tmp_path / "py_repo"
    repo.mkdir()
    _make_python_fixture(repo)
    lw = SB.LocalWorktreeSandbox()
    handle = lw.prepare(SB.SandboxSpec(worktree_dir=str(repo), network_allowlist=()))
    assert isinstance(handle, SB.SandboxHandle)
    out = lw.run(handle, "python3 test_calc.py")
    assert out.exit_code == 0
    assert "PY_OK" in out.stdout


def test_python_fixture_runs_container_tier(tmp_path):
    """6.6：Python fixture 跑过 container tier（FakeContainerRunner，模拟容器内执行）。"""
    repo = tmp_path / "py_repo"
    repo.mkdir()
    _make_python_fixture(repo)
    runner = FakeContainerRunner(exec_result=(0, "PY_OK", ""))
    c = CS.ContainerSandbox(runner)
    handle = c.prepare(SB.SandboxSpec(worktree_dir=str(repo), network_allowlist=()))
    assert isinstance(handle, SB.SandboxHandle)
    out = c.run(handle, ["python3", "test_calc.py"])
    assert out.exit_code == 0 and "PY_OK" in out.stdout
    assert runner.executed  # container exec 被调用


@pytest.mark.skipif(shutil.which("node") is None, reason="node runtime not installed")
def test_node_fixture_runs_local_tier(tmp_path):
    """6.6：Node fixture 跑过 local tier（node 可用时跑，否则 skip）。"""
    repo = tmp_path / "node_repo"
    repo.mkdir()
    _make_node_fixture(repo)
    lw = SB.LocalWorktreeSandbox()
    handle = lw.prepare(SB.SandboxSpec(worktree_dir=str(repo), network_allowlist=()))
    out = lw.run(handle, "node test_calc.js")
    assert out.exit_code == 0 and "NODE_OK" in out.stdout


def test_node_fixture_runs_container_tier(tmp_path):
    """6.6：Node fixture 跑过 container tier（FakeContainerRunner，不依赖真实 node）。"""
    repo = tmp_path / "node_repo"
    repo.mkdir()
    _make_node_fixture(repo)
    runner = FakeContainerRunner(exec_result=(0, "NODE_OK", ""))
    c = CS.ContainerSandbox(runner)
    handle = c.prepare(SB.SandboxSpec(worktree_dir=str(repo), network_allowlist=()))
    out = c.run(handle, ["node", "test_calc.js"])
    assert out.exit_code == 0 and "NODE_OK" in out.stdout


def test_both_tiers_no_real_external_services(tmp_path):
    """6.6：fixture 无网络依赖——空 allowlist + 空 requested_hosts 不触发 block，且不访问外网。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_python_fixture(repo)
    # container tier：空 allowlist + 无 requested_hosts → 不 block（fixture 本就不联网）
    runner = FakeContainerRunner(exec_result=(0, "", ""))
    c = CS.ContainerSandbox(runner)
    handle = c.prepare(SB.SandboxSpec(worktree_dir=str(repo), network_allowlist=()))
    assert isinstance(handle, SB.SandboxHandle)
    out = c.run(handle, ["python3", "test_calc.py"], requested_hosts=())
    assert out.exit_code == 0
