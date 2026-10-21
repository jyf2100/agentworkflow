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
    )
