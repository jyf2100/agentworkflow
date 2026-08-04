# -*- coding: utf-8 -*-
"""test_verify_bundles.py — bundle splitting 单测（harden-pa-verify-determinism task 3.1/3.2/5.2）。

threshold 边界（上/下/等）+ related-file grouping（impl+test / i18n 对）。hermetic（canned diff）。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from verify_bundles import Bundle, split_bundles


def _diff(files: list[str]) -> str:
    """构造多文件 git diff canned（每文件 1 行 +new）。"""
    return "".join(
        f"diff --git a/{f} b/{f}\nindex ..\n--- a/{f}\n+++ b/{f}\n@@ -1,2 +1,3 @@\n ctx\n+new\n"
        for f in files)


DIFF_SMALL = _diff(["foo.py"])


def test_empty_diff_no_bundles():
    assert split_bundles("") == []


def test_small_diff_single_bundle():
    b = split_bundles(DIFF_SMALL)
    assert len(b) == 1
    assert b[0].files == ("foo.py",)
    assert "single-bundle" in b[0].reason


def test_at_threshold_still_single():
    # 等于 threshold（10 文件）→ ≤ → single-bundle（spec B4: at or below）
    b = split_bundles(_diff([f"src/f{i}.py" for i in range(10)]))
    assert len(b) == 1


def test_above_file_threshold_splits():
    # 12 文件各异 stem → 12 bundle（>10 触发 split）
    b = split_bundles(_diff([f"src/mod{i}.py" for i in range(12)]),
                      file_threshold=10, line_threshold=100000)
    assert len(b) > 1


def test_related_impl_and_test_grouped():
    diff = _diff(["src/foo.py", "tests/test_foo.py", "src/bar.py"])
    b = split_bundles(diff, file_threshold=2, line_threshold=100000)
    merged = {f: bundle for bundle in b for f in bundle.files}
    # foo.py + test_foo.py 同 stem(foo) → 同 bundle；bar.py 独立
    assert merged["src/foo.py"] is merged["tests/test_foo.py"]
    assert merged["src/bar.py"] is not merged["src/foo.py"]


def test_i18n_pair_grouped():
    diff = _diff(["locales/message_en.properties", "locales/message_zh.properties", "src/x.py"])
    b = split_bundles(diff, file_threshold=2, line_threshold=100000)
    merged = {f: bundle for bundle in b for f in bundle.files}
    assert merged["locales/message_en.properties"] is merged["locales/message_zh.properties"]


def test_above_line_threshold_enters_split_path():
    # 1 文件但 >800 行 → 进 split 路径（单 stem → 1 bundle，但 reason=stem 非 single-bundle）
    big = "diff --git a/big.py b/big.py\n--- a/big.py\n+++ b/big.py\n"
    big += "".join(f"+line{i}\n" for i in range(900))
    b = split_bundles(big, file_threshold=100000, line_threshold=800)
    assert b and "single-bundle" not in b[0].reason


def test_bundle_is_frozen():
    b = Bundle(files=("a.py",))
    try:
        b.files = ("b.py",)  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("Bundle 应为 frozen（immutability 契约）")
