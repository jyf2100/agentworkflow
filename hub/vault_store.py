"""凭据保险箱——per-user 凭据 Fernet 整 payload 加密存储（add-hub-slice-2a，spec: hub-credential-vault）。

凭据唯一持久形态 = `<data_root>/vault/credentials.json` 单一 Fernet token（明文 MUST NOT
落盘；文件 0600 / 目录 0700）。master key 与密文分离：宿主 0600 文件经 compose env
`HUB_VAULT_MASTER_KEY` 注入，绝不入仓。读取面仅两个（spec「原子写与读取面」）：
派发时 get_user_env + bootstrap vault-import——不存在「列出全部明文」入口。
key 缺失/错误/密文损坏一律明确报错，绝不猜测或静默清空重建。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken

KEY_ENV = "HUB_VAULT_MASTER_KEY"
VAULT_DIRNAME = "vault"
VAULT_FILENAME = "credentials.json"

# 注入 env-file 的必需三值（F9）；ANTHROPIC_MODEL（可选模型路由）随 payload 一并存取。
# add-forge-adapter 3.1 起派发面必需键改为**按项目 forge 动态计算**（project_required_keys）——
# 本常量仅作历史参照保留，勿在新代码引用（GH_TOKEN 不再是无条件必需——gitlab/gitcode-only
# 项目不被迫造假 GH_TOKEN）。
VAULT_REQUIRED_KEYS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "GH_TOKEN")

# 平台 token 键映射（add-forge-adapter 3.1）：forge 声明 → 派发必需的平台 token
FORGE_TOKEN_KEYS = {"github": "GH_TOKEN", "gitlab": "GITLAB_TOKEN", "gitcode": "GITCODE_TOKEN"}

# 占位符形态判据（2026-10-23 hub 首跑 #3 实证：hub.env 模板值未换——BASE_URL host=x、
# token len=1——「非空」检查放过，注入容器后 claude "Not logged in" 烧 3 分钟才暴露）。
# 刻意保守起步：黑名单只收模板事故形态，不收 localhost/内网短名（本地代理是合法用法）；
# token 阈值 8：实证形态 len=1 全拦，内网 LiteLLM 自定义短 key（如 sk-343486，9 字符）
# 合法放行（2026-10-23 用户真值被阈值 20 误拦——公网 token ≥40 的假设不适用于内网代理）。
# 判据纯形态、零网络探测。
PLACEHOLDER_HOSTS = {"x"}   # https://x（hub.env 模板占位）
MIN_TOKEN_LEN = 8


def placeholder_issues(env: dict[str, str]) -> list[str]:
    """凭据占位符形态检测：返回问题清单（空 = 通过）。只回显键名/长度/host，绝不回显值。

    消费点：lifecycle._credentials（派发前 fail-fast，不烧容器）、cli.vault-import 与
    api PUT /auth/credentials（录入口拦截——占位符进保险箱会跨重启持久，比进 env 更糟）。

    拦截双层结构（本体论审核 A-LOW 2026-10-23）：录入口拦截是**建议层**——set_user 本体
    不校验（docker exec 直调等旁路可绕）；**强制层**在派发前 lifecycle._credentials 再查，
    占位/缺键一律 fail-fast 不起容器。勿把录入口拦截当全量保障。"""
    issues: list[str] = []
    url_s = env.get("ANTHROPIC_BASE_URL", "")
    if url_s:
        u = urlparse(url_s)
        host = u.hostname or ""
        if host in PLACEHOLDER_HOSTS or "<" in url_s or ">" in url_s or not host:
            # 不回显 host 值（review LOW）：BASE_URL 是被误贴 token 的常见通道，片段不进日志/通知
            issues.append("ANTHROPIC_BASE_URL 疑似占位符/非法（host 为空或命中模板黑名单）")
        if u.username or u.password:
            # 本体论审核 A-LOW 2026-10-23：userinfo 位嵌凭据（http://user:pass@host）是误贴通道，同不拦
            issues.append("ANTHROPIC_BASE_URL 含 userinfo（凭据嵌 URL 反模式）")
    for k in ("ANTHROPIC_AUTH_TOKEN", "GH_TOKEN", "GITLAB_TOKEN", "GITCODE_TOKEN"):
        v = env.get(k, "")
        if v and (len(v) < MIN_TOKEN_LEN or "<" in v or v.lower() in ("token", "changeme")):
            issues.append(f"{k} 疑似占位符（len={len(v)}）")
    # ANTHROPIC_MODEL（可选字段）：非空时占位形态同拦——它经同一通道进 vault/容器 env，
    # "<model>"/"changeme" 恰是本轮要防的事故类残余（本体论审核 A-MEDIUM 2026-10-23）
    m = env.get("ANTHROPIC_MODEL", "")
    if m and ("<" in m or ">" in m or m.lower() in ("model", "changeme", "xxx")):
        issues.append("ANTHROPIC_MODEL 疑似占位符")
    return issues


def forge_required_keys(forge: str | None) -> tuple[str, ...]:
    """单个 forge 声明 → 平台 token 必需键（github/缺省 → GH_TOKEN；add-forge-adapter 3.1）。

    非法值 raise ValueError 不静默回落 github（与 pa 侧 forge_api.validate_profile 同口径）；
    调用方（lifecycle._credentials）catch 后 abandon 明示，绝不带错派发。"""
    f = (forge or "github").strip().lower()
    if f not in FORGE_TOKEN_KEYS:
        raise ValueError(f"非法 forge 值：{forge!r}（合法：{'/'.join(FORGE_TOKEN_KEYS)}）")
    return (FORGE_TOKEN_KEYS[f],)


def project_required_keys(forges) -> tuple[str, ...]:
    """项目 forge 声明集合 → 派发必需键集 = ANTHROPIC 两值 ∪ 各平台 token 并集。

    None/空集 = 无 forge 声明 → github 缺省（与旧 VAULT_REQUIRED_KEYS 等价——github
    项目行为零变化）。非法 forge 值传播 ValueError（fail-fast 不猜）。"""
    keys: list[str] = ["ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"]
    for f in (forges or ["github"]):
        for k in forge_required_keys(f):
            if k not in keys:
                keys.append(k)
    return tuple(keys)


class VaultError(Exception):
    """保险箱读写失败——消息只含路径/键名，绝不含凭据值。"""


class VaultKeyMissing(VaultError):
    """HUB_VAULT_MASTER_KEY 未配置——env-only 合法形态的判定依据（serve 据此装配 vault=None）。"""


def load_key(env: dict[str, str] | None = None) -> bytes:
    """从 env 读 master key；缺失/非法明确报错（不猜）。"""
    e = os.environ if env is None else env
    raw = e.get(KEY_ENV, "")
    if not raw:
        raise VaultKeyMissing(f"缺少 env {KEY_ENV}（宿主 vault.key 内容，见 RUNBOOK hub compose 部署节）")
    try:
        return raw.encode("ascii")
    except UnicodeEncodeError:
        raise VaultError(f"{KEY_ENV} 非法（须 urlsafe base64 ascii）") from None


class CredentialVault:
    """整 payload 单 Fernet token 的 per-user 凭据存取（字段级加密无增益——解密即全量进内存，design D2）。"""

    def __init__(self, data_root: Path | str, key: bytes):
        try:
            self._f = Fernet(key)
        except (ValueError, TypeError) as e:
            raise VaultError(f"master key 非法（须 32 字节 urlsafe base64）：{e}") from None
        self._dir = Path(data_root) / VAULT_DIRNAME
        self._path = self._dir / VAULT_FILENAME

    def _read_all(self) -> dict:
        """读全量 payload；文件不存在 = 空保险箱（非错误）。"""
        if not self._path.is_file():
            return {"users": {}}
        token = self._path.read_text(encoding="utf-8").strip()
        try:
            payload = self._f.decrypt(token.encode("ascii"))
        except InvalidToken:
            raise VaultError("解密失败：master key 不匹配或密文损坏（不猜——绝不静默清空重建保险箱）") from None
        except (UnicodeEncodeError, ValueError):
            raise VaultError("保险箱文件内容非法（须单一 Fernet token）") from None
        users = json.loads(payload).get("users")
        if not isinstance(users, dict):
            raise VaultError("保险箱 payload 结构非法（users 字典）")
        return {"users": users}

    def _write_all(self, payload: dict) -> None:
        """原子写：同目录 tmp → chmod 0600 → rename（崩溃不产半写文件）；目录 0700。"""
        self._dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._dir, 0o700)
        token = self._f.encrypt(json.dumps(payload, ensure_ascii=False).encode()).decode("ascii")
        fd, tmp = tempfile.mkstemp(dir=self._dir, prefix=".credentials.", suffix=".tmp")
        try:
            os.write(fd, token.encode("ascii"))
            os.close(fd)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def set_user(self, user: str, env: dict[str, str]) -> None:
        """写入/覆盖一个 user 的凭据记录（幂等）。"""
        payload = self._read_all()
        payload["users"][user] = dict(env)
        self._write_all(payload)

    def get_user_env(self, user: str) -> dict[str, str] | None:
        """按 user 解密取 env 字典；无记录 = None（调用方决定兜底路径）。"""
        rec = self._read_all()["users"].get(user)
        return dict(rec) if isinstance(rec, dict) else None
