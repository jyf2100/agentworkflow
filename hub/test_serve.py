"""serve.py 装配测试——build_app 两形态（add-hub-slice-2a 4.2，design D3/D4）。

env-only（未配 master key，合法不降级）/ vault 装配（key 在）/ key 坏明确崩
（不静默吞成 env-only）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

import serve
import vault_store


def _env(tmp_path: Path) -> dict:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)  # 部署事实：data_root 由 RUNBOOK 预建（chown 流程）
    return {
        "HUB_USER_ID": "roc",
        "HUB_USER_ANTHROPIC_BASE_URL": "https://litellm.example",
        "HUB_USER_ANTHROPIC_AUTH_TOKEN": "tok",
        "HUB_USER_GH_TOKEN": "ghp",
        "HUB_IMAGE_TAG_DEFAULT": "pa-executor:v0.1.3",
        "HUB_DATA_ROOT": str(tmp_path / "data"),
    }


def test_build_app_env_only_without_key(tmp_path, monkeypatch):
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("HUB_VAULT_MASTER_KEY", raising=False)
    app, lc, cfg = serve.build_app()
    assert lc._vault is None  # 切片 1 env-only 合法形态（D3），非降级


def test_build_app_with_key_mounts_vault(tmp_path, monkeypatch):
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HUB_VAULT_MASTER_KEY", Fernet.generate_key().decode())
    app, lc, cfg = serve.build_app()
    assert lc._vault is not None  # key 在 = 保险箱装配进 lifecycle


def test_build_app_mounts_vault_into_api_layer(tmp_path, monkeypatch):
    """回归锚（2026-10-27 真机 503）：key 在时 vault 须透传 create_app——凭据面板
    GET 不再「保险箱未配置 503」（此前 serve 只装进 Lifecycle，API 面独漏）。"""
    from fastapi.testclient import TestClient

    import auth
    env = _env(tmp_path)
    env.update({"HUB_MM_BASE_URL": "https://mm.example.com", "HUB_MM_CLIENT_ID": "cid",
                "HUB_MM_CLIENT_SECRET": "sec", "HUB_SESSION_SECRET": "S" * 43})
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HUB_VAULT_MASTER_KEY", Fernet.generate_key().decode())
    app, lc, cfg = serve.build_app()
    client = TestClient(app)
    client.cookies.set(auth.SESSION_COOKIE, auth.sign_token(
        {"mm_user_id": "probe00112233445566778899aabb", "username": "zhang3"},
        lc.cfg.session_secret, ttl_s=60))
    resp = client.get("/panel/credentials")
    assert resp.status_code == 200 and "保险箱未配置" not in resp.text  # 修复前 503


def test_build_app_bad_key_raises_not_silent(tmp_path, monkeypatch):
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HUB_VAULT_MASTER_KEY", "not-a-fernet-key")  # 配了但坏
    with pytest.raises(vault_store.VaultError, match="master key 非法"):
        serve.build_app()  # 明确崩启动——绝不静默吞成 env-only


# ── add-hub-slice-4a 4.2：通知通道装配（D8 两级回退之一级）────────

def test_build_app_notifier_none_without_bot_token(tmp_path, monkeypatch):
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("HUB_VAULT_MASTER_KEY", raising=False)
    monkeypatch.delenv("HUB_MM_BOT_TOKEN", raising=False)
    app, lc, cfg = serve.build_app()
    assert lc.notifier is None  # 未配 bot token = 通道关闭（run 终态照常落库零通知）


def test_build_app_notifier_mounted_with_bot_token_and_base_url(tmp_path, monkeypatch):
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HUB_MM_BASE_URL", "https://mm.example.com")
    monkeypatch.setenv("HUB_MM_BOT_TOKEN", "bot-tok")
    app, lc, cfg = serve.build_app()
    assert lc.notifier is not None  # bot token + base_url 齐 = Notifier 注入（daemon 陈旧检测同引用）
