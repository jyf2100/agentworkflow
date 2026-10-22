"""hub 配置加载——切片 1 单用户 env 直注形态。

add-hub-skeleton D6：env 读取全部集中本文件，是切片 2 凭据保险箱的唯一替换点
（换值来源，schema 不动）。凭据绝不进错误消息、日志或 DB（spec: execution-hub
「单用户 env 直注 bootstrap」）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import auth as auth_mod

# 必需 env（缺失即拒绝启动——不静默降级）；凭据三值 + 镜像默认 tag + 数据卷根 + 单用户标识
REQUIRED_KEYS = (
    "HUB_USER_ID",
    "HUB_USER_ANTHROPIC_BASE_URL",
    "HUB_USER_ANTHROPIC_AUTH_TOKEN",
    "HUB_USER_GH_TOKEN",
    "HUB_IMAGE_TAG_DEFAULT",
    "HUB_DATA_ROOT",
)
DEFAULT_MAX_CONTAINERS = 3
DEFAULT_MAX_RETRIES = 2

# 可选 auth env（add-hub-slice-2b）：不配 = auth 群 503「未配置」，其余端点零影响
#（2a 形态即回退态）。四项齐（auth.mm_configured）auth 群才可用。
MM_BASE_URL_ENV = "HUB_MM_BASE_URL"
MM_CLIENT_ID_ENV = "HUB_MM_CLIENT_ID"
MM_CLIENT_SECRET_ENV = "HUB_MM_CLIENT_SECRET"
SESSION_SECRET_ENV = "HUB_SESSION_SECRET"
SESSION_TTL_ENV = "HUB_SESSION_TTL_S"


class ConfigError(Exception):
    """配置缺失/非法——消息只含键名，绝不含凭据值。"""


@dataclass(frozen=True)
class HubConfig:
    user_id: str                    # F10 meta 的 initiator（单用户过渡形态；切片 2 只换值来源）
    anthropic_base_url: str
    anthropic_auth_token: str
    gh_token: str
    user_model: str | None          # 可选模型路由（F9 补记：非凭据，与三值同 env-file 通道注入）
    image_tag_default: str          # F13：入队未显式传 tag 时的固化默认（生产显式钉版本）
    data_root: Path                 # 数据卷根（服务器 /data/pa 同构约定）
    max_containers: int             # 跨项目并行峰值上限（design doc §2：峰值 5-10 可配）
    max_retries: int                # 单队列项重派上限（0=一次失败即放弃，A3）
    # auth 可选面（2b D7/D8）：None = 未配置（auth 群 503，2a 形态照跑）
    mm_base_url: str | None = None
    mm_client_id: str | None = None
    mm_client_secret: str | None = None
    session_secret: bytes | None = None      # 与保险箱 master key 分离（spec「签名会话生命周期」）
    session_ttl_s: int = 7 * 86400           # D2：默认 7 天

    def user_env(self) -> dict[str, str]:
        """容器 env-file 内容：三值 + 可选模型路由（与切片 2 保险箱同通道）。"""
        env = {
            "ANTHROPIC_BASE_URL": self.anthropic_base_url,
            "ANTHROPIC_AUTH_TOKEN": self.anthropic_auth_token,
            "GH_TOKEN": self.gh_token,
        }
        if self.user_model:
            env["ANTHROPIC_MODEL"] = self.user_model
        return env


def load_config(env: Mapping[str, str] | None = None) -> HubConfig:
    """从 env 映射（缺省 os.environ）加载配置；必需项缺失→列出全部缺项，绝不静默。"""
    e = dict(os.environ) if env is None else dict(env)
    missing = [k for k in REQUIRED_KEYS if not e.get(k)]
    if missing:
        raise ConfigError(f"缺少必需 env（共 {len(missing)} 项）：{', '.join(missing)}")

    mc_raw = e.get("HUB_MAX_CONTAINERS") or str(DEFAULT_MAX_CONTAINERS)
    try:
        max_containers = int(mc_raw)
    except ValueError:
        raise ConfigError(f"HUB_MAX_CONTAINERS 非整数：{mc_raw!r}") from None
    if max_containers < 1:
        raise ConfigError(f"HUB_MAX_CONTAINERS 须 ≥1：{max_containers}")

    mr_raw = e.get("HUB_MAX_RETRIES") or str(DEFAULT_MAX_RETRIES)
    try:
        max_retries = int(mr_raw)
    except ValueError:
        raise ConfigError(f"HUB_MAX_RETRIES 非整数：{mr_raw!r}") from None
    if max_retries < 0:
        raise ConfigError(f"HUB_MAX_RETRIES 须 ≥0：{max_retries}")

    return HubConfig(
        user_id=e["HUB_USER_ID"],
        anthropic_base_url=e["HUB_USER_ANTHROPIC_BASE_URL"],
        anthropic_auth_token=e["HUB_USER_ANTHROPIC_AUTH_TOKEN"],
        gh_token=e["HUB_USER_GH_TOKEN"],
        user_model=e.get("HUB_USER_MODEL") or None,
        image_tag_default=e["HUB_IMAGE_TAG_DEFAULT"],
        data_root=Path(e["HUB_DATA_ROOT"]),
        max_containers=max_containers,
        max_retries=max_retries,
        mm_base_url=e.get(MM_BASE_URL_ENV) or None,
        mm_client_id=e.get(MM_CLIENT_ID_ENV) or None,
        mm_client_secret=e.get(MM_CLIENT_SECRET_ENV) or None,
        session_secret=(e.get(SESSION_SECRET_ENV) or "").encode("ascii") or None,
        session_ttl_s=int(e.get(SESSION_TTL_ENV) or auth_mod.DEFAULT_SESSION_TTL_S),
    )
