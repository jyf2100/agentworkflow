#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_artifact_store.py — SHA-256 内容寻址工件存储单测（OpenSpec add-durable-loop-runtime task 2.4 + 2.5）。

第二阶段把 diff/测试输出/验证器反馈/recovery snapshot 从「散落在日志/临时文件」收敛为 **内容寻址工件存储**
（design 决策#5「工件 + 敏感数据分层」）：内容算 SHA-256 digest 作为地址，``ArtifactRef`` 只存指针进 journal。
同内容 → 同 digest → 同地址（天然去重 + 内容完整性可校验）。

    task 2.4（存储）—— ``store`` 算 digest、按 digest 分桶落盘、返回 ``ArtifactRef``；``load`` 按 ref 读回。
    task 2.5（完整性 + 分层 + 脱敏 + fail-closed）——
        * **digest 校验**：``load`` 读回后重算 digest 比对 ref——防 path 指向被篡改内容（fail-closed）；
        * **metadata 允许列表**：``kind``/``sensitivity`` 必须在枚举集合——防构造非法 ref 注入；
        * **敏感数据分层脱敏**：``sanitized`` 内容存储前抹 token/密钥（再算 digest）；``public``/``internal`` 原样；
        * **完整性失败测试**：篡改文件后 ``load`` raise ``ArtifactIntegrityError``，绝不返回可疑内容。

IO 用 ``tmp_path`` 隔离。模块依赖 ``loop_state`` 数据模型 + 标准库（hashlib/re/pathlib），不触 SDK。

跑：python3 -m pytest scripts/test_artifact_store.py -q
AAA 结构（Arrange / Act / Assert）。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import artifact_store as A  # noqa: E402
from loop_state import ArtifactRef  # noqa: E402


# ════════════════════════════════════════════════════════════════════════
# task 2.4：内容寻址存储（digest / 去重 / load 往返）
# ════════════════════════════════════════════════════════════════════════
def test_store_returns_content_addressed_ref(tmp_path):
    """store 返回的 ``ArtifactRef.digest`` = ``sha256:<内容十六进制>``——内容即地址（design 决策#5）。

    digest 是真源：``ArtifactRef`` 的 path/size 只是便利指针，可被校验推翻。"""
    # Arrange
    content = b"diff --git a/foo b/foo\n+added\n"
    # Act
    ref = A.store(tmp_path, content, kind="diff", sensitivity="public")
    # Assert
    expected = "sha256:" + hashlib.sha256(content).hexdigest()
    assert ref.digest == expected
    assert ref.size == len(content)
    assert ref.kind == "diff"
    assert ref.sensitivity == "public"
    assert ref.path.startswith("sha256/")


def test_store_content_addressing_dedups_identical_content(tmp_path):
    """同内容两次 store → 同 digest/path（内容寻址天然去重，不重复落盘）。

    多次迭代产生相同 diff/test 输出时，工件存储不膨胀——digest 相同即复用。"""
    # Arrange
    content = b"identical payload"
    # Act
    ref1 = A.store(tmp_path, content, kind="test_output", sensitivity="internal")
    ref2 = A.store(tmp_path, content, kind="test_output", sensitivity="internal")
    # Assert
    assert ref1.digest == ref2.digest
    assert ref1.path == ref2.path


def test_load_returns_stored_content(tmp_path):
    """load 按 ref 读回原始字节（public/internal 原样，不经脱敏）。"""
    content = "PASSED 3 tests\n"
    ref = A.store(tmp_path, content, kind="test_output", sensitivity="public")
    got = A.load(tmp_path, ref)
    assert got == content.encode("utf-8")


def test_store_accepts_str_and_bytes(tmp_path):
    """store 接受 str（UTF-8 编码）或 bytes——dispatch 侧产出多为 str。"""
    ref_s = A.store(tmp_path, "hello", kind="diff", sensitivity="public")
    ref_b = A.store(tmp_path / "other", b"hello", kind="diff", sensitivity="public")
    assert ref_s.digest == ref_b.digest   # 同内容同 digest


def test_store_uses_bucketed_path(tmp_path):
    """digest 分桶（``sha256/<前2字符>/<余下>``）——防单目录文件爆炸（成千上万工件场景）。"""
    ref = A.store(tmp_path, b"x", kind="diff", sensitivity="public")
    h = ref.digest.split(":", 1)[1]
    assert ref.path == f"sha256/{h[:2]}/{h[2:]}"


# ════════════════════════════════════════════════════════════════════════
# task 2.5：metadata 允许列表（防非法 ref 注入）
# ════════════════════════════════════════════════════════════════════════
def test_store_rejects_unknown_kind(tmp_path):
    """kind 必须在 ``ArtifactKind`` 枚举——防构造非法 kind 写入存储/混入 telemetry 路由。

    metadata 允许列表是 fail-closed 边界校验（coding-style「输入校验」）。"""
    with pytest.raises(ValueError):
        A.store(tmp_path, b"x", kind="malicious_kind", sensitivity="public")


def test_store_rejects_unknown_sensitivity(tmp_path):
    """sensitivity 必须在 ``Sensitivity`` 枚举——防越权分类（如把 internal 伪装 public 外发）。"""
    with pytest.raises(ValueError):
        A.store(tmp_path, b"x", kind="diff", sensitivity="top_secret")


# ════════════════════════════════════════════════════════════════════════
# task 2.5：敏感数据分层脱敏（sanitized 抹密钥；public/internal 原样）
# ════════════════════════════════════════════════════════════════════════
def test_redact_secrets_strips_tokens():
    """``redact_secrets`` 抹 GitHub PAT/Bearer/token=/basic-auth URL——落盘前消毒（design 决策#5）。"""
    text = "ghp_abcd1234efgh5678ijkl9012mnop bearer Bearer xyz123 token=sekret https://u:p@host"
    redacted = A.redact_secrets(text)
    assert "ghp_" not in redacted
    assert "Bearer xyz123" not in redacted
    assert "sekret" not in redacted
    assert "u:p@host" not in redacted


def test_store_sanitized_redacts_before_hashing(tmp_path):
    """sensitivity=sanitized → 内容**先脱敏再算 digest/落盘**（digest 反映脱敏后内容，load 回的也无密钥）。

    这是「敏感数据分层」的核心：sanitized 工件可安全外发（已消毒），digest 绑定消毒后内容。"""
    # Arrange
    content = "ran tests\ntoken=ghp_supersecretvalue1234\nok"
    # Act
    ref = A.store(tmp_path, content, kind="test_output", sensitivity="sanitized")
    got = A.load(tmp_path, ref).decode("utf-8")
    # Assert：落盘内容已脱敏
    assert "ghp_supersecretvalue1234" not in got
    assert "ran tests" in got and "ok" in got
    # digest 是脱敏后内容的 digest（非原文）
    from artifact_store import compute_digest, redact_secrets
    assert ref.digest == compute_digest(redact_secrets(content).encode("utf-8"))


def test_store_public_not_redacted(tmp_path):
    """public 工件原样存（如 diff 摘要——本就无密钥，脱敏无意义且会改内容）。"""
    content = "clean diff no secrets"
    ref = A.store(tmp_path, content, kind="diff", sensitivity="public")
    assert A.load(tmp_path, ref).decode("utf-8") == content


def test_store_internal_not_redacted(tmp_path):
    """internal 工件原样存（recovery snapshot/transcript——仅内部，不外发，无需脱敏）。"""
    content = "internal snapshot with paths /home/user/x"
    ref = A.store(tmp_path, content, kind="recovery_snapshot", sensitivity="internal")
    assert A.load(tmp_path, ref).decode("utf-8") == content


# ════════════════════════════════════════════════════════════════════════
# task 2.5：digest 完整性校验 + fail-closed（防篡改）
# ════════════════════════════════════════════════════════════════════════
def test_verify_digest_passes_on_integrity(tmp_path):
    """verify_digest 对未篡改内容返回 True。"""
    content = b"abc"
    ref = A.store(tmp_path, content, kind="diff", sensitivity="public")
    ok, _ = A.verify_digest(content, ref)
    assert ok is True


def test_verify_digest_fails_on_tampered_content():
    """verify_digest 对被篡改内容（digest 不匹配）返回 False——元数据撒谎可被机械识破。"""
    ref = ArtifactRef(digest="sha256:deadbeef", size=3, kind="diff",
                      path="sha256/de/adbeef", sensitivity="public")
    ok, reason = A.verify_digest(b"tampered", ref)
    assert ok is False
    assert "mismatch" in reason.lower() or "digest" in reason.lower()


def test_load_raises_on_integrity_failure(tmp_path):
    """**fail-closed**：文件被篡改后 ``load`` 重算 digest 不匹配 → raise ``ArtifactIntegrityError``，
    绝不返回可疑内容给 reducer/dispatch（design 决策#5 完整性）。"""
    # Arrange
    content = b"original evidence"
    ref = A.store(tmp_path, content, kind="test_output", sensitivity="public")
    # 篡改落盘内容（模拟磁盘错/手动改）
    (tmp_path / ref.path).write_bytes(b"TAMPERED evidence")
    # Act / Assert
    with pytest.raises(A.ArtifactIntegrityError):
        A.load(tmp_path, ref)


def test_compute_digest_stable_format():
    """``compute_digest`` 输出 ``sha256:<hex>``——格式稳定（ArtifactRef.digest 契约）。"""
    d = A.compute_digest(b"")
    assert d.startswith("sha256:")
    assert len(d) == len("sha256:") + 64   # SHA-256 = 64 hex chars


# ════════════════════════════════════════════════════════════════════════
# add-cross-prd-learning-memory task 1.2：reflection 工件类别（终态反思全量输出 sanitized）
# ════════════════════════════════════════════════════════════════════════
def test_reflection_kind_in_allowed_kinds():
    """task 1.2：``reflection`` 在 ArtifactKind 枚举 + ALLOWED_KINDS 派生集合（store 接受）。

    终态反思全量输出（sanitized）落内容寻址工件存储——design 决策#3「Full reflection output is
    stored as a new content-addressed ``reflection`` artifact」。ALLOWED_KINDS 由 ArtifactKind 派生，
    加枚举值后自动生效，artifact_store.py 本身不改。"""
    from loop_state import ArtifactKind
    assert "reflection" in {k.value for k in ArtifactKind}
    assert "reflection" in A.ALLOWED_KINDS


def test_store_reflection_kind_round_trip(tmp_path):
    """task 1.2：reflection 工件可存 + load 往返 + digest 校验通过（同其他 kind 契约）。

    reflection 是终态反思的全量 sanitized 输出（含 pattern_description、evidence refs、schema 字段），
    走与其他 kind 同样的内容寻址存储 + digest 校验链路。"""
    # Arrange — reflection 通常是结构化 JSON sanitized 后的文本
    content = '{"phase":"verify","failure_class":"gate_blocked","corrective_action":"add test"}'
    # Act
    ref = A.store(tmp_path, content, kind="reflection", sensitivity="sanitized")
    got = A.load(tmp_path, ref)
    # Assert — 往返一致，digest 校验通过（load 内 verify）
    assert ref.kind == "reflection"
    assert ref.sensitivity == "sanitized"
    assert got == content.encode("utf-8")
    ok, _ = A.verify_digest(content, ref)
    assert ok is True


def test_store_reflection_sanitized_redacts_secrets(tmp_path):
    """task 1.2：reflection sensitivity=sanitized 抹密钥后再算 digest（design 决策#5 + #3）。

    反思输出可能引用 token/path 等敏感串——sanitized 存储前消毒，digest 绑定消毒后内容，落盘内容无密钥。"""
    # Arrange
    content = "reflection output\ntoken=ghp_supersecretvalue1234\nmore"
    # Act
    ref = A.store(tmp_path, content, kind="reflection", sensitivity="sanitized")
    got = A.load(tmp_path, ref).decode("utf-8")
    # Assert
    assert "ghp_supersecretvalue1234" not in got
    assert "reflection output" in got and "more" in got


def test_load_reflection_raises_on_integrity_failure(tmp_path):
    """task 1.2：reflection 工件篡改后 load fail-closed（同 test_load_raises_on_integrity_failure）。

    reflection 是 lesson candidate 的 evidence 来源——内容被篡改 → 校验失败 → 绝不进 catalog（design「corrupt
    or unverified memory MUST fail closed」）。"""
    # Arrange
    content = b'{"reflection":"original"}'
    ref = A.store(tmp_path, content, kind="reflection", sensitivity="sanitized")
    # 篡改
    (tmp_path / ref.path).write_bytes(b'{"reflection":"TAMPERED"}')
    # Act / Assert
    with pytest.raises(A.ArtifactIntegrityError):
        A.load(tmp_path, ref)
