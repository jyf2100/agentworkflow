"""hub 镜像陈旧检测——remote release tag vs 配置默认（add-hub-slice-4a，spec: hub-notifications「镜像陈旧告警」）。

三态分流（对齐 fail-safe-dispatch 三态不变式；slice-3 `_CONFIG_4XX` 同型教训——
确定性配置错绝不归 UNKNOWN 静默）：
- (tag, None) = FOUND：参与比对，不一致 → 经通知通道私信配置单用户 + stderr 留痕
- (None, "config_4xx") = 404 确定性配置错：stderr 高声告警（去抖一次），不进比对
- (None, None) = UNKNOWN（网络错/5xx/超时）：不告警不误报，记日志下轮再查

去抖：进程生命周期内同一差异组合只告警一次（内存 set，重启重告可接受——spec 已定）。
比对 MUST NOT 触发自动拉取或重建镜像（告警是唯一动作，升级是人工决定）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Callable

# 去抖键：gh CLI 缺失 warn 一次（检测关闭），与差异组合共用同一 seen 集合
_NO_GH_KEY = "__no_gh__"


def gh_client(token: str | None = None) -> Callable[[str], tuple[str | None, str | None]]:
    """构造真 gh 客户端闭包——`gh api repos/{repo}/releases/latest --jq .tag_name`。

    token 经子进程 env GH_TOKEN 注入（gh 原生读此 env；凭据绝不进命令行参数——
    ps 可见面）。超时/网络/5xx 全归 UNKNOWN（瞬时，不构成告警依据）。
    """

    def _call(repo: str) -> tuple[str | None, str | None]:
        env = {**os.environ, **({"GH_TOKEN": token} if token else {})}
        try:
            r = subprocess.run(
                ["gh", "api", f"repos/{repo}/releases/latest", "--jq", ".tag_name"],
                capture_output=True, text=True, timeout=15, env=env,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None, None  # 瞬时/环境 → UNKNOWN
        if r.returncode == 0:
            tag = r.stdout.strip()
            return (tag, None) if tag else (None, None)  # 空 tag 视为异常归 UNKNOWN
        if "HTTP 404" in r.stderr:
            return None, "config_4xx"  # 确定性配置错（repo 配错或无 release；gh 输出形态 "gh: ... (HTTP 404)"）
        return None, None  # 网络错/5xx/限流 → UNKNOWN

    return _call


def latest_release_tag(
    repo: str,
    client: Callable[[str], tuple[str | None, str | None]],
) -> tuple[str | None, str | None]:
    """查 repo 最新 release tag——三态（(tag,None)=FOUND / (None,"config_4xx") / (None,None)=UNKNOWN）。

    client 必传（L5：默认值不可用的签名是撒谎——check_stale 内已做 which 检查
    后用 gh_client 构造；测试注入 FakeRemote 替身，D7 同 FakeMMClient 哲学）。
    """
    return client(repo)


def check_stale(cfg, seen: set, client=None, notifier=None) -> None:
    """单次陈旧检测（daemon 每 N tick 调一次）。

    cfg.image_template_repo 未配置 = 检测关闭零行为。seen = 去抖集合（调用方持有，
    进程生命周期）；notifier = 通知通道（None = 差异告警仅 stderr，不发私信）。
    异常由调用方兜（daemon 续跑）；本函数自身分支均正常返回。
    """
    repo = cfg.image_template_repo
    if not repo:
        return
    if client is None:
        if shutil.which("gh") is None:
            if _NO_GH_KEY not in seen:
                seen.add(_NO_GH_KEY)
                print("[stale][warn] gh CLI 缺失——镜像陈旧检测关闭（装 gh 或检查 PATH）", file=sys.stderr)
            return
        client = gh_client(cfg.gh_token)

    tag, err = latest_release_tag(repo, client=client)
    if err == "config_4xx":
        if (repo, "config_4xx") not in seen:
            seen.add((repo, "config_4xx"))
            print(
                f"[stale][ERROR] 镜像比对源仓库 404：{repo}（repo 配错或无 release）"
                f"——请检查 HUB_IMAGE_TEMPLATE_REPO（此错不修，陈旧检测整体失效）",
                file=sys.stderr,
            )
        return
    if tag is None:  # UNKNOWN：瞬时失败不告警不误报
        print(f"[stale] 查询 {repo} 失败（瞬时/网络），下轮再查", file=sys.stderr)
        return
    if tag == cfg.image_tag_default:
        return  # 一致：无事
    combo = (repo, tag, cfg.image_tag_default)
    if combo in seen:
        return  # 同差异组合去抖（进程生命周期一次）
    seen.add(combo)
    text = (
        f"[pa-hub] 镜像陈旧：remote 最新 {tag} ≠ 本地默认 {cfg.image_tag_default}"
        f"（{repo}）——请评估升级并重建镜像（人工决定，hub 不自动拉取）"
    )
    print(f"[stale][warn] {text}", file=sys.stderr)
    if notifier is not None:
        notifier.notify_admin(text)  # 内部 fail-open，不抛
