"""hub F10 署名 meta——卷上署名 + 写面钉死 + run 名预分配（add-hub-skeleton 任务 4.1-4.2）。

spec: execution-hub「F10 署名 meta」。journal 与 state 归容器内 pa 写，hub 在卷上的
写面钉死为仅 meta 文件（文件名模式硬校验，越界一律拒绝）。run 名 = <stamp>_<slug>，
stamp 预分配对齐 inject 段实际撞名判据：state 目录存在 prd_manifest_<stamp>.json
（全局 stamp 命名空间）才 bump，后缀形态 m/m2/m3… 同 run_daily `_bump_stamp_suffix`——
由此 meta 文件名、--stamp、实际 run 目录三者天然对齐（本体论审核 A1/A2/A2b）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

# stamp 实际形态：8 位日期（YYYYMMDD）+ 可选 mN 避碰后缀（A1 契约事实）
STAMP_RE = r"\d{8}(m\d*)?"
SLUG_RE = r"[a-z0-9-]+"
META_NAME_RE = rf"{STAMP_RE}_{SLUG_RE}\.meta\.json"
_MANIFEST_FOR = "prd_manifest_{}.json"


class MetaSignError(Exception):
    """署名/预分配非法输入——拒绝而非猜测（写面钉死：fail 不静默）。"""


def allocate_fresh_stamp(state_dir: Path, base_stamp: str) -> str:
    """基于 PRD stamp 预分配 fresh run 名：manifest 撞名则自增 m 后缀直到 fresh。

    判据对齐 inject 段（传入 fresh stamp 则沿用，durable-loop-state delta）：
    `state/prd_manifest_<stamp>.json` 文件存在（全局命名空间，跨 project）。
    """
    if not re.fullmatch(r"\d{8}", base_stamp):
        raise MetaSignError(f"base_stamp 须为 8 位日期（YYYYMMDD）：{base_stamp!r}")
    stamp = base_stamp
    n = 0
    while (state_dir / _MANIFEST_FOR.format(stamp)).exists():
        n += 1
        stamp = f"{base_stamp}m" if n == 1 else f"{base_stamp}m{n}"
    return stamp


def sign_meta(
    run_dir: Path,
    stamp: str,
    slug: str,
    *,
    initiator: str,
    queue_item_id: int,
    image_tag: str,
    now: datetime | None = None,
) -> Path:
    """在 run 目录写入署名 meta（起容器前署名——spec scenario）。

    本模块是 hub 在数据卷上的唯一写面：只写 `<stamp>_<slug>.meta.json`，
    stamp/slug 各自硬校验 + 文件名整体复检 + 写入目标必须落在 run_dir 内。
    """
    if not re.fullmatch(STAMP_RE, stamp):
        raise MetaSignError(f"stamp 非法（须 YYYYMMDD 可选 mN 后缀）：{stamp!r}")
    if not re.fullmatch(SLUG_RE, slug):
        raise MetaSignError(f"slug 非法（须 [a-z0-9-]+）：{slug!r}")
    name = f"{stamp}_{slug}.meta.json"
    if not re.fullmatch(META_NAME_RE, name):
        raise MetaSignError(f"meta 文件名不符合钉死模式：{name!r}")

    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / name
    if target.resolve().parent != run_dir.resolve():
        raise MetaSignError("meta 目标越界 run 目录——拒绝")
    if target.exists():
        return target  # 首写保真（本体论审核 B2）：已署名的 meta 不重写——written_at 是首派署名时刻，重试/重派不刷新

    written_at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    payload = {
        "initiator": initiator,          # F10：run 发起人（单用户形态 = HUB_USER_ID）
        "queue_item_id": queue_item_id,  # DB 可重建索引的回链
        "image_tag": image_tag,          # F13：入队固化 tag（非 latest）
        "written_at": written_at,
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target
