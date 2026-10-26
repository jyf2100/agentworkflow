"""vault_store 测试——凭据保险箱（add-hub-slice-2a 1.1/1.2，spec: hub-credential-vault）。

TDD 先行：落盘即密文 / 原子写 / key 错明确报错 / 读取面 get_user_env / 幂等覆盖。
断言绝不写明文值进错误路径。
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

import vault_store as vs

_USER_ENV = {
    "ANTHROPIC_BASE_URL": "https://roc-proxy.example.internal/v1",
    "ANTHROPIC_AUTH_TOKEN": "sk-ant-plainvalue-xyz",
    "GH_TOKEN": "ghp_plainvalue_abc123",
}


def _vault(tmp_path: Path, key: bytes | None = None) -> vs.CredentialVault:
    return vs.CredentialVault(tmp_path, key or Fernet.generate_key())


# ── 占位符形态检测（2026-10-23 首跑 #3 实证：hub.env 模板值未换真值）──

def test_placeholder_issues_clean_env_passes():
    """真实形态（长 token + 正常 host）→ 零问题。"""
    assert vs.placeholder_issues(_USER_ENV) == []


def test_placeholder_issues_production_accident_form():
    """生产事故形态回归锚：https://x + len=1 token → 全拦（hub 首跑 #3 即此形态）。"""
    issues = vs.placeholder_issues({
        "ANTHROPIC_BASE_URL": "https://x",
        "ANTHROPIC_AUTH_TOKEN": "t",
        "GH_TOKEN": "g",
    })
    assert any("ANTHROPIC_BASE_URL" in i for i in issues)
    assert any("ANTHROPIC_AUTH_TOKEN" in i and "len=1" in i for i in issues)
    assert any("GH_TOKEN" in i for i in issues)


def test_placeholder_issues_template_angle_brackets():
    """hub.env.example 模板形态（尖括号包值）→ 拦（最常见「没换干净」形态）。"""
    issues = vs.placeholder_issues({
        "ANTHROPIC_BASE_URL": "https://litellm.example",
        "ANTHROPIC_AUTH_TOKEN": "<roc 的 ANTHROPIC_AUTH_TOKEN>",
        "GH_TOKEN": "ghp-real-0123456789abcdef",
    })
    assert len(issues) == 1 and "ANTHROPIC_AUTH_TOKEN" in issues[0]
    assert "<" not in issues[0]  # 问题文案不回显值


def test_placeholder_issues_local_and_internal_hosts_pass():
    """保守黑名单：localhost/内网短名/端口形态合法（本地代理是真实用法，不误拦）。"""
    assert vs.placeholder_issues({
        "ANTHROPIC_BASE_URL": "http://localhost:4000",
        "ANTHROPIC_AUTH_TOKEN": "tok-abcdef0123456789abcdef",
        "GH_TOKEN": "ghp_abcdefghij0123456789",
    }) == []
    assert vs.placeholder_issues({
        "ANTHROPIC_BASE_URL": "https://roc:4000",
        "ANTHROPIC_AUTH_TOKEN": "tok-abcdef0123456789abcdef",
        "GH_TOKEN": "ghp_abcdefghij0123456789",
    }) == []


def test_placeholder_issues_bad_url_no_host():
    """URL 非法到无 host → 同样拦（host=''）。"""
    issues = vs.placeholder_issues({
        "ANTHROPIC_BASE_URL": "not-a-url",
        "ANTHROPIC_AUTH_TOKEN": "tok-abcdef0123456789abcdef",
        "GH_TOKEN": "ghp_abcdefghij0123456789",
    })
    assert len(issues) == 1 and "ANTHROPIC_BASE_URL" in issues[0]


def test_placeholder_issues_model_placeholder_rejected():
    """ANTHROPIC_MODEL 占位形态同拦（A-MEDIUM 2026-10-23：同通道进 vault/容器 env 的事故类残余）。"""
    for bad_model in ("<model>", "changeme", "MODEL"):
        issues = vs.placeholder_issues({
            "ANTHROPIC_BASE_URL": "https://litellm.example",
            "ANTHROPIC_AUTH_TOKEN": "tok-abcdef0123456789abcdef",
            "GH_TOKEN": "ghp_abcdefghij0123456789",
            "ANTHROPIC_MODEL": bad_model,
        })
        assert any("ANTHROPIC_MODEL" in i for i in issues), bad_model
    # 真实模型名（含点/连字符）不误拦；字段缺席/空串 = 合法（可选字段）
    for ok_env in ({"ANTHROPIC_MODEL": "qwen3.8-27b-fast"}, {"ANTHROPIC_MODEL": ""}, {}):
        env = {"ANTHROPIC_BASE_URL": "https://litellm.example",
               "ANTHROPIC_AUTH_TOKEN": "tok-abcdef0123456789abcdef",
               "GH_TOKEN": "ghp_abcdefghij0123456789", **ok_env}
        assert vs.placeholder_issues(env) == [], ok_env


def test_placeholder_issues_url_userinfo_rejected():
    """BASE_URL 含 userinfo（http://user:pass@host）→ 拦（凭据嵌 URL 反模式/误贴通道，A-LOW 2026-10-23）。"""
    issues = vs.placeholder_issues({
        "ANTHROPIC_BASE_URL": "http://sk-secret123:pass@172.32.0.1:9001",
        "ANTHROPIC_AUTH_TOKEN": "tok-abcdef0123456789abcdef",
        "GH_TOKEN": "ghp_abcdefghij0123456789",
    })
    assert len(issues) == 1 and "userinfo" in issues[0]
    assert "sk-secret123" not in issues[0]  # 文案不回显值


# ── 1.1 落盘即密文 + 权限 ─────────────────────────────────────

def test_set_user_writes_single_token_not_plaintext(tmp_path):
    v = _vault(tmp_path)
    v.set_user("roc", _USER_ENV)
    raw = (tmp_path / "vault" / "credentials.json").read_text(encoding="ascii")
    for secret in _USER_ENV.values():  # 任何明文值不得出现在磁盘文件
        assert secret not in raw
    assert raw.strip() == raw.strip().strip('"')  # 单一 Fernet token（无 JSON 字段包裹）


def test_roundtrip_get_user_env(tmp_path):
    key = Fernet.generate_key()
    vs.CredentialVault(tmp_path, key).set_user("roc", _USER_ENV)
    assert vs.CredentialVault(tmp_path, key).get_user_env("roc") == _USER_ENV


@pytest.mark.skipif(os.name != "posix", reason="权限断言仅 posix")
def test_file_0600_dir_0700(tmp_path):
    v = _vault(tmp_path)
    v.set_user("roc", _USER_ENV)
    vdir = tmp_path / "vault"
    assert stat.S_IMODE(vdir.stat().st_mode) == 0o700
    assert stat.S_IMODE((vdir / "credentials.json").stat().st_mode) == 0o600


# ── 1.1 key 缺失/错误明确报错（不猜、不静默重建）──────────────

def test_load_key_missing_raises(tmp_path):
    with pytest.raises(vs.VaultError, match="HUB_VAULT_MASTER_KEY"):
        vs.load_key({})


def test_wrong_key_raises_not_rebuild(tmp_path):
    key = Fernet.generate_key()
    v = vs.CredentialVault(tmp_path, key)
    v.set_user("roc", _USER_ENV)
    bad = vs.CredentialVault(tmp_path, Fernet.generate_key())
    with pytest.raises(vs.VaultError, match="master key"):
        bad.get_user_env("roc")
    # 报错后原密文原样保留（未被清空重建）
    assert vs.CredentialVault(tmp_path, key).get_user_env("roc") == _USER_ENV


def test_malformed_key_raises(tmp_path):
    with pytest.raises(vs.VaultError, match="master key 非法"):
        vs.CredentialVault(tmp_path, b"not-a-fernet-key")


# ── 1.1 原子写：崩溃不半写、无 tmp 残留 ──────────────────────

def test_atomic_write_keeps_old_content_on_failure(tmp_path, monkeypatch):
    key = Fernet.generate_key()
    v = vs.CredentialVault(tmp_path, key)
    v.set_user("roc", _USER_ENV)

    def boom(src, dst):
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        v.set_user("roc", {**_USER_ENV, "GH_TOKEN": "ghp_newvalue"})
    # 旧完整内容保留 + 无 .tmp 残留
    assert vs.CredentialVault(tmp_path, key).get_user_env("roc") == _USER_ENV
    assert not list((tmp_path / "vault").glob("*.tmp"))


# ── 1.2 读取面 + 幂等覆盖 ────────────────────────────────────

def test_get_missing_user_returns_none(tmp_path):
    v = _vault(tmp_path)
    assert v.get_user_env("nobody") is None


def test_set_user_idempotent_overwrite(tmp_path):
    key = Fernet.generate_key()
    v = vs.CredentialVault(tmp_path, key)
    v.set_user("roc", _USER_ENV)
    newer = {**_USER_ENV, "GH_TOKEN": "ghp_rotated_456"}
    v.set_user("roc", newer)  # 幂等覆盖同 user 记录
    assert v.get_user_env("roc") == newer


# ── add-forge-adapter 3.1：平台 token 占位判据 + forge→必需键映射 ──
def test_placeholder_issues_gitlab_gitcode_tokens_same_rule():
    """GITLAB/GITCODE token 同阈值黑名单口径（短值/尖括号/占位词全拦；合法形态放行）。"""
    issues = vs.placeholder_issues({"GITLAB_TOKEN": "short", "GITCODE_TOKEN": "<roc>"})
    assert any("GITLAB_TOKEN" in i for i in issues)
    assert any("GITCODE_TOKEN" in i for i in issues)
    assert vs.placeholder_issues({
        "GITLAB_TOKEN": "glpat-real-0123456789",
        "GITCODE_TOKEN": "gdc_live_real-0123456789",
    }) == []


def test_forge_required_keys_mapping_and_invalid():
    """github/缺省 → GH_TOKEN；gitlab/gitcode → 各自平台 token；非法值 raise 不静默回落。"""
    import pytest
    assert vs.forge_required_keys(None) == ("GH_TOKEN",)
    assert vs.forge_required_keys("github") == ("GH_TOKEN",)
    assert vs.forge_required_keys("gitlab") == ("GITLAB_TOKEN",)
    assert vs.forge_required_keys("gitcode") == ("GITCODE_TOKEN",)
    with pytest.raises(ValueError, match="非法 forge"):
        vs.forge_required_keys("gitee")


def test_project_required_keys_union_and_default():
    """空/None = github 缺省（与旧三值必需等价，github 项目零变化）；多 forge 并集。"""
    base = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN")
    assert vs.project_required_keys(None) == base + ("GH_TOKEN",)
    assert vs.project_required_keys(set()) == base + ("GH_TOKEN",)
    assert vs.project_required_keys({"gitlab"}) == base + ("GITLAB_TOKEN",)
    # 多 forge 并集：set 迭代序随 hash 种子漂移——顺序无关断言（消费方只查存在性）
    assert set(vs.project_required_keys({"github", "gitlab"})) == \
        set(base) | {"GH_TOKEN", "GITLAB_TOKEN"}
    import pytest
    with pytest.raises(ValueError):
        vs.project_required_keys({"gitee"})
