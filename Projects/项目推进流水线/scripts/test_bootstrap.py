# test_bootstrap.py — bootstrap 实例化冒烟（productize-pa-template task 3.5）。
# 全部经 PATH/HOME 隔离驱动真实脚本：python3/claude 皆 PATH mock（零真实安装、零真实 LLM 调用），
# git 用 fixture 仓（init+add 即可，无需 commit）。template 副本随行同测（bootstrap/ 在 manifest）。
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent
PA_ROOT = SCRIPTS_DIR.parent
BOOTSTRAP_SRC = PA_ROOT / "bootstrap" / "bootstrap.sh"
SAMPLES_SRC = PA_ROOT / "bootstrap" / "samples"

if not BOOTSTRAP_SRC.exists():
    pytest.skip("bootstrap/ 不在本 checkout", allow_module_level=True)


def _sh(bin_: Path, name: str, body: str) -> None:
    p = bin_ / name; p.write_text(body, encoding="utf-8"); p.chmod(0o755)


def _fakebin(tmp_path: Path, with_claude: bool = True) -> Path:
    bin_ = tmp_path / "fakebin"; bin_.mkdir(exist_ok=True)
    _sh(bin_, "python3", "#!/bin/sh\nexit 0\n")   # version 门与 pip 安装全 mock 绿
    if with_claude:
        _sh(bin_, "claude", '#!/bin/sh\ncase "$*" in *--version*) echo "1.0.0-mock";; *) echo ok;; esac\nexit 0\n')
    return bin_


def _fixture_root(tmp_path: Path):
    root = tmp_path / "tmpl"
    pa = root / "Projects" / "项目推进流水线"
    (pa / "bootstrap" / "samples").mkdir(parents=True)
    (pa / "pyproject.toml").write_text('[project]\nname = "pa-pipeline"\nversion = "0"\n', encoding="utf-8")
    shutil.copy2(BOOTSTRAP_SRC, pa / "bootstrap" / "bootstrap.sh")
    for s in SAMPLES_SRC.iterdir():
        shutil.copy2(s, pa / "bootstrap" / "samples" / s.name)
    (pa / "scripts").mkdir()
    (pa / "scripts" / "zz_probe.py").write_text("# probe\n", encoding="utf-8")
    (root / ".claude" / "agents").mkdir(parents=True)
    (root / ".claude" / "agents" / "pa-prd.md").write_text("# persona\n", encoding="utf-8")
    (root / "openspec" / "specs" / "x").mkdir(parents=True)
    (root / "openspec" / "specs" / "x" / "spec.md").write_text("# spec\n", encoding="utf-8")
    home = tmp_path / "home"; home.mkdir()
    return root, pa, home


def _run(script: Path, home: Path, bin_: Path, *extra: str) -> subprocess.CompletedProcess:
    env = {"PATH": f"{bin_}:/usr/bin:/bin", "HOME": str(home)}
    return subprocess.run(["bash", str(script), *extra],
                          capture_output=True, text=True, env=env, timeout=120)


def _run_dev(root: Path, home: Path, bin_: Path, *extra: str) -> subprocess.CompletedProcess:
    """vault 开发布局入口：<pa>/bootstrap/bootstrap.sh。"""
    return _run(root / "Projects" / "项目推进流水线" / "bootstrap" / "bootstrap.sh", home, bin_, *extra)


def test_clean_init_full_flow(tmp_path):
    """干净目录初始化全流程：rc=0、实例数据落位、cron/SMTP 均缺省关。"""
    root, pa, home = _fixture_root(tmp_path)
    r = _run_dev(root, home, _fakebin(tmp_path), "--non-interactive")
    assert r.returncode == 0, r.stderr + r.stdout
    assert (root / ".project-auto" / "sources.yaml").is_file()        # PA_HOME=VAULT_ROOT/.project-auto（仓根）
    assert (pa / "config" / "notify.example.env").is_file()
    assert (pa / "config" / "profile.example.yaml").is_file()         # 样例在 config/（不进 profiles glob）
    assert "bootstrap 完成" in r.stdout
    assert "未装 cron" in r.stdout


def test_template_root_layout(tmp_path):
    """release 投影布局（bootstrap.sh 在 template 根）同样可跑。"""
    root, pa, home = _fixture_root(tmp_path)
    shutil.copy2(BOOTSTRAP_SRC, root / "bootstrap.sh")
    r = _run(root / "bootstrap.sh", home, _fakebin(tmp_path), "--non-interactive")
    assert r.returncode == 0, r.stderr + r.stdout


def test_missing_claude_fails_with_guidance(tmp_path):
    """claude CLI 缺失 → rc=2 + 明确指引，不继续后续步骤。"""
    root, _pa, home = _fixture_root(tmp_path)
    r = _run_dev(root, home, _fakebin(tmp_path, with_claude=False), "--non-interactive")
    assert r.returncode == 2 and "claude CLI" in r.stderr
    assert ".project-auto" not in r.stdout   # 前置失败即停：未走到实例初始化


def test_persona_conflict_refused_non_interactive(tmp_path):
    """D4：用户级同名 persona 冲突 → 列出 + 非交互默认拒绝（rc=2）。"""
    root, _pa, home = _fixture_root(tmp_path)
    (home / ".claude" / "agents").mkdir(parents=True)
    (home / ".claude" / "agents" / "pa-prd.md").write_text("# 用户级同名\n", encoding="utf-8")
    r = _run_dev(root, home, _fakebin(tmp_path), "--non-interactive")
    assert r.returncode == 2 and "同名 persona" in r.stderr and "拒绝" in r.stderr


def test_drift_tampered_scripts_warns_and_preserves(tmp_path):
    """D1 机制化：受检 scripts 篡改 → 漂移警告 + 回流指引，不覆盖、不阻断。"""
    root, pa, home = _fixture_root(tmp_path)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, timeout=30)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, timeout=30)
    probe = pa / "scripts" / "zz_probe.py"; probe.write_text("# 本地篡改\n", encoding="utf-8")
    r = _run_dev(root, home, _fakebin(tmp_path), "--non-interactive")
    assert r.returncode == 0, r.stderr + r.stdout
    out = r.stdout + r.stderr
    assert "本地漂移" in out and "回流" in out
    assert probe.read_text(encoding="utf-8") == "# 本地篡改\n"


def test_existing_instance_data_preserved(tmp_path):
    """实例数据（config/.project-auto）已存在 → 样例不覆盖。"""
    root, pa, home = _fixture_root(tmp_path)
    tgt = root / ".project-auto" / "sources.yaml"
    tgt.parent.mkdir(parents=True); tgt.write_text("# 实例自有\n", encoding="utf-8")
    r = _run_dev(root, home, _fakebin(tmp_path), "--non-interactive")
    assert r.returncode == 0, r.stderr + r.stdout
    assert tgt.read_text(encoding="utf-8") == "# 实例自有\n"
