#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_sandbox_publication.py — task 5.4 长期凭据留 host-side + prove absent 测试。

spec task 5.4 契约：「Keep GitHub, SMTP, cloud, and model publication credentials host-side
and prove they are absent from sandbox environment and artifacts.」

覆盖三件事：
  * **留 host-side**：model publication 也加入 HOST_CREDENTIAL_KINDS（host_side_publish verified）；
  * **prove absent from sandbox environment**：``sanitize_sandbox_env`` 移除长期凭据 env var +
    ``assert_credentials_absent(env=...)`` 断言净化后 env 零长期凭据；
  * **prove absent from artifacts**：``assert_credentials_absent(text=...)`` 复用
    ``artifact_store.redact_secrets`` 判定 artifact 文本无凭据值模式。

AAA；纯库。跑：
    python3 -m pytest scripts/test_sandbox_publication.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import artifact_store as AR  # noqa: E402
import sandbox_publication as SP  # noqa: E402


# ════════════════════════════════════════════════════════════════════════════
# Section 5 task 5.4：长期凭据留 host-side（含 model publication）
# ════════════════════════════════════════════════════════════════════════════
def test_model_deploy_is_host_credential_kind():
    """5.4：model publication 长期凭据也留 host（HOST_CREDENTIAL_KINDS 含 model_deploy）。"""
    assert SP.PUB_MODEL_DEPLOY in SP.HOST_CREDENTIAL_KINDS


def test_host_side_publish_model_deploy_no_credentials_blocked():
    """5.4：model_deploy 无宿主长期凭据 → no_credentials blocked（fail-closed，不降级）。"""
    req = SP.HostPublicationRequest(
        kind=SP.PUB_MODEL_DEPLOY, target="registry/model:v1", idempotency_key="sha256:abc")
    r = SP.host_side_publish(req, host_credentials={})
    assert r.status == "no_credentials"
    assert "sandbox" in r.evidence                          # 证据明确：凭据从不进 sandbox


def test_host_side_publish_model_deploy_host_verified():
    """5.4：model_deploy 宿主有长期凭据 → host verified published（sandbox 内无凭据）。"""
    req = SP.HostPublicationRequest(
        kind=SP.PUB_MODEL_DEPLOY, target="registry/model:v1", idempotency_key="sha256:abc")
    r = SP.host_side_publish(req, host_credentials={SP.PUB_MODEL_DEPLOY: True})
    assert r.status == "published"


def test_all_four_credential_domains_are_host_only():
    """5.4：GitHub / SMTP / cloud / model 四类长期凭据都在 HOST_CREDENTIAL_KINDS（留 host）。"""
    assert SP.PUB_GIT_PUSH in SP.HOST_CREDENTIAL_KINDS
    assert SP.PUB_PR_CREATE in SP.HOST_CREDENTIAL_KINDS
    assert SP.PUB_SMTP_SEND in SP.HOST_CREDENTIAL_KINDS
    assert SP.PUB_CLOUD_DEPLOY in SP.HOST_CREDENTIAL_KINDS
    assert SP.PUB_MODEL_DEPLOY in SP.HOST_CREDENTIAL_KINDS


# ════════════════════════════════════════════════════════════════════════════
# task 5.4 prove absent from sandbox environment：sanitize_sandbox_env
# ════════════════════════════════════════════════════════════════════════════
def test_sanitize_sandbox_env_strips_all_credential_domains():
    """5.4：sanitize 移除 GitHub/SMTP/cloud/model 长期凭据 env var，sandbox env 零长期凭据。"""
    env = {"PATH": "/usr/bin", "HOME": "/tmp",
           "GITHUB_TOKEN": "ghp_x", "SMTP_PASSWORD": "p",
           "AWS_SECRET_ACCESS_KEY": "s", "MODEL_API_KEY": "m", "HF_TOKEN": "h"}
    sanitized, removed = SP.sanitize_sandbox_env(env)
    for cred in ("GITHUB_TOKEN", "SMTP_PASSWORD", "AWS_SECRET_ACCESS_KEY", "MODEL_API_KEY", "HF_TOKEN"):
        assert cred not in sanitized
    assert sanitized["PATH"] == "/usr/bin" and sanitized["HOME"] == "/tmp"   # 非凭据保留
    assert set(removed) == {"GITHUB_TOKEN", "SMTP_PASSWORD", "AWS_SECRET_ACCESS_KEY",
                            "MODEL_API_KEY", "HF_TOKEN"}


def test_sanitize_sandbox_env_keeps_non_credential_untouched():
    """5.4：非凭据 env var 原样保留（净化只移除长期凭据）。"""
    env = {"PATH": "/x", "NODE_ENV": "test", "PYTHONPATH": "/srv"}
    sanitized, removed = SP.sanitize_sandbox_env(env)
    assert sanitized == env
    assert removed == ()


def test_sanitize_sandbox_env_empty():
    sanitized, removed = SP.sanitize_sandbox_env({})
    assert sanitized == {} and removed == ()


# ════════════════════════════════════════════════════════════════════════════
# task 5.4 prove absent：assert_credentials_absent（env + artifact 文本）
# ════════════════════════════════════════════════════════════════════════════
def test_assert_credentials_absent_clean_env_passes():
    """5.4：净化后 env（无长期凭据 var）→ prove 断言通过。"""
    SP.assert_credentials_absent(env={"PATH": "/x", "HOME": "/tmp"})   # 不抛即通过


def test_assert_credentials_absent_leaked_env_raises():
    """5.4：env 含长期凭据 var → CredentialLeakError（fail-loud，绝不静默放过）。"""
    with pytest.raises(SP.CredentialLeakError):
        SP.assert_credentials_absent(env={"GITHUB_TOKEN": "ghp_x"})


def test_assert_credentials_absent_clean_text_passes():
    """5.4 prove absent from artifacts：干净 artifact 文本 → 断言通过。"""
    SP.assert_credentials_absent(text="test output: 100 passed, 0 failed")   # 不抛


def test_assert_credentials_absent_credential_in_text_raises():
    """5.4：artifact 文本含 GitHub PAT → 泄漏断言失败（复用 redact_secrets 判定）。"""
    with pytest.raises(SP.CredentialLeakError):
        SP.assert_credentials_absent(text="auth header: token ghp_AAAAabcdefghijklmnopqrstuvwxyz1234")


def test_assert_credentials_absent_redacted_artifact_passes():
    """5.4：artifact 经 redact_secrets 消毒后无凭据值 → prove 断言通过（端到端 absent 链）。"""
    raw = "deploy with token ghp_AAAAabcdefghijklmnopqrstuvwxyz1234 and bearer xyz"
    SP.assert_credentials_absent(text=AR.redact_secrets(raw))   # 消毒后通过


def test_assert_credentials_absent_both_env_and_text_checked():
    """5.4：env 干净但 text 含凭据 → 仍 raise（两个维度独立 prove）。"""
    with pytest.raises(SP.CredentialLeakError):
        SP.assert_credentials_absent(env={"PATH": "/x"},
                                     text="deploy token=AKIAIOSFODNN7EXAMPLE leaked")
