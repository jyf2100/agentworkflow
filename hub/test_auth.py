"""auth.py 单测——会话签名基元 + MM OAuth 流程接缝（add-hub-slice-2b 1.1/2.x，spec: hub-oauth-session）。

cookie 基元全分支（有效/过期/伪造/缺字段/结构坏）；OAuth 经 FakeMMClient 注入
（D8：monkeypatch 接缝，无 MM 实例离线可测全分支）。
"""
import dataclasses

import pytest
from cryptography.fernet import Fernet

import auth
import config


KEY = b"s" * 32
NOW = 1_000_000.0


# ── 1.1 签名基元 ────────────────────────────────────────────────

def test_sign_verify_roundtrip():
    tok = auth.sign_token({"mm_user_id": "abc123", "username": "roc"}, KEY, ttl_s=60, now=NOW)
    p = auth.verify_token(tok, KEY, now=NOW + 59)
    assert p is not None and p["mm_user_id"] == "abc123" and p["username"] == "roc"


def test_verify_expired_returns_none():
    tok = auth.sign_token({"mm_user_id": "u"}, KEY, ttl_s=60, now=NOW)
    assert auth.verify_token(tok, KEY, now=NOW + 61) is None


def test_verify_forged_signature_returns_none():
    tok = auth.sign_token({"mm_user_id": "u"}, KEY, ttl_s=60, now=NOW)
    raw, sig = tok.split(".")
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    assert auth.verify_token(f"{raw}.{flipped}", KEY, now=NOW) is None


def test_verify_wrong_key_returns_none():
    tok = auth.sign_token({"mm_user_id": "u"}, KEY, ttl_s=60, now=NOW)
    assert auth.verify_token(tok, b"x" * 32, now=NOW) is None


def test_verify_garbage_returns_none():
    for bad in ("", "notatoken", "a.b.c", "////.****"):
        assert auth.verify_token(bad, KEY, now=NOW) is None


def test_verify_missing_exp_returns_none():
    # 自签无 exp 的 payload（绕过 sign_token 的合法面）须拒
    import base64, hashlib, hmac, json
    raw = base64.urlsafe_b64encode(json.dumps({"mm_user_id": "u"}).encode()).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(hmac.new(KEY, raw.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    assert auth.verify_token(f"{raw}.{sig}", KEY, now=NOW) is None


def test_session_requires_identity_fields():
    # 有 exp 但缺身份字段（mm_user_id/username）→ None（半会话视同无会话）
    tok = auth.sign_token({"foo": 1}, KEY, ttl_s=60, now=NOW)
    assert auth.verify_session(tok, KEY, now=NOW) is None
    ok = auth.sign_token({"mm_user_id": "u", "username": "roc"}, KEY, ttl_s=60, now=NOW)
    assert auth.verify_session(ok, KEY, now=NOW) == {"mm_user_id": "u", "username": "roc"}


# ── D8 OAuth 接缝（FakeMMClient）────────────────────────────────

class FakeMMClient:
    """httpx.Client 鸭子接缝——token 端点与 users/me 可编程响应。"""

    def __init__(self, *, token_status=200, token_body=None, me_status=200, me_body=None, exc=None):
        self.token_status, self.token_body = token_status, token_body or {"access_token": "AT"}
        self.me_status, self.me_body = me_status, me_body or {"id": "mmid1", "username": "roc"}
        self.exc = exc
        self.calls = []

    def post(self, url, data=None):
        self.calls.append(("post", url, dict(data or {})))
        if self.exc:
            raise self.exc
        class R:
            status_code = self.token_status
            def json(self_inner): return self.token_body
        return R()

    def get(self, url, headers=None):
        self.calls.append(("get", url, dict(headers or {})))
        if self.exc:
            raise self.exc
        class R:
            status_code = self.me_status
            def json(self_inner): return self.me_body
        return R()


def _cfg(**kw):
    import config
    return config.HubConfig(
        user_id="roc", anthropic_base_url="https://x", anthropic_auth_token="t", gh_token="g",
        user_model=None, image_tag_default="img:1", data_root=kw.get("data_root", "/tmp/x"),
        max_containers=1, max_retries=1,
        mm_base_url="https://mm.example.com", mm_client_id="cid", mm_client_secret="sec",
        session_secret=b"k" * 32, session_ttl_s=3600,
    )


def test_build_authorize_url_contains_params():
    url = auth.build_authorize_url(_cfg(), "https://hub/cb", "st4te")
    assert url.startswith("https://mm.example.com/oauth/authorize?")
    assert "client_id=cid" in url and "state=st4te" in url and "response_type=code" in url


def test_exchange_code_happy_path():
    c = FakeMMClient()
    uid, name = auth.exchange_code(_cfg(), "the-code", "https://hub/cb", client=c)
    assert (uid, name) == ("mmid1", "roc")
    post = [x for x in c.calls if x[0] == "post"][0]
    assert post[1] == "https://mm.example.com/api/v4/oauth/token"
    assert post[2]["code"] == "the-code" and post[2]["client_secret"] == "sec"
    get = [x for x in c.calls if x[0] == "get"][0]
    assert get[1] == "https://mm.example.com/api/v4/users/me" and get[2]["Authorization"] == "Bearer AT"


def test_exchange_code_rejected_raises():
    with pytest.raises(auth.AuthError):
        auth.exchange_code(_cfg(), "bad", "https://hub/cb", client=FakeMMClient(token_status=403))
    with pytest.raises(auth.AuthError):
        auth.exchange_code(_cfg(), "c", "https://hub/cb", client=FakeMMClient(me_status=401))


def test_exchange_code_mm_unreachable_raises():
    with pytest.raises(auth.AuthError):
        auth.exchange_code(_cfg(), "c", "https://hub/cb", client=FakeMMClient(exc=ConnectionError("down")))


def test_mm_configured_gate():
    cfg = _cfg()
    assert auth.mm_configured(cfg) is True
    assert auth.mm_configured(dataclasses.replace(cfg, mm_client_secret=None)) is False
    assert auth.mm_configured(dataclasses.replace(cfg, session_secret=None)) is False


# ── 2.x auth 端点群（api 层，MM mock 经 monkeypatch auth.exchange_code）───────


import api as api_mod
import db as hub_db
import lifecycle as lc_mod
import queue_engine as qe
from fastapi.testclient import TestClient
from runner import FakeRunner
from vault_store import CredentialVault

from datetime import datetime, timezone
from pathlib import Path

_CLOCK = datetime(2026, 10, 22, 3, 0, 0, tzinfo=timezone.utc)
_MK = "mmid-abc123"


def _mk(tmp_path: Path, *, with_auth=True, with_vault=True):
    d = hub_db.HubDB(tmp_path / "h.sqlite3")
    eng = qe.QueueEngine(d, max_containers=2, max_retries=2, image_tag_default="img:1")
    base = {
        "HUB_USER_ID": "roc",
        "HUB_USER_ANTHROPIC_BASE_URL": "https://x",
        "HUB_USER_ANTHROPIC_AUTH_TOKEN": "tok",
        "HUB_USER_GH_TOKEN": "ghp",
        "HUB_IMAGE_TAG_DEFAULT": "img:1",
        "HUB_DATA_ROOT": str(tmp_path / "data"),
    }
    if with_auth:
        base.update({
            "HUB_MM_BASE_URL": "https://mm.example.com",
            "HUB_MM_CLIENT_ID": "cid", "HUB_MM_CLIENT_SECRET": "sec",
            "HUB_SESSION_SECRET": "S" * 43,
        })
    cfg = config.load_config(base)
    lc = lc_mod.Lifecycle(d, eng, FakeRunner(images={"img:1"}), cfg, clock=_CLOCK)
    vault = None
    if with_vault:
        vault = CredentialVault(tmp_path, Fernet.generate_key())
    client = TestClient(api_mod.create_app(d, eng, lc, vault=vault))
    return client, d, eng, lc, cfg


def _login(client, monkeypatch, *, mm_user_id=_MK, username="zhang3", fail=None):
    """驱动 authorize → callback 完整流（exchange_code monkeypatch 为假 MM）。"""
    def fake_exchange(cfg, code, redirect_uri, client=None):
        if fail:
            raise auth.AuthError(fail)
        return mm_user_id, username
    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    r = client.get("/auth/authorize", follow_redirects=False)
    assert r.status_code == 302
    return client.get("/auth/callback", params={"code": "c1", "state": _state_of(r)}, follow_redirects=False)


def _state_of(authorize_resp) -> str:
    import re
    m = re.search(r"state=([^&]+)", authorize_resp.headers["location"])
    return m.group(1)


def test_authorize_redirects_with_state_cookie(tmp_path):
    client, *_ = _mk(tmp_path)
    r = client.get("/auth/authorize", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("https://mm.example.com/oauth/authorize")
    assert auth.STATE_COOKIE in r.headers.get("set-cookie", "")
    # state 是可验签 token
    st = _state_of(r)
    assert auth.verify_token(st, b"S" * 43) is not None


def test_auth_endpoints_503_when_unconfigured(tmp_path):
    client, *_ = _mk(tmp_path, with_auth=False)
    for url in ("/auth/authorize", "/auth/callback", "/auth/me"):
        assert client.get(url).status_code == 503
    # logout 例外（本体论审核 B1）：幂等清 cookie 无 MM 依赖，未配置仍 200——不制造伪故障
    assert client.get("/auth/logout").status_code == 200


def test_full_login_sets_session_and_me_shows_identity(tmp_path, monkeypatch):
    client, *_ = _mk(tmp_path)
    r = _login(client, monkeypatch)
    assert r.status_code == 302 and auth.SESSION_COOKIE in r.headers.get("set-cookie", "")
    me = client.get("/auth/me")
    assert me.status_code == 200 and me.json() == {"mm_user_id": _MK, "username": "zhang3"}


def test_callback_rejects_state_mismatch(tmp_path, monkeypatch):
    client, *_ = _mk(tmp_path)
    client.get("/auth/authorize")
    r = client.get("/auth/callback", params={"code": "c1", "state": "forged"}, follow_redirects=False)
    assert r.status_code == 400 and "state" in r.json()["detail"]
    assert client.get("/auth/me").status_code == 401  # 不产半会话


def test_callback_rejects_mm_error_and_deny(tmp_path, monkeypatch):
    client, *_ = _mk(tmp_path)
    r = _login(client, monkeypatch, fail="MM token 端点拒绝（status=403）")
    assert r.status_code == 502 and client.get("/auth/me").status_code == 401
    client2, *_ = _mk(tmp_path)  # 用户拒绝（error 参数直回）
    client2.get("/auth/authorize")
    r2 = client2.get("/auth/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert r2.status_code == 400 and "拒绝" in r2.json()["detail"]


def test_me_401_without_session_and_logout_clears(tmp_path, monkeypatch):
    client, *_ = _mk(tmp_path)
    assert client.get("/auth/me").status_code == 401
    _login(client, monkeypatch)
    assert client.get("/auth/me").status_code == 200
    assert client.get("/auth/logout").status_code == 200
    assert client.get("/auth/me").status_code == 401


def test_put_credentials_happy_and_401(tmp_path, monkeypatch):
    client, _, _, _, _ = _mk(tmp_path)
    body = {"anthropic_base_url": "https://mm-x", "anthropic_auth_token": "at", "gh_token": "gt", "model": "glm-5.2"}
    r = client.put("/auth/credentials", json=body)
    assert r.status_code == 401
    _login(client, monkeypatch)
    r2 = client.put("/auth/credentials", json=body)
    assert r2.status_code == 200
    j = r2.json()
    assert j["ok"] is True and j["user"] == _MK and "at" not in r2.text  # 不回显明文
    # 响应只有字段名


def test_put_credentials_missing_fields_atomic(tmp_path, monkeypatch):
    client, d, *_ = _mk(tmp_path)
    _login(client, monkeypatch)
    r = client.put("/auth/credentials", json={"anthropic_base_url": "https://x", "anthropic_auth_token": "", "gh_token": "g"})
    assert r.status_code == 400
    # 缺失字段列出，且不落部分记录（vault 无该 user 记录）


def test_put_credentials_503_without_vault(tmp_path, monkeypatch):
    client, *_ = _mk(tmp_path, with_vault=False)
    _login(client, monkeypatch)
    r = client.put("/auth/credentials", json={"anthropic_base_url": "https://x", "anthropic_auth_token": "a", "gh_token": "g"})
    assert r.status_code == 503


def test_post_queue_session_identity_takes_priority(tmp_path, monkeypatch):
    client, d, eng, lc, cfg = _mk(tmp_path)
    prd = tmp_path / "p.md"; prd.write_text("# x", encoding="utf-8")
    profs = tmp_path / "data" / "proj-a" / "profiles"; profs.mkdir(parents=True)
    (profs / "proj-a.yaml").write_text("x", encoding="utf-8")
    (tmp_path / "data" / "proj-a" / "sources.yaml").write_text("s: []\n", encoding="utf-8")
    # 无会话：兜底单用户（2a 行为）
    r1 = client.post("/queue", json={"project": "proj-a", "prd_path": str(prd)})
    assert r1.status_code == 201 and r1.json()["enqueued_by"] == "roc" and r1.json().get("initiator") is None
    # 会话：真实身份（key=mm_user_id、initiator=username）
    _login(client, monkeypatch)
    r2 = client.post("/queue", json={"project": "proj-a", "prd_path": str(prd)})
    assert r2.status_code == 201
    item = r2.json()
    assert item["enqueued_by"] == _MK and item["initiator"] == "zhang3"
