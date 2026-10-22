"""cli.py 冒烟测试——面板前的人肉驱动面（add-hub-skeleton 任务 8.2：走 api 同一内部函数）。"""
from pathlib import Path

import cli


def _env(tmp_path: Path) -> dict:
    return {
        "HUB_USER_ID": "roc",
        "HUB_USER_ANTHROPIC_BASE_URL": "https://x",
        "HUB_USER_ANTHROPIC_AUTH_TOKEN": "tok",
        "HUB_USER_GH_TOKEN": "ghp",
        "HUB_IMAGE_TAG_DEFAULT": "pa-executor:v0.1.3",
        "HUB_DATA_ROOT": str(tmp_path / "data"),
    }


def test_cli_enqueue_then_status_roundtrip(tmp_path, capsys, monkeypatch):
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    db_path = tmp_path / "h.sqlite3"
    prd = tmp_path / "p.md"; prd.write_text("# x", encoding="utf-8")
    rc = cli.main(["--db", str(db_path), "enqueue", "--project", "proj-a", "--prd", str(prd)], runner=None)
    assert rc == 0
    out = capsys.readouterr().out
    assert "proj-a" in out and "pending" in out
    rc = cli.main(["--db", str(db_path), "status"], runner=None)
    assert rc == 0
    out = capsys.readouterr().out
    assert "proj-a" in out and "pending" in out  # 与 api.queue_view 同源数据


def test_cli_vault_import_roundtrip_no_echo(tmp_path, capsys, monkeypatch):
    """vault-import --from-env（add-hub-slice-2a 1.3）：落密文、幂等覆盖、输出不回显明文。"""
    from cryptography.fernet import Fernet

    import vault_store as vs

    key = Fernet.generate_key()
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HUB_VAULT_MASTER_KEY", key.decode())
    monkeypatch.setenv("HUB_USER_MODEL", "haiku")  # 可选模型路由随 payload 一并导入

    rc = cli.main(["vault-import", "--from-env"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "GH_TOKEN" in out and "ANTHROPIC_MODEL" in out  # 字段名可见
    assert "ghp" not in out and "tok" not in out and "https://x" not in out  # 明文值不回显

    vault = vs.CredentialVault(tmp_path / "data", key)
    assert vault.get_user_env("roc") == {
        "ANTHROPIC_BASE_URL": "https://x",
        "ANTHROPIC_AUTH_TOKEN": "tok",
        "GH_TOKEN": "ghp",
        "ANTHROPIC_MODEL": "haiku",
    }
    monkeypatch.setenv("HUB_USER_GH_TOKEN", "ghp-rotated")
    assert cli.main(["vault-import", "--from-env"]) == 0  # 重复导入幂等覆盖
    assert vault.get_user_env("roc")["GH_TOKEN"] == "ghp-rotated"


def test_cli_vault_import_missing_key_fails(tmp_path, capsys, monkeypatch):
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("HUB_VAULT_MASTER_KEY", raising=False)
    rc = cli.main(["vault-import", "--from-env"])
    assert rc == 2
    assert "HUB_VAULT_MASTER_KEY" in capsys.readouterr().err


def test_cli_entrypoint_runs_as_script(tmp_path):
    """`python3 cli.py status` 直跑必须有输出（5.3 真机验证坐实的回归防：
    __main__ 入口块缺失时 import 即静默退出——rc=0 零输出，CLI 形同虚设）。"""
    import os
    import subprocess
    import sys

    (tmp_path / "data").mkdir()
    env = {**os.environ, **_env(tmp_path), "HUB_VAULT_MASTER_KEY": ""}
    env.pop("HUB_VAULT_MASTER_KEY", None)
    r = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "cli.py"), "status"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert r.returncode == 0
    assert "共 0 项" in r.stdout  # 空队列也必须有这行——静默即入口块又没了
