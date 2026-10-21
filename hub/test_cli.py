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
