#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_sandbox_canary.py — task 5.5 real Node/Python fixture canaries（5 维度）测试。

spec task 5.5：「Add real Node and Python fixture canaries covering allowed network,
denied network, denied credential access, resource limits, and unavailable runtime behavior.」

design L50：subprocess-level real fixture（node/python 真跑）+ Docker/Podman canary when available
+ **documented skip/block when unavailable**（unavailable runtime 维度）。

5 维度 × python/node（real subprocess 经 sandbox adapter）：
  * allowed_network：声明 allowlist 内 host → fixture 执行成功（passed）；
  * denied_network：声明未授权 host → container tier policy block（denied）/ local lower-assurance
    不强制（passed + note，诚实反映）；
  * denied_credential：长期凭据 env 经 sanitize（5.4）后不存在（denied）；
  * resource_limits：spec 资源限制反映（reflected）/ 无限制（passed）；
  * unavailable_runtime：worktree 缺失 / runner 不可用 → blocked（documented skip/block）。

AAA；real subprocess（python3/node 真跑）。跑：
    python3 -m pytest scripts/test_sandbox_canary.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import container_sandbox as CS  # noqa: E402
import sandbox as SB  # noqa: E402
import sandbox_canary as SC  # noqa: E402


class FakeContainerRunner:
    """container sandbox 测试桩（network 违例 → policy block，复用 test_sandbox 形态）。"""
    __test__ = False

    def __init__(self, *, available=True, exec_result=(0, "", "")):
        self._available = available
        self._exec_result = exec_result

    def available(self):
        return self._available

    def create(self, **kw):
        return "fake_container_1"

    def exec(self, container_id, command):
        return self._exec_result

    def remove(self, container_id):
        pass


class _StaticEgress:
    """task 5.1/5.2 egress 桩（过 preflight+install）。"""
    __test__ = False

    def __init__(self, enforceable: bool = True):
        self._ok = enforceable

    def enforceable(self) -> bool:
        return self._ok

    def install(self, allowlist) -> bool:   # noqa: ARG002
        return self._ok

    def describe(self) -> str:
        return "static test egress"


def _local():
    return SB.LocalWorktreeSandbox()


def _spec(tmp_path, **kw):
    wt = tmp_path / "wt"
    wt.mkdir()
    return SB.SandboxSpec(worktree_dir=str(wt), **kw)


# ════════════════════════════════════════════════════════════════════════════
# allowed_network：声明 allowlist 内 host → fixture 执行成功（real subprocess）
# ════════════════════════════════════════════════════════════════════════════
def test_canary_allowed_network_python_passed(tmp_path):
    """5.5：python fixture 声明 allowlist 内 host → real subprocess 执行成功。"""
    spec = _spec(tmp_path, network_allowlist=("pypi.org",))
    o = SC.canary_allowed_network(adapter=_local(), spec=spec, language="python",
                                  allowed_host="pypi.org")
    assert o.dimension == "allowed_network" and o.language == "python"
    assert o.outcome == "passed"


def test_canary_allowed_network_node_passed(tmp_path):
    """5.5：node fixture 声明 allowlist 内 host → real node subprocess 执行成功。"""
    spec = _spec(tmp_path, network_allowlist=("registry.npmjs.org",))
    o = SC.canary_allowed_network(adapter=_local(), spec=spec, language="node",
                                  allowed_host="registry.npmjs.org")
    assert o.outcome == "passed" and o.language == "node"


# ════════════════════════════════════════════════════════════════════════════
# denied_network：未授权 host → container tier policy block / local lower-assurance
# ════════════════════════════════════════════════════════════════════════════
def test_canary_denied_network_container_tier_denied(tmp_path):
    """5.5：container tier 声明未授权 host → policy block（denied，FakeContainerRunner + egress）。"""
    spec = _spec(tmp_path, network_allowlist=("pypi.org",))
    c = CS.ContainerSandbox(FakeContainerRunner(), egress=_StaticEgress(True))
    o = SC.canary_denied_network(adapter=c, spec=spec, language="python", denied_host="evil.invalid")
    assert o.outcome == "denied"


def test_canary_denied_network_local_lower_assurance_reflected(tmp_path):
    """5.5：local tier lower-assurance 不强制 network → 诚实反映（passed + lower-assurance note）。"""
    spec = _spec(tmp_path, network_allowlist=("pypi.org",))
    o = SC.canary_denied_network(adapter=_local(), spec=spec, language="python", denied_host="evil.invalid")
    assert o.outcome == "passed"                          # local tier 不强制
    assert "lower" in o.detail.lower()                    # 诚实标注 lower-assurance


# ════════════════════════════════════════════════════════════════════════════
# denied_credential：长期凭据 env sanitize 后不存在
# ════════════════════════════════════════════════════════════════════════════
def test_canary_denied_credential_sanitized(tmp_path):
    """5.5：长期凭据 env 经 sanitize（5.4）→ sandbox 内不存在（denied）。"""
    o = SC.canary_denied_credential(
        language="python", leaked_env={"GITHUB_TOKEN": "ghp_x", "SMTP_PASSWORD": "p", "PATH": "/x"})
    assert o.outcome == "denied" and o.dimension == "denied_credential"
    assert "sanit" in o.detail.lower() or "removed" in o.detail.lower()


# ════════════════════════════════════════════════════════════════════════════
# resource_limits：spec 资源限制反映
# ════════════════════════════════════════════════════════════════════════════
def test_canary_resource_limits_reflected(tmp_path):
    """5.5：spec 带 cpu/mem/process 限制 → canary 反映（reflected）。"""
    spec = _spec(tmp_path, cpu_limit="2.0", memory_limit="2g", process_limit=100)
    o = SC.canary_resource_limits(spec=spec, language="python")
    assert o.outcome == "reflected" and "2.0" in o.detail


def test_canary_resource_limits_no_limits_passed(tmp_path):
    """5.5：spec 无限制（local lower-assurance）→ passed。"""
    spec = _spec(tmp_path)
    o = SC.canary_resource_limits(spec=spec, language="node")
    assert o.outcome == "passed"


# ════════════════════════════════════════════════════════════════════════════
# unavailable_runtime：worktree 缺失 / runner 不可用 → blocked（documented skip/block）
# ════════════════════════════════════════════════════════════════════════════
def test_canary_unavailable_runtime_worktree_missing_blocked(tmp_path):
    """5.5：worktree 缺失 → sandbox_blocked（documented block，design L50）。"""
    spec = SB.SandboxSpec(worktree_dir=str(tmp_path / "does-not-exist"))
    o = SC.canary_unavailable_runtime(adapter=_local(), spec=spec, language="python")
    assert o.outcome == "blocked" and o.dimension == "unavailable_runtime"


def test_canary_unavailable_runtime_runner_unavailable_blocked(tmp_path):
    """5.5：container runner 不可用 → sandbox_blocked（documented block）。"""
    spec = _spec(tmp_path, network_allowlist=("pypi.org",))
    c = CS.ContainerSandbox(FakeContainerRunner(available=False), egress=_StaticEgress(True))
    o = SC.canary_unavailable_runtime(adapter=c, spec=spec, language="python")
    assert o.outcome == "blocked"


# ════════════════════════════════════════════════════════════════════════════
# run_fixture_canaries 汇总：5 维度全覆盖 + python/node
# ════════════════════════════════════════════════════════════════════════════
def test_run_fixture_canaries_covers_five_dimensions(tmp_path):
    """5.5 汇总：run_fixture_canaries 产 5 维度 ×（python+node）canary 结果。"""
    spec = _spec(tmp_path, network_allowlist=("pypi.org",), cpu_limit="2.0")
    outcomes = SC.run_fixture_canaries(
        adapter=_local(), spec=spec, languages=("python", "node"),
        leaked_env={"GITHUB_TOKEN": "ghp_x", "PATH": "/usr/bin"})
    dims = {o.dimension for o in outcomes}
    assert dims == {"allowed_network", "denied_network", "denied_credential",
                    "resource_limits", "unavailable_runtime"}
    langs = {o.language for o in outcomes}
    assert langs == {"python", "node"}                    # real Node + Python 都覆盖
