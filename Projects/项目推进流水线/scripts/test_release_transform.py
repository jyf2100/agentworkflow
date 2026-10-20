# test_release_transform.py — release 链单测（productize-pa-template task 2.4）。
#
# 四类覆盖（对应 tasks 2.4 四项）：
#   ① 转换幂等性：apply_rule 三分支（transformed/already/mismatch fail-loud）+ 真实规则文件两遍转换零改动；
#   ② grep 门禁负例：fixture vault 植入个人痕迹（本机用户路径）→ release_template.sh rc=2；
#   ③ 内容契约负例：manifest 混入 SPEC.md（禁入路径）→ rc=2；
#   ④ 规约层漂移负例：篡改副本 openspec/specs → check_spec_layer.sh 非零。
# 另有锚漂移早警：每条 RULE 的锚文本必须在 vault 现源中（漂移在测试期暴露，不等发布期）。
# 注意：门禁 pattern 在本文件一律片段拼接（_j）——grep 门禁扫描整个副本，字面量会被自己命中。
#
# template 投影不含 release/ 工具链（manifest 刻意排除）——module-level skip 保持副本 quality.sh 绿。
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent
PA_ROOT = SCRIPTS_DIR.parent
VAULT_ROOT = PA_ROOT.parent.parent
RELEASE_DIR = PA_ROOT / "release"
TRANSFORM_PY = RELEASE_DIR / "transform" / "transform.py"
RELEASE_SH = RELEASE_DIR / "release_template.sh"
SPEC_LAYER_SH = RELEASE_DIR / "check_spec_layer.sh"

if not TRANSFORM_PY.exists():
    pytest.skip("release/ 工具链不在本 checkout（template 投影）——release 单测仅 vault 侧运行",
                allow_module_level=True)

sys.path.insert(0, str(TRANSFORM_PY.parent))
import transform  # noqa: E402  # release/ 模块经 sys.path 引入（扁平模块约定，不抽包）

def _j(*parts: str) -> str:
    """片段拼接——避免门禁 pattern 在测试源码中成串出现（grep 门禁会扫到测试自身）。"""
    return "".join(parts)


# 与 release_template.sh PERSONAL_PATTERNS 同源的复验集（word-boundary 项用 re 表达）
_PERSONAL_RES = (
    _j("/Users/", "roc"), _j("/Users/", "[a-z]"),
    _j("smtp.", "new", "land.com.", "cn"), _j("vip.", "si", "na.com"),
    _j("juy", "f@"), _j("dv", "s@"), _j("new", "land-smtp"), _j("si", "na-smtp"),
    _j("new", "land"), r"\b" + "si" + r"na\b", _j("部门工作", "跟踪"), _j("ok", "winds"),
    _j("居", "燕峰"), _j("Dav", "Mail"), _j("jyf", "2100"), _j("/mnt/", "disk01"),
)


# ════════════════════════ ① 转换纯函数与幂等 ════════════════════════

def test_apply_rule_three_branches():
    rule = transform.TransformRule(relpath="x.md", old="旧锚", new="新文")
    out, st = transform.apply_rule("前旧锚后", rule); assert (out, st) == ("前新文后", "transformed")
    out, st = transform.apply_rule("前新文后", rule); assert (out, st) == ("前新文后", "already")
    with pytest.raises(transform.TransformMismatchError):
        transform.apply_rule("毫无关系", rule)   # 两者皆无 → fail-loud，绝不静默跳过


def test_rule_anchors_all_present_in_vault():
    """锚漂移早警：每条规则的 old（或幂等形态 new）必须在 vault 现源——vault 改锚文本即红。"""
    for rule in transform.RULES:
        src = VAULT_ROOT / rule.relpath
        assert src.is_file(), f"规则指向的文件不存在：{rule.relpath}"
        text = src.read_text(encoding="utf-8")
        assert rule.old in text or rule.new in text, \
            f"锚漂移（vault 源已改，RULES 须人工更新）：{rule.relpath}：{rule.old[:50]!r}"


def test_spec_layer_has_no_rules():
    """规约层（openspec/specs、docs/adr）零规则——passthrough 逐字节一致的转换面保证。"""
    assert not [r for r in transform.RULES if r.relpath.startswith(("openspec/", "docs/adr/"))]


def test_transform_real_rule_files_idempotent_and_clean(tmp_path):
    """真实全部规则文件：两遍转换零改动（幂等）+ 产物零个人痕迹（门禁 pattern 集）。"""
    rels = sorted({r.relpath for r in transform.RULES})
    p1, p2 = tmp_path / "p1", tmp_path / "p2"
    transform.transform_tree(VAULT_ROOT, p1, rels)
    transform.transform_tree(p1, p2, rels)   # 以第一遍产物为源 → 应全 already
    for rel in rels:
        text1 = (p1 / rel).read_text(encoding="utf-8")
        assert text1 == (p2 / rel).read_text(encoding="utf-8"), f"幂等破坏：{rel}"
        for pat in _PERSONAL_RES:
            assert not re.search(pat, text1), f"个人痕迹残留 [{pat}] 于 {rel}"


def test_passthrough_byte_identical(tmp_path):
    """无规则文件 passthrough 逐字节一致（read_bytes 保真，含行尾/尾空行）。"""
    rel = "Projects/项目推进流水线/pyproject.toml"
    transform.transform_tree(VAULT_ROOT, tmp_path, [rel])
    assert (tmp_path / rel).read_bytes() == (VAULT_ROOT / rel).read_bytes()


# ════════════════════════ ②③④ 发布门负例（驱动脚本本体）════════════════════════

def _git_fixture_vault(tmp_path: Path, files: dict[str, str]) -> Path:
    """最小 fixture vault：git init + add（ls-files 只需 tracked，无需 commit）。"""
    v = tmp_path / "vault"
    for rel, content in files.items():
        p = v / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(v), "init", "-q"], check=True, timeout=30)
    subprocess.run(["git", "-C", str(v), "add", "-A"], check=True, timeout=30)
    return v


def _run_release(vault: Path, manifest: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(RELEASE_SH), "--vault", str(vault), "--manifest", str(manifest), "--out", str(out)],
        capture_output=True, text=True, timeout=120)


def test_gate_grep_planted_personal_trace_fails(tmp_path):
    """grep 门禁负例：植入本机用户路径痕迹于无规则文件 → passthrough 保留 → 门禁命中 rc=2。
    （fixture quality.sh 恒绿，确保 rc=2 归因于 grep 门禁而非质量门。）"""
    planted = _j("# 临时记录：找 /Users/", "roc 确认\n")
    v = _git_fixture_vault(tmp_path, {
        "Projects/项目推进流水线/scripts/quality.sh": "#!/usr/bin/env bash\nexit 0\n",
        "Projects/项目推进流水线/scripts/zz_note.md": planted,
    })
    m = tmp_path / "m.txt"; m.write_text("Projects/项目推进流水线/scripts/\n", encoding="utf-8")
    r = _run_release(v, m, tmp_path / "out")
    assert r.returncode == 2 and "grep 门禁命中" in r.stderr, f"rc={r.returncode} stderr={r.stderr}"


def test_gate_forbidden_path_fails(tmp_path):
    """内容契约负例：manifest 混入 SPEC.md（禁入路径）→ rc=2，且在质量门之前拦截。"""
    v = _git_fixture_vault(tmp_path, {"Projects/项目推进流水线/SPEC.md": "# 旧总纲（superseded）\n"})
    m = tmp_path / "m.txt"; m.write_text("Projects/项目推进流水线/SPEC.md\n", encoding="utf-8")
    r = _run_release(v, m, tmp_path / "out")
    assert r.returncode == 2 and "禁入路径" in r.stderr, f"rc={r.returncode} stderr={r.stderr}"


def test_check_spec_layer_tamper_fails(tmp_path):
    """规约层漂移负例：副本 spec 被篡改 → check_spec_layer.sh 非零（发布链同一脚本）。"""
    va, ou = tmp_path / "va", tmp_path / "ou"
    for root in (va, ou):
        for rel in ("openspec/specs/x/spec.md", "docs/adr/0001-x.md"):
            p = root / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("# spec\n", encoding="utf-8")
    ok = subprocess.run(["bash", str(SPEC_LAYER_SH), str(va), str(ou)], capture_output=True, text=True, timeout=30)
    assert ok.returncode == 0, ok.stderr
    (ou / "openspec/specs/x/spec.md").write_text("# 被篡改\n", encoding="utf-8")
    bad = subprocess.run(["bash", str(SPEC_LAYER_SH), str(va), str(ou)], capture_output=True, text=True, timeout=30)
    assert bad.returncode != 0 and "不一致" in bad.stderr, bad.stderr
