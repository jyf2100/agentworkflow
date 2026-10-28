"""mm_intake.py 单测——成员三态授权 / 缓存 / evidence 判据 / 确定性 wrap / deliver_key
（add-mm-channel-intake 3.x，spec: hub-mm-intake）。全离线（FakeMMClient）。"""
from datetime import datetime, timezone
from pathlib import Path

import mm_intake
import notify
from mm_intake import (CHANNEL_MISSING, MEMBER, NOT_MEMBER, UNKNOWN,
                       MemberCache, check_membership, deliver_key_for,
                       extract_evidence, has_evidence, membership_verdict,
                       wrap_mm_prd)

_U = "a1b2c3d4e5f6a1b2c3d4e5f6a1"  # 26-hex（user_id）
_C = "c1b2c3d4e5f6a1b2c3d4e5f6a1"  # 26 字符频道 id


def _mm(members=None, **kw):
    return notify.FakeMMClient(channel_members={_C: members if members is not None else [_U]}, **kw)


# ── 三态语义（D3：UNKNOWN ≠ 授权结论）──────────────────────────────

def test_verdict_member_and_not_member():
    assert membership_verdict(_mm([_U]), _C, _U) == MEMBER
    assert membership_verdict(_mm(["b" * 26]), _C, _U) == NOT_MEMBER


def test_verdict_none_client_is_unknown_not_bypass():
    """bot 通道未配置 = 判据不可得 → UNKNOWN（fail-closed，绝不旁路放行）。"""
    assert membership_verdict(None, _C, _U) == UNKNOWN


def test_verdict_forbidden_403_is_unknown_not_not_member():
    """403 bot 无权限 = hub 侧通道故障——绝不解读为发起人未授权。"""
    assert membership_verdict(_mm(members_forbidden={_C}), _C, _U) == UNKNOWN


def test_verdict_crash_and_not_found():
    assert membership_verdict(_mm(crash_on_members=RuntimeError("net")), _C, _U) == UNKNOWN
    assert membership_verdict(_mm(members_not_found={_C}), _C, _U) == CHANNEL_MISSING


# ── 缓存（3.2：仅 FOUND 可缓存，TTL 60s 进程内存）─────────────────

class _FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_cache_found_cached_within_ttl():
    clk = _FakeClock()
    cache = MemberCache(clock=clk)
    mm = _mm([_U])
    assert check_membership(mm, _C, _U, cache) == MEMBER
    assert mm.member_queries == [_C]  # 首查打 MM
    mm.channel_members[_C] = []  # 踢出（MM 侧已变）
    assert check_membership(mm, _C, _U, cache) == MEMBER  # TTL 内仍 FOUND（缓存）
    assert mm.member_queries == [_C]


def test_cache_expires_after_ttl():
    clk = _FakeClock()
    cache = MemberCache(clock=clk)
    mm = _mm([_U])
    check_membership(mm, _C, _U, cache)
    clk.t += mm_intake.MEMBER_CACHE_TTL_S  # 过期边界
    mm.channel_members[_C] = []
    assert check_membership(mm, _C, _U, cache) == NOT_MEMBER  # 重查判据（踢人 60s 内生效）
    assert mm.member_queries == [_C, _C]


def test_cache_never_caches_unknown():
    clk = _FakeClock()
    cache = MemberCache(clock=clk)
    mm = _mm(crash_on_members=RuntimeError("net"))
    assert check_membership(mm, _C, _U, cache) == UNKNOWN
    mm.crash_on_members = None  # MM 恢复
    mm.channel_members[_C] = [_U]
    assert check_membership(mm, _C, _U, cache) == MEMBER  # 未缓存——恢复即放行
    assert mm.member_queries == [_C, _C]


def test_cache_not_member_also_cached_found_verdict():
    clk = _FakeClock()
    cache = MemberCache(clock=clk)
    mm = _mm(["b" * 26])
    assert check_membership(mm, _C, _U, cache) == NOT_MEMBER
    mm.channel_members[_C] = [_U]  # 拉人（MM 侧已变）
    assert check_membership(mm, _C, _U, cache) == NOT_MEMBER  # TTL 内维持（60s 边界可接受）
    assert mm.member_queries == [_C]


# ── evidence 判据（全/半角冒号——中文输入法高频全角）───────────────

def test_evidence_halfwidth_and_fullwidth_colon():
    assert has_evidence("修 bug 出处: https://x/1")
    assert has_evidence("修 bug 出处：https://x/1")  # 全角冒号必须覆盖
    assert has_evidence("出处 : https://x/1")  # 冒号前空白容忍


def test_evidence_missing_or_empty_rejected():
    assert not has_evidence("修 bug 无出处标记")
    assert not has_evidence("出处:")
    assert not has_evidence("出处：")  # 标记在但行内值空 = 空依据闸前拒
    assert not has_evidence("出处:   ")


def test_extract_evidence_line_value():
    assert extract_evidence("修 bug 出处: https://x/1\n下一行") == "https://x/1"
    assert extract_evidence("出处：  群里讨论 2026-10-28  ") == "群里讨论 2026-10-28"


# ── 确定性 wrap（D5 幂等前提：零时间戳零随机）──────────────────────

def test_wrap_deterministic_byte_identical():
    a = wrap_mm_prd("proj-a", "需求正文 出处: x", _U, "roc", _C, "x")
    b = wrap_mm_prd("proj-a", "需求正文 出处: x", _U, "roc", _C, "x")
    assert a == b  # 同输入 byte-identical——deliver_key 幂等的前提
    assert "mm-channel" in a and f'"{_U}"' in a and f'"{_C}"' in a
    assert "uuid" not in a


def test_wrap_frontmatter_shape():
    w = wrap_mm_prd("proj-a", "正文", _U, "roc", _C, "https://x/1")
    assert w.startswith('---\nproject: "proj-a"\n')
    assert "\nsource: mm-channel\n" in w
    assert w.endswith("---\n\n正文")


def test_deliver_key_shape_and_hour_window():
    w = wrap_mm_prd("proj-a", "b", _U, "roc", _C, "e")
    t0 = datetime(2026, 10, 28, 9, 59, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 28, 10, 1, tzinfo=timezone.utc)
    k0 = deliver_key_for("proj-a", _U, w, t0)
    assert k0.startswith(f"mm:proj-a:{_U}:") and k0.endswith(":2026102809")
    assert deliver_key_for("proj-a", _U, w, t1) != k0  # 跨小时窗 = 新键（有意重发不拦）


# ── readable_projects（与 _panel_projects 两皮一芯）────────────────

def test_readable_projects_scans_readiness_surface(tmp_path: Path):
    root = tmp_path / "data"
    good = root / "proj-a"; (good / "profiles").mkdir(parents=True)
    (good / "sources.yaml").write_text("sources: []\n", encoding="utf-8")
    (root / "proj-b").mkdir()  # 缺必读面
    (root / "../evil").mkdir(exist_ok=True)  # 不合法名（不进扫描）
    assert mm_intake.readable_projects(root) == ["proj-a"]
    assert mm_intake.readable_projects(tmp_path / "nope") == []
