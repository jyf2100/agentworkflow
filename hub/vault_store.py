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

from cryptography.fernet import Fernet, InvalidToken

KEY_ENV = "HUB_VAULT_MASTER_KEY"
VAULT_DIRNAME = "vault"
VAULT_FILENAME = "credentials.json"

# 注入 env-file 的必需三值（F9）；ANTHROPIC_MODEL（可选模型路由）随 payload 一并存取
VAULT_REQUIRED_KEYS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "GH_TOKEN")


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
