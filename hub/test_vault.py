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
