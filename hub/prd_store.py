"""hub F10 写面②类——投递 PRD 落盘（add-hub-slice-3 D6）。

写面两类枚举之②（spec: execution-hub「F10 署名 meta」MODIFIED）：① meta 署名
（meta_sign.py）；② 投递 PRD（本模块）——只写 `<卷根>/<project>/prd/` 单一拼法，
文件名硬校验，越界一律拒绝（不存在通用写函数，防写面膨胀滑坡）。容器内 pa 写
自己 state（inject 规范化 + manifest）是 pa 既有写面，不属此枚举管辖。

同名同容幂等复用不重写；同名异容（磁盘已有另一份合法投递占位）`-<sha8>` 后缀
避碰；重复投递修复（db 键命中但文件缺失/异容）overwrite=True 写回原位。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from lifecycle import PROJECT_RE

# stamp 形态与 meta_sign 同源（8 位日期 + 可选 mN 避碰后缀）；可选 -<sha8> 落盘避碰后缀
STAMP_RE = r"\d{8}(m\d*)?"
SLUG_RE = r"[a-z0-9-]+"
# mm-<sha8>（可选 -<sha8> 避碰后缀）= MM 入站确定性文件名（add-mm-channel-intake D5：
# 零时间戳零随机成分——同输入 byte-identical 是 deliver_key 幂等的前提；与面板 stamp
# 前缀形态有意偏离，勿「顺手统一」加回日期/uuid）
MM_NAME_RE = r"mm-[a-f0-9]{8}(-[a-f0-9]{8})?\.md"
PRD_NAME_RE = rf"{STAMP_RE}_{SLUG_RE}(-[a-f0-9]{{8}})?\.md|{MM_NAME_RE}"
SHA256_RE = r"[a-f0-9]{64}"
_PRD_DIR = "prd"


class PrdStoreError(Exception):
    """落盘非法输入/越界——拒绝而非猜测（写面钉死：fail 不静默）。"""


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def prd_dir(vault_root: Path, project: str) -> Path:
    """落盘目录唯一拼法（写面钉死：不提供任意路径写入口）。"""
    if not PROJECT_RE.fullmatch(project):
        raise PrdStoreError(f"project 名非法（须匹配 {PROJECT_RE.pattern}）：{project!r}")
    return vault_root / project / _PRD_DIR


def store_prd(
    vault_root: Path, project: str, filename: str, content: str, *, declared_sha: str,
    overwrite: bool = False,
) -> Path:
    """校验并落盘投递 PRD，返回实际落盘位。

    file-first（spec scenario「落盘先于入队」）的执行点：/deliver handler 先经
    本函数落盘成功，才持久化队列项。sha 校验防传输损坏 + 防幂等键撒谎。

    overwrite=False（新投递）：目标存在同容 = 幂等复用不重写；异容 = 另一份合法
    投递占位 → `-<sha8>` 后缀避碰。overwrite=True（重复投递修复：db 键已命中，
    键声明内容即真相）：无条件写回 filename 原位（缺失/异容补写）。
    """
    if not re.fullmatch(PRD_NAME_RE, filename):
        raise PrdStoreError(f"PRD 文件名不符合钉死模式：{filename!r}")
    if not re.fullmatch(SHA256_RE, declared_sha):
        raise PrdStoreError("sha256 声明值须为 64 位小写 hex")
    if sha256_text(content) != declared_sha:
        raise PrdStoreError("sha256(content) 与声明值不符——拒绝落盘")

    target_dir = prd_dir(vault_root, project)
    target = target_dir / filename
    if not overwrite:
        if target.exists():
            if target.read_text(encoding="utf-8") == content:
                return target  # 同名同容幂等复用（不重写）
            stem = filename[: -len(".md")]
            target = target_dir / f"{stem}-{declared_sha[:8]}.md"
            if not re.fullmatch(PRD_NAME_RE, target.name):
                raise PrdStoreError(f"避碰文件名越出钉死模式：{target.name!r}")
    target_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target
