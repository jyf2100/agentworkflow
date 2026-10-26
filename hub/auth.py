"""hub 会话基元 + MM OAuth2 流程（add-hub-slice-2b，spec: hub-oauth-session）。

D2：会话 = 自包含 HMAC 签名 cookie（payload {mm_user_id, username, exp}；cryptography
生态手写 HMAC-SHA256，零新依赖）——hub 重启/MM 宕机会话不丢（F7），无服务端会话表
（排度器单写者约束不动）。verify 失败一律 None（视同无会话，不抛——spec「伪造拒绝」
不产生报错副作用）。D8：OAuth 出站经注入式 http client（测试 FakeMMClient），真形态
httpx 同鸭子面。state = 一次性 CSRF nonce 载体（payload 仅 nonce），非身份、非会话
——与会话同签名机制但不同范畴，verify_session 的半会话校验即两范畴之界。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import quote

import httpx

SESSION_COOKIE = "hub_session"
STATE_COOKIE = "hub_state"
STATE_TTL_S = 600  # authorize → callback 往返窗口（10 min）
DEFAULT_SESSION_TTL_S = 7 * 86400  # D2：默认 7 天可配


class AuthError(Exception):
    """OAuth 流程失败——消息不含任何凭据/secret 值。"""


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_token(payload: dict, key: bytes, *, ttl_s: int, now: float | None = None) -> str:
    """payload + exp 签名为单段 token（`<payload>.<sig>`）。"""
    body = {**payload, "exp": int((now if now is not None else time.time()) + ttl_s)}
    raw = _b64e(json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64e(hmac.new(key, raw.encode(), hashlib.sha256).digest())
    return f"{raw}.{sig}"


def verify_token(token: str, key: bytes, *, now: float | None = None) -> dict | None:
    """有效返回 payload（含 exp）；过期/伪造/结构坏/缺 exp → None。"""
    try:
        raw, sig = token.split(".", 1)
        expect = _b64e(hmac.new(key, raw.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expect):
            return None
        payload = json.loads(_b64d(raw))
        if not isinstance(payload, dict) or "exp" not in payload:
            return None
        if (now if now is not None else time.time()) > float(payload["exp"]):
            return None
        return payload
    except Exception:
        return None


def verify_session(token: str, key: bytes, *, now: float | None = None) -> dict | None:
    """会话专用：verify_token + 身份字段完备校验（缺 mm_user_id/username = 半会话 → None）。"""
    p = verify_token(token, key, now=now)
    if p is None or not p.get("mm_user_id") or not p.get("username"):
        return None
    return {"mm_user_id": p["mm_user_id"], "username": p["username"]}


def mm_configured(cfg) -> bool:
    """auth 群可用判据：MM 四项齐（base_url/client_id/client_secret/session_secret）。"""
    return all((cfg.mm_base_url, cfg.mm_client_id, cfg.mm_client_secret, cfg.session_secret))


def build_authorize_url(cfg, redirect_uri: str, state: str) -> str:
    return (
        f"{cfg.mm_base_url}/oauth/authorize?response_type=code"
        f"&client_id={quote(cfg.mm_client_id)}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        f"&state={quote(state, safe='')}"
    )


def exchange_code(cfg, code: str, redirect_uri: str, client=None) -> tuple[str, str]:
    """code+secret → MM token → users/me → (mm_user_id, username)。

    client 注入接缝（D8）：缺省 httpx.Client；失败（拒绝/非 200/网络不可达）一律
    AuthError 明确报错——不产生半会话。
    """
    c = client or httpx.Client(timeout=10)
    try:
        r = c.post(
            f"{cfg.mm_base_url}/oauth/access_token",  # MM 网页路由（非 /api/v4——真机 11.10.1 坐实，API 前缀形态 404）
            data={
                "grant_type": "authorization_code",
                "client_id": cfg.mm_client_id,
                "client_secret": cfg.mm_client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        if r.status_code != 200:
            raise AuthError(f"MM token 端点拒绝（status={r.status_code}）——code 无效或已过期")
        access = r.json().get("access_token")
        if not access:
            raise AuthError("MM token 响应缺 access_token")
        r2 = c.get(
            f"{cfg.mm_base_url}/api/v4/users/me",
            headers={"Authorization": f"Bearer {access}"},
        )
        if r2.status_code != 200:
            raise AuthError(f"MM users/me 失败（status={r2.status_code}）")
        user = r2.json()
        if not user.get("id") or not user.get("username"):
            raise AuthError("MM users/me 响应缺 id/username")
        return str(user["id"]), str(user["username"])
    except AuthError:
        raise
    except Exception as e:  # 网络不可达等（F7：MM 宕机 = 无新登录，明确报错不产会话）
        raise AuthError(f"MM 不可达/调用失败：{type(e).__name__}") from None
    finally:
        if client is None:
            c.close()
