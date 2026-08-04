# -*- coding: utf-8 -*-
"""test_verify_anchors.py — verify_anchors 解析器单测（harden-pa-verify-determinism task 5.1b）。

canned jest / pytest / node-test 红输出样本 → 结构化 Anchor；unparseable / unsupported runner
→ unresolved（fail-open，不伪造——同 fail-safe-dispatch UNKNOWN 姿态，design D2）。

hermetic：canned 字符串，不跑真实测试，不依赖 npm/pytest 安装。
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from verify_anchors import Anchor, detect_runner, derive_anchors, map_anchor_to_hunk, parse_failing_anchors


# ── canned 红输出样本（贴近真实 runner 格式）───────────────────────────────────
PYTEST_RED = """\
============================= test session starts ==============================
platform darwin -- Python 3.11.4
collected 2 items

tests/test_smoke.py .F                                                   [100%]

=================================== FAILURES ===================================
___________________________________ test_ok ____________________________________
    def test_ok():
>       assert False
E       assert False

tests/test_smoke.py:5: AssertionError
============================= 1 failed in 0.01s =============================
"""

JEST_RED = """\
FAIL ./tests/smoke.test.js
  ● smoke suite › should pass

    expect(received).toBe(true)

    Expected: true
    Received: false

      10 |   expect(true).toBe(true)
         |                  ^
      at Object.<anonymous> (tests/smoke.test.js:10:7)

Tests: 1 failed, 1 total
"""

NODETEST_RED = """\
TAP version 13
# Subtest: smoke test
not ok 1 - smoke test
  ---
  duration_ms: 1.234
  failureType: 'testCodeFailure'
  error: 'The expression evaluated to a falsy value'
  file: tests/smoke.test.js
  line: 3
  column: 1
  ...
1..1
# tests 1
# pass 0
# fail 1
"""

PYTEST_MULTI_RED = """\
FAILED tests/test_a.py::test_one - assert False
FAILED tests/test_b.py::test_two - assert False
tests/test_a.py:7: AssertionError
tests/test_b.py:12: AssertionError
"""


# ── detect_runner ──────────────────────────────────────────────────────────────
def test_detect_runner_pytest():
    assert detect_runner("pytest -q", PYTEST_RED) == "pytest"


def test_detect_runner_node_test():
    assert detect_runner("node --test", NODETEST_RED) == "node-test"


def test_detect_runner_jest():
    assert detect_runner("jest", JEST_RED) == "jest"


def test_detect_runner_npm_test_falls_back_to_testout():
    # npm test 是 launcher，下游不明 → 从 testout 内容推（TAP→node-test，FAIL ./→jest）
    assert detect_runner("npm test", NODETEST_RED) == "node-test"
    assert detect_runner("npm test", JEST_RED) == "jest"


def test_detect_runner_unknown():
    assert detect_runner("ruby -Itest", "whatever") == "unknown"


# ── parse_failing_anchors：成功路径 ─────────────────────────────────────────────
def test_pytest_red_maps_to_anchor():
    a = parse_failing_anchors(PYTEST_RED, "pytest")
    ok = [x for x in a if x.status == "ok"]
    assert ok, f"expected ≥1 ok anchor, got {a}"
    assert ok[0].test_name == "test_ok"
    assert ok[0].file == "tests/test_smoke.py"
    assert ok[0].line == 5


def test_jest_red_maps_to_anchor():
    a = parse_failing_anchors(JEST_RED, "jest")
    ok = [x for x in a if x.status == "ok"]
    assert ok, f"expected ≥1 ok anchor, got {a}"
    assert "should pass" in ok[0].test_name
    assert ok[0].file == "tests/smoke.test.js"
    assert ok[0].line == 10


def test_nodetest_red_maps_to_anchor():
    a = parse_failing_anchors(NODETEST_RED, "node-test")
    ok = [x for x in a if x.status == "ok"]
    assert ok, f"expected ≥1 ok anchor, got {a}"
    assert ok[0].test_name == "smoke test"
    assert ok[0].file == "tests/smoke.test.js"
    assert ok[0].line == 3


def test_multiple_failing_tests_all_anchored():
    # design OQ / task 1.1：多失败测试全部锚定（不取首条）
    a = parse_failing_anchors(PYTEST_MULTI_RED, "pytest")
    ok = [x for x in a if x.status == "ok"]
    assert len(ok) == 2
    names = sorted(x.file for x in ok)
    assert names == ["tests/test_a.py", "tests/test_b.py"]


# ── parse_failing_anchors：fail-open（unresolved，design D2）──────────────────
def test_unparseable_pytest_returns_unresolved():
    a = parse_failing_anchors("garbage with no FAILED/traceback", "pytest")
    assert a and a[0].status == "unresolved"
    assert a[0].reason


def test_empty_testout_returns_unresolved():
    a = parse_failing_anchors("", "pytest")
    assert a and a[0].status == "unresolved"


def test_unsupported_runner_returns_unresolved():
    a = parse_failing_anchors("anything", "ruby-rspec")
    assert a and a[0].status == "unresolved"
    assert "ruby-rspec" in (a[0].reason or "")


# ── Anchor 是 frozen（immutability，common/coding-style）────────────────────────
def test_anchor_is_frozen():
    a = Anchor(test_name="t", status="ok", file="f.py", line=1)
    try:
        a.test_name = "other"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("Anchor 应为 frozen（immutability 契约）")


# ── map_anchor_to_hunk / derive_anchors（task 1.2 / 1.3 / 1.4）──────────────────
DIFF_FOO = """\
diff --git a/foo.py b/foo.py
index 123..456 100644
--- a/foo.py
+++ b/foo.py
@@ -8,5 +10,7 @@
 ctx
-removed
+added1
+added2
 ctx
"""


def test_anchor_in_hunk_maps_ok():
    a = Anchor(test_name="t", status="ok", file="foo.py", line=12)
    r = map_anchor_to_hunk(a, DIFF_FOO)
    assert r.status == "ok"
    assert r.hunk and "@@" in r.hunk


def test_anchor_not_in_hunk_is_base_side():
    a = Anchor(test_name="t", status="ok", file="foo.py", line=100)
    assert map_anchor_to_hunk(a, DIFF_FOO).status == "base-side"


def test_anchor_file_not_in_diff_is_base_side():
    a = Anchor(test_name="t", status="ok", file="bar.py", line=5)
    assert map_anchor_to_hunk(a, DIFF_FOO).status == "base-side"


def test_unresolved_anchor_not_mapped():
    a = Anchor(test_name="t", status="unresolved", reason="x")
    assert map_anchor_to_hunk(a, DIFF_FOO).status == "unresolved"


def test_anchor_missing_line_is_unresolved():
    a = Anchor(test_name="t", status="ok", file="foo.py", line=None)
    assert map_anchor_to_hunk(a, DIFF_FOO).status == "unresolved"


def test_derive_end_to_end_base_side_with_empty_diff():
    # PYTEST_RED → ok anchor (tests/test_smoke.py:5)；空 diff → file 不在 diff → base-side
    a = derive_anchors(PYTEST_RED, "pytest", "")
    assert a and a[0].status == "base-side"


def test_derive_end_to_end_ok_when_hunk_covers():
    red = "FAILED foo.py::test_x - assert False\nfoo.py:12: AssertionError\n"
    a = derive_anchors(red, "pytest", DIFF_FOO)
    ok = [x for x in a if x.status == "ok"]
    assert ok and ok[0].file == "foo.py" and ok[0].line == 12 and ok[0].hunk
