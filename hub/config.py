"""hub 配置加载——切片 1 单用户 env 直注形态。

add-hub-skeleton D6：env 读取全部集中本文件，是切片 2 凭据保险箱的唯一替换点
（换值来源，schema 不动）。凭据绝不进错误消息、日志或 DB（spec: execution-hub
「单用户 env 直注 bootstrap」）。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import auth as auth_mod
import vault_store

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
PANEL_ADMINS_ENV = "HUB_PANEL_ADMINS"  # 面板改行为操作白名单（MM username 逗号分隔；空=任何有效会话，add-hub-panel-console D4）

# 可选投递通道 token（add-hub-slice-3 D7）：不配 = 内网无守卫；配置则 POST /deliver
# 要求 Authorization: Bearer（hmac.compare_digest 线性比较防时序）。
DELIVER_TOKEN_ENV = "HUB_DELIVER_TOKEN"

# 可选通知面（add-hub-slice-4a，spec: hub-notifications）：bot token 不配（或 MM base_url
# 缺）= 通道关闭零行为；频道映射 "proj:chan-id,proj2:chan-id2"；陈旧检测源 "owner/repo"
# 不配 = 检测关闭；检测周期 N tick 可配。
MM_BOT_TOKEN_ENV = "HUB_MM_BOT_TOKEN"
MM_PROJECT_CHANNELS_ENV = "HUB_MM_PROJECT_CHANNELS"
IMAGE_TEMPLATE_REPO_ENV = "HUB_IMAGE_TEMPLATE_REPO"
STALE_CHECK_TICKS_ENV = "HUB_STALE_CHECK_TICKS"

# 可选 MM 入站 slash command token（add-mm-channel-intake D6）：不配 = 入站关闭
# （POST /mm/intake 503，零行为）。凭据第三实体：OAuth app（登录）/ bot token（出站
# 推递）/ slash token（入站校验）三者独立分职。
MM_SLASH_TOKEN_ENV = "HUB_MM_SLASH_TOKEN"
DEFAULT_STALE_CHECK_TICKS = 720  # ≈ 5s tick 下每小时一次
# runner 池健康探针周期（add-hub-runner-pool D4，stale_check 同型 N tick 计数）
PROBE_TICKS_ENV = "HUB_PROBE_TICKS"
DEFAULT_PROBE_TICKS = 60  # ≈ 5s tick 下每 5 分钟一轮（三项各一次 CLI 子进程，别更密）

# 可选远程 runner 面（add-hub-remote-runner，ADR-0009）：两键全不配 = 本机 daemon +
# data_root 挂载，现状 byte-identical。add-hub-runner-pool 后两键降级为**首启 seed
# 一次性消费**（serve 表空才灌一行 runners，此后面板 runners 表是唯一真相，env 再改
# 静默无效）。限形校验（tcp:// 前缀）在注册/seed 入口（db._validate_runner_fields
# 单点）：表空态 env 值形态坏 = seed_runner 抛 DBError → serve 启动 fail loud。
DOCKER_HOST_ENV = "HUB_DOCKER_HOST"
RUNNER_MOUNT_ROOT_ENV = "HUB_RUNNER_MOUNT_ROOT"


def parse_project_channels(raw: str | None) -> dict[str, str]:
    """频道映射解析："proj-a:chan1,proj-b:chan2" → {proj: chan}。

    纯机械（spec: hub-notifications「频道广播」）：坏段（无冒号/空值）跳过——env 面
    配错不崩启动（通知是观察面），但 stderr warn 可见（无静默失败）。
    """
    if not raw:
        return {}
    out: dict[str, str] = {}
    for seg in raw.split(","):
        seg = seg.strip()
        if not seg:
            continue
        proj, sep, chan = seg.partition(":")
        if not sep or not proj.strip() or not chan.strip():
            print(f"[warn] HUB_MM_PROJECT_CHANNELS 坏段跳过：{seg!r}（须 proj:chan-id）", file=sys.stderr)
            continue
        out[proj.strip()] = chan.strip()
    return out


class ConfigError(Exception):
    """配置缺失/非法——消息只含键名，绝不含凭据值。"""


@dataclass(frozen=True)
class HubConfig:
    user_id: str                    # F10 meta 的 initiator（单用户过渡形态；切片 2 只换值来源）
    anthropic_base_url: str
    anthropic_auth_token: str
    gh_token: str
    user_model: str | None          # 可选模型路由（F9 补记：非凭据，与三值同 env-file 通道注入）
    image_tag_default: str          # F13：入队未显式传 tag 时的固化默认（生产显式钉版本）；值=完整镜像引用 repo:tag（docker run/inspect 直接消费；stale 比对取 tag 段）
    data_root: Path                 # 数据卷根（服务器 /data/pa 同构约定）
    max_containers: int             # 跨项目并行峰值上限（design doc §2：峰值 5-10 可配）
    max_retries: int                # 单队列项重派上限（0=一次失败即放弃，A3）
    # auth 可选面（2b D7/D8）：None = 未配置（auth 群 503，2a 形态照跑）
    mm_base_url: str | None = None
    mm_client_id: str | None = None
    mm_client_secret: str | None = None
    session_secret: bytes | None = None      # 与保险箱 master key 分离（spec「签名会话生命周期」）
    session_ttl_s: int = 7 * 86400           # D2：默认 7 天
    deliver_token: str | None = None         # slice-3 D7：投递通道共享 token（None = 内网无守卫）
    panel_admins: frozenset[str] = frozenset()  # 空 = 面板改行为操作对任何有效会话放行（单用户回退态）
    # 通知面（4a，spec: hub-notifications）：None/空 = 对应能力关闭零行为
    mm_bot_token: str | None = None          # 独立 bot 凭据实体（F14，与 OAuth app 分离）
    project_channels: dict[str, str] = field(default_factory=dict)  # 仅 seed 迁移消费（add-mm-channel-intake D2：启动 seed 进 project_channels 表后此 env 键废止；通知路由读绑定表）
    mm_slash_token: str | None = None        # 入站 slash command 校验 token（None = 入站关闭零行为）
    image_template_repo: str | None = None   # 陈旧检测比对源 owner/repo（None = 检测关闭）
    stale_check_ticks: int = DEFAULT_STALE_CHECK_TICKS  # 检测周期（N tick 一次）
    probe_ticks: int = DEFAULT_PROBE_TICKS  # runner 池健康探针周期（N tick 一次）
    # 远程 runner 面（add-hub-remote-runner D1/D2 → add-hub-runner-pool seed 化）：
    # None = 零变化（本机 daemon / data_root 挂载）；有值仅 serve 首启 seed 消费一次
    docker_host: str | None = None          # seed 行 docker_host（限形在 seed/注册入口，不在本层）
    runner_mount_root: Path | None = None   # seed 行挂载根（daemon 侧视角；此后表是唯一真相）

    def user_env(self) -> dict[str, str]:
        """容器 env-file 内容：三值 + 可选模型路由（与切片 2 保险箱同通道）。"""
        env = {
            "ANTHROPIC_BASE_URL": self.anthropic_base_url,
            "ANTHROPIC_AUTH_TOKEN": self.anthropic_auth_token,
            "GH_TOKEN": self.gh_token,
        }
        if self.user_model:
            env["ANTHROPIC_MODEL"] = self.user_model
        # add-forge-adapter 3.2：平台 token 透传（env 兜底路径与 vault 整 payload 注入对齐；
        # hub.env 可直接配三平台，有才带——GH_TOKEN 上面的固定三值是切片 2 既有契约不动）
        for k in vault_store.FORGE_TOKEN_KEYS.values():
            if k != "GH_TOKEN" and os.environ.get(k):
                env[k] = os.environ[k]
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

    # M1（实现本体审核）：0 会炸 daemon modulo、负数令检测静默关闭——fail fast 启动拦
    sc_raw = e.get(STALE_CHECK_TICKS_ENV)
    try:
        stale_check_ticks = int(sc_raw) if sc_raw else DEFAULT_STALE_CHECK_TICKS
    except ValueError:
        raise ConfigError(f"{STALE_CHECK_TICKS_ENV} 非整数：{sc_raw!r}") from None
    if stale_check_ticks < 1:
        raise ConfigError(f"{STALE_CHECK_TICKS_ENV} 须 ≥1：{stale_check_ticks}")

    # add-hub-runner-pool D4：探针周期同型校验（0 炸 modulo、负数静默关闭——fail fast）
    pt_raw = e.get(PROBE_TICKS_ENV)
    try:
        probe_ticks = int(pt_raw) if pt_raw else DEFAULT_PROBE_TICKS
    except ValueError:
        raise ConfigError(f"{PROBE_TICKS_ENV} 非整数：{pt_raw!r}") from None
    if probe_ticks < 1:
        raise ConfigError(f"{PROBE_TICKS_ENV} 须 ≥1：{probe_ticks}")

    # add-hub-runner-pool 6.1：docker_host 限形校验移至注册/seed 入口（db 层
    # _validate_runner_fields 单点）——本层只解析透传（seed 消费；表空态坏值 =
    # serve seed_runner 抛 DBError 启动 fail loud，行为等价原 ConfigError 拒起）。
    docker_host = e.get(DOCKER_HOST_ENV) or None
    rmr_raw = e.get(RUNNER_MOUNT_ROOT_ENV) or None

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
        deliver_token=e.get(DELIVER_TOKEN_ENV) or None,
        panel_admins=frozenset(u.strip() for u in (e.get(PANEL_ADMINS_ENV) or "").split(",") if u.strip()),
        mm_bot_token=e.get(MM_BOT_TOKEN_ENV) or None,
        project_channels=parse_project_channels(e.get(MM_PROJECT_CHANNELS_ENV)),
        mm_slash_token=e.get(MM_SLASH_TOKEN_ENV) or None,
        image_template_repo=e.get(IMAGE_TEMPLATE_REPO_ENV) or None,
        stale_check_ticks=stale_check_ticks,
        probe_ticks=probe_ticks,
        docker_host=docker_host,
        runner_mount_root=Path(rmr_raw) if rmr_raw is not None else None,
    )
