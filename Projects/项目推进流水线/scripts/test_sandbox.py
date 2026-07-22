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


class StaticEgress:
    """task 5.1/5.2 测试用 egress enforcement adapter：``enforceable``/``install_ok`` 固定 bool。"""
    def __init__(self, enforceable: bool, desc: str = "static", install_ok: bool = True):
        self._ok = enforceable
        self._desc = desc
        self._install_ok = install_ok

    def enforceable(self) -> bool:
        return self._ok

    def install(self, allowlist) -> bool:   # noqa: ARG002
        return self._install_ok

    def describe(self) -> str:
        return self._desc


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
    c = CS.ContainerSandbox(runner, image="pa:test", egress=StaticEgress(True))
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
    c = CS.ContainerSandbox(FakeContainerRunner(), egress=StaticEgress(True))
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
    c = CS.ContainerSandbox(FakeContainerRunner(create_raises=True), egress=StaticEgress(True))
    result = c.prepare(SB.SandboxSpec(worktree_dir=str(wt)))
    assert isinstance(result, SB.SandboxBlocked)


def test_container_run_executes_and_enforces_network(tmp_path):
    wt = tmp_path / "wt"; wt.mkdir()
    runner = FakeContainerRunner(exec_result=(0, "ok", ""))
    c = CS.ContainerSandbox(runner, egress=StaticEgress(True))
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
    c = CS.ContainerSandbox(runner, egress=StaticEgress(True))
    handle = c.prepare(SB.SandboxSpec(worktree_dir=str(wt)))
    c.teardown(handle)
    assert runner.removed == [handle.runtime_id]


# ════════════════════════════════════════════════════════════════════════════
# Section 5 task 5.1：egress enforcement adapter + preflight（design 决策#5）
# container adapter 必须调用 enforceable egress policy 或 sandbox_blocked——label 只是审计
# 元数据，不足以 claim network enforcement。claim higher assurance 前 preflight enforceable()。
# ════════════════════════════════════════════════════════════════════════════
def test_container_prepare_preflights_egress_enforceability(tmp_path):
    """5.1 核心（design #5）：egress 不可执行（label-only / 无真实强制）→ claim higher 前 preflight
    拦截 → SandboxBlocked（policy_violation），绝不以 label 充当 enforcement。"""
    wt = tmp_path / "wt"; wt.mkdir()
    c = CS.ContainerSandbox(FakeContainerRunner(),
                             egress=StaticEgress(False, "label-only audit metadata"))
    result = c.prepare(SB.SandboxSpec(worktree_dir=str(wt)))
    assert isinstance(result, SB.SandboxBlocked)
    assert result.policy_violation is True
    assert "egress" in result.reason.lower()


def test_container_prepare_no_egress_adapter_blocked(tmp_path):
    """未注入 egress adapter（egress=None）→ 不能 claim higher assurance（默认 fail-closed，design #5）。"""
    wt = tmp_path / "wt"; wt.mkdir()
    c = CS.ContainerSandbox(FakeContainerRunner())          # egress=None
    result = c.prepare(SB.SandboxSpec(worktree_dir=str(wt)))
    assert isinstance(result, SB.SandboxBlocked)
    assert "egress" in result.reason.lower()


def test_container_prepare_enforceable_egress_proceeds_to_higher(tmp_path):
    """egress 可执行（真实强制已部署，preflight 过）→ 正常 prepare，claim higher assurance。"""
    wt = tmp_path / "wt"; wt.mkdir()
    runner = FakeContainerRunner()
    c = CS.ContainerSandbox(runner, egress=StaticEgress(True, "pa-egress network inspected"))
    handle = c.prepare(SB.SandboxSpec(worktree_dir=str(wt)))
    assert isinstance(handle, SB.SandboxHandle)
    assert handle.limits["assurance"] == "higher"


def test_label_only_egress_is_not_enforceable():
    """LabelOnlyEgress（docker --label 元数据）enforceable=False——label 不构成 enforcement（design #5）。"""
    e = CS.LabelOnlyEgress()
    assert e.enforceable() is False
    assert "label" in e.describe().lower()


def test_docker_network_egress_preflights_inspect(monkeypatch):
    """DockerNetworkEgress.enforceable() 真实 preflight：docker network inspect <name> 返回 0=已部署
    （enforceable）；网络缺失/无 docker → False（不 claim）。导入零 docker 依赖（仅运行时 subprocess）。"""
    e = CS.DockerNetworkEgress(network="pa-egress")

    class _R:
        returncode = 0
    # 有 docker + network 存在 → enforceable
    monkeypatch.setattr(CS.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(CS.subprocess, "run", lambda *a, **k: _R())
    assert e.enforceable() is True
    # network 缺失（inspect 非 0）→ 不 enforceable
    class _R2:
        returncode = 1
    monkeypatch.setattr(CS.subprocess, "run", lambda *a, **k: _R2())
    assert e.enforceable() is False
    # 无 docker → 不 enforceable
    monkeypatch.setattr(CS.shutil, "which", lambda _: None)
    assert e.enforceable() is False


# ════════════════════════════════════════════════════════════════════════════
# Section 5 task 5.2：enforceable egress policy 替代 label-only intent（design #5）
# label-only domain intent 不再被接受为 enforcement；egress adapter 必须为 allowlist 安装/验证
# enforceable policy（install），install/verify 失败 → sandbox_blocked。
# ════════════════════════════════════════════════════════════════════════════
def test_container_prepare_egress_install_failure_blocked(tmp_path):
    """5.2 核心：egress enforceable（边界就绪）但为 allowlist 安装/验证策略失败 → sandbox_blocked
    （policy_violation）——绝不以 label 充当 enforcement。"""
    wt = tmp_path / "wt"; wt.mkdir()
    c = CS.ContainerSandbox(FakeContainerRunner(),
                             egress=StaticEgress(True, install_ok=False))
    result = c.prepare(SB.SandboxSpec(worktree_dir=str(wt), network_allowlist=("pypi.org",)))
    assert isinstance(result, SB.SandboxBlocked)
    assert result.policy_violation is True
    assert "egress" in result.reason.lower()


def test_label_only_egress_install_returns_false():
    """5.2：LabelOnlyEgress.install() 恒 False——label 不是 enforceable policy，无法 install/verify。"""
    assert CS.LabelOnlyEgress().install(("pypi.org",)) is False


def test_docker_network_egress_install_three_paths(monkeypatch):
    """5.2：DockerNetworkEgress.install 真实 ensure named network——inspect 已存在→True（不 create）；
    missing→create 成功→True；create 失败→False（不 claim enforcement）。"""
    e = CS.DockerNetworkEgress(network="pa-egress")

    class _R:
        def __init__(self, rc): self.returncode = rc
    monkeypatch.setattr(CS.shutil, "which", lambda _: "/usr/bin/docker")
    # 已存在（inspect 0）→ True，不 create
    monkeypatch.setattr(CS.subprocess, "run", lambda cmd, **k: _R(0))
    assert e.install(("pypi.org",)) is True
    # missing（inspect 1）→ create 成功（0）→ True
    created = []

    def run_missing_then_create(cmd, **k):
        created.append(cmd)
        return _R(1) if "inspect" in str(cmd) else _R(0)
    monkeypatch.setattr(CS.subprocess, "run", run_missing_then_create)
    assert e.install(("pypi.org",)) is True
    assert any("create" in str(c) for c in created)
    # create 失败（inspect 1 + create 1）→ False
    monkeypatch.setattr(CS.subprocess, "run", lambda cmd, **k: _R(1))
    assert e.install(("pypi.org",)) is False


# ════════════════════════════════════════════════════════════════════════════
# Section 5 task 5.3：route dev/test 命令经 sandbox adapter + 禁止 container→local 静默 fallback
# local tier 经 LocalWorktreeSandbox（cwd=worktree subprocess，与 dev/test 现状等价，baseline 零变化）；
# container tier 经 ContainerSandbox；container prepare blocked → 默认返回 blocked，**绝不静默切 local**
# （design #5 / Migration Plan L5）；仅 dev smoke 显式 allow_local_fallback=True 才切 local（标记可审计）。
# ════════════════════════════════════════════════════════════════════════════
def test_select_adapter_local_when_container_flag_off():
    """5.3：container_sandbox flag 关 → 选 local adapter（baseline LOCAL_WORKTREE tier）。"""
    tier, adapter = SB.select_adapter(
        container_sandbox_enabled=False,
        local_adapter=SB.LocalWorktreeSandbox(),
        container_adapter=CS.ContainerSandbox(FakeContainerRunner(), egress=StaticEgress(True)))
    assert tier is SB.AssuranceTier.LOCAL_WORKTREE
    assert isinstance(adapter, SB.LocalWorktreeSandbox)


def test_select_adapter_container_when_flag_and_prefer():
    """5.3：container_sandbox flag 开 + profile prefer_container → 选 container adapter。"""
    cont = CS.ContainerSandbox(FakeContainerRunner(), egress=StaticEgress(True))
    tier, adapter = SB.select_adapter(
        container_sandbox_enabled=True, prefer_container=True,
        local_adapter=SB.LocalWorktreeSandbox(), container_adapter=cont)
    assert tier is SB.AssuranceTier.CONTAINER
    assert adapter is cont


def test_route_command_local_tier_executes_real_subprocess(tmp_path):
    """5.3：local tier 经 adapter 路由执行真实命令（LocalWorktreeSandbox subprocess；dev/test 命令经此路由）。"""
    wt = tmp_path / "wt"; wt.mkdir()
    spec = SB.SandboxSpec(worktree_dir=str(wt))
    rr = SB.route_command(adapter=SB.LocalWorktreeSandbox(), spec=spec,
                          command=["python3", "-c", "print('DEV_OK')"])
    assert rr.ok and rr.blocked is None
    assert rr.result.exit_code == 0 and "DEV_OK" in rr.result.stdout
    assert rr.fell_back_to_local is False


def test_route_command_container_tier_executes(tmp_path):
    """5.3：container tier 经 adapter 路由执行（FakeContainerRunner 模拟容器 exec，egress 可执行）。"""
    wt = tmp_path / "wt"; wt.mkdir()
    runner = FakeContainerRunner(exec_result=(0, "TEST_OK", ""))
    spec = SB.SandboxSpec(worktree_dir=str(wt), network_allowlist=("pypi.org",))
    rr = SB.route_command(adapter=CS.ContainerSandbox(runner, egress=StaticEgress(True)),
                          spec=spec, command=["pytest", "-q"], requested_hosts=("pypi.org",))
    assert rr.ok and rr.result.exit_code == 0 and "TEST_OK" in rr.result.stdout


def test_route_command_container_blocked_no_silent_fallback(tmp_path):
    """5.3 核心：container prepare blocked（egress 不可执行）+ 不允许 fallback → 返回 blocked，
    **绝不静默切 local**（fell_back_to_local=False；生产路径调用方据此 abort/记 sandbox_blocked）。"""
    wt = tmp_path / "wt"; wt.mkdir()
    spec = SB.SandboxSpec(worktree_dir=str(wt))
    rr = SB.route_command(
        adapter=CS.ContainerSandbox(FakeContainerRunner(), egress=StaticEgress(False)),
        spec=spec, command=["pytest", "-q"],
        allow_local_fallback=False, local_adapter=SB.LocalWorktreeSandbox())
    assert not rr.ok
    assert isinstance(rr.blocked, SB.SandboxBlocked)
    assert rr.fell_back_to_local is False        # 不静默切 local
    assert rr.result is None


def test_route_command_dev_smoke_explicit_local_fallback(tmp_path):
    """5.3：container blocked + dev smoke 显式 allow_local_fallback=True → 切 local tier 执行，
    fell_back_to_local=True（显式标记可审计，非静默降级）。"""
    wt = tmp_path / "wt"; wt.mkdir()
    spec = SB.SandboxSpec(worktree_dir=str(wt))
    rr = SB.route_command(
        adapter=CS.ContainerSandbox(FakeContainerRunner(), egress=StaticEgress(False)),
        spec=spec, command=["python3", "-c", "print('SMOKE_OK')"],
        allow_local_fallback=True, local_adapter=SB.LocalWorktreeSandbox())
    assert rr.ok                                            # fallback 到 local 后执行成功
    assert rr.fell_back_to_local is True                    # 显式标记（非静默）
    assert rr.result.exit_code == 0 and "SMOKE_OK" in rr.result.stdout


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
    c = CS.ContainerSandbox(runner, egress=StaticEgress(True))
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
    c = CS.ContainerSandbox(runner, egress=StaticEgress(True))
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
    c = CS.ContainerSandbox(runner, egress=StaticEgress(True))
    handle = c.prepare(SB.SandboxSpec(worktree_dir=str(repo), network_allowlist=()))
    assert isinstance(handle, SB.SandboxHandle)
    out = c.run(handle, ["python3", "test_calc.py"], requested_hosts=())
    assert out.exit_code == 0
