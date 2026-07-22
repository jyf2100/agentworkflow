"""artifact_store.py — SHA-256 内容寻址工件存储（OpenSpec add-durable-loop-runtime task 2.4 + 2.5）。

第二阶段把 diff/测试输出/验证器反馈/recovery snapshot 从「散落在日志/临时文件/被追加 PRD」收敛为
**内容寻址工件存储**（design 决策#5「工件 + 敏感数据分层」）：

    * 内容算 SHA-256 digest 作为地址 → 同内容同地址（天然去重 + 跨迭代复用）；
    * ``ArtifactRef``（digest/size/kind/path/sensitivity）只作指针进 journal，真源是 digest；
    * 敏感数据分层脱敏：``sanitized`` 存储前抹密钥（再算 digest），可安全外发；``public``/``internal`` 原样。

    task 2.4（存储）—— ``store`` 算 digest、按 digest 分桶落盘、返回 ``ArtifactRef``；``load`` 按 ref 读回。
    task 2.5（完整性 + 允许列表 + 脱敏 + fail-closed）——
        * ``load`` 读回重算 digest 比对 ref（防 path 指向被篡改内容，fail-closed ``ArtifactIntegrityError``）；
        * ``kind``/``sensitivity`` 必须在枚举允许列表（防非法 ref 注入 / 越权分类）；
        * ``redact_secrets`` 抹 GitHub PAT/Bearer/token=/basic-auth（与 ``external_state.sanitize`` 同源规则）。

模块依赖 ``loop_state`` 数据模型 + 标准库（hashlib/re/pathlib），不触 SDK——cron 隔离不变。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from loop_state import ArtifactRef, ArtifactKind, Sensitivity


# metadata 允许列表（task 2.5 边界校验）：kind/sensitivity 必须命中，否则拒绝存储。
ALLOWED_KINDS: frozenset[str] = frozenset(k.value for k in ArtifactKind)
ALLOWED_SENSITIVITIES: frozenset[str] = frozenset(s.value for s in Sensitivity)


# ── 敏感数据脱敏（与 external_state.sanitize 同源规则，独立定义以不耦合已归档模块）──
# GitHub PAT(ghp_/gho_/ghu_/ghs_/ghr_) / Bearer / token=... / basic-auth URL(user:pass@)
_SECRET_RE = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{16,}"
    r"|Bearer\s+[A-Za-z0-9._\-]+"
    r"|token[=:]\s*[A-Za-z0-9._\-]+"
    r"|https?://[A-Za-z0-9._\-]+:[A-Za-z0-9._\-]+@)"
)
_REDACTED = "***"


def redact_secrets(text: str) -> str:
    """抹 token/密钥/Bearer/basic-auth（artifact 落盘前消毒，``sensitivity=sanitized`` 时调用）。

    与 ``external_state.sanitize`` 的区别：本函数**不截断、不压换行**——artifact 要保留全量供审计/重放，
    只抹密钥类敏感串。None/非 str → 原样返回。
    """
    if not isinstance(text, str):
        return text
    return _SECRET_RE.sub(_REDACTED, text)


def compute_digest(content: bytes) -> str:
    """算 SHA-256 digest，返回 ``sha256:<hex>``（``ArtifactRef.digest`` 契约格式）。"""
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _bucketed_path(digest: str) -> str:
    """digest → 分桶相对路径 ``sha256/<前2字符>/<余下hex>``（防单目录文件爆炸）。"""
    hexpart = digest.split(":", 1)[1]
    return f"sha256/{hexpart[:2]}/{hexpart[2:]}"


class ArtifactIntegrityError(Exception):
    """工件完整性校验失败（digest 不匹配）——fail-closed：绝不返回可疑内容。

    场景：load 读回的内容重算 digest 与 ``ArtifactRef.digest`` 不符（磁盘错/手动篡改/path 错指）。
    """


def store(root: str | Path, content: str | bytes,
          kind: str, sensitivity: str) -> ArtifactRef:
    """存内容到内容寻址工件存储，返回 ``ArtifactRef``。

    流程：
        1. 校验 ``kind``/``sensitivity`` 在允许列表（fail-closed 边界）；
        2. ``sensitivity=sanitized`` → 先 ``redact_secrets``（消毒后再算 digest/落盘）；
        3. 算 digest → 分桶路径；若已存在则复用（内容寻址去重），否则落盘；
        4. 返回 ``ArtifactRef``（digest/size 为消毒后实际存储值，path 相对 root）。

    Args:
        root: 工件存储根目录（per-run，通常 ``<state>/artifacts/<run_id>``）。
        content: str（UTF-8 编码）或 bytes。
        kind: ``ArtifactKind`` 值（diff/test_output/verifier_feedback/recovery_snapshot/transcript）。
        sensitivity: ``Sensitivity`` 值（public/sanitized/internal）。
    """
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"非法 artifact kind（不在允许列表）: {kind!r}")
    if sensitivity not in ALLOWED_SENSITIVITIES:
        raise ValueError(f"非法 artifact sensitivity（不在允许列表）: {sensitivity!r}")

    # str → bytes
    if isinstance(content, str):
        content_bytes = content.encode("utf-8")
    else:
        content_bytes = content

    # 敏感分层：sanitized 存储前脱敏（digest 绑定消毒后内容）
    if sensitivity == Sensitivity.SANITIZED.value:
        content_bytes = redact_secrets(content_bytes.decode("utf-8", errors="replace")).encode("utf-8")

    digest = compute_digest(content_bytes)
    rel_path = _bucketed_path(digest)
    abs_path = Path(root) / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    if not abs_path.exists():   # 内容寻址去重：同 digest 已存在则不重写
        abs_path.write_bytes(content_bytes)

    return ArtifactRef(
        digest=digest,
        size=len(content_bytes),
        kind=kind,
        path=rel_path,
        sensitivity=sensitivity,
    )


def load(root: str | Path, ref: ArtifactRef) -> bytes:
    """按 ``ArtifactRef`` 读回内容，并 **重算 digest 校验完整性**（fail-closed）。

    digest 不匹配 → raise ``ArtifactIntegrityError``，绝不返回可疑内容。
    """
    abs_path = Path(root) / ref.path
    content = abs_path.read_bytes()
    verify_digest_or_raise(content, ref)
    return content


def verify_digest(content: str | bytes, ref: ArtifactRef) -> tuple[bool, str]:
    """校验内容 digest 是否匹配 ``ref.digest``。

    Returns:
        ``(True, "ok")`` 匹配；``(False, reason)`` 不匹配（元数据撒谎/内容被篡改可被识破）。
    """
    content_bytes = content.encode("utf-8") if isinstance(content, str) else content
    actual = compute_digest(content_bytes)
    if actual == ref.digest:
        return True, "ok"
    return False, f"digest mismatch: expected {ref.digest}, got {actual}"


def verify_digest_or_raise(content: str | bytes, ref: ArtifactRef) -> None:
    """``verify_digest`` 的 fail-closed 版：不匹配即 raise ``ArtifactIntegrityError``。"""
    ok, reason = verify_digest(content, ref)
    if not ok:
        raise ArtifactIntegrityError(reason)
