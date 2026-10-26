"""projects.py 单测——面板建档机械落盘（add-hub-panel-console D1，spec: hub-panel）。

零 YAML 库手写的防线全在字符串层：转义、单行、长度、重名、目录清理——
逐条压试；不引 yaml 解析依赖（hub 最小依赖面不动），以精确字符串断言钉住产物。
"""
import pytest

import projects


def test_normalize_repo_variants():
    assert projects.normalize_repo("owner/repo") == "https://github.com/owner/repo"
    assert projects.normalize_repo("https://github.com/owner/repo") == "https://github.com/owner/repo"
    assert projects.normalize_repo("https://github.com/owner/repo/") == "https://github.com/owner/repo"
    assert projects.normalize_repo("  owner/repo  ") == "https://github.com/owner/repo"  # 去空白
    assert projects.normalize_repo("https://github.com/O.Repo/x_y-z") == "https://github.com/O.Repo/x_y-z"
    for bad in ("", "owner", "owner/repo/x", "a//b", "https://gitlab.com/o/r", "git@github.com:owner/repo", "owner/"):
        with pytest.raises(projects.ProjectSetupError):
            projects.normalize_repo(bad)


def test_create_project_writes_two_files(tmp_path):
    p = projects.create_project(tmp_path, "demo", "owner/repo", '目标："跑通"', "信号\\一面")
    prof = (p / "profiles" / "demo.yaml").read_text(encoding="utf-8")
    assert "name: demo\n" in prof
    assert "admission: true\n" in prof
    assert 'repo: "https://github.com/owner/repo"\n' in prof  # repo 规范化
    assert 'goal: "目标：\\"跑通\\""\n' in prof  # 引号转义（D1 双引号 scalar）
    assert '  one_liner: "信号\\\\一面"\n' in prof  # 反斜杠转义
    assert (p / "sources.yaml").read_text(encoding="utf-8") == "sources: []\n"


def test_create_project_rejects_bad_name_and_dup(tmp_path):
    for bad in ("../evil", "Demo", "-x", "a b", ""):
        with pytest.raises(projects.ProjectSetupError):
            projects.create_project(tmp_path, bad, "o/r", "g", "s")
    projects.create_project(tmp_path, "demo", "o/r", "g", "s")
    with pytest.raises(projects.ProjectSetupError, match="已存在"):
        projects.create_project(tmp_path, "demo", "o/r", "g", "s")  # 重名
    (tmp_path / "occupied").write_text("x", encoding="utf-8")  # 文件占位同拒
    with pytest.raises(projects.ProjectSetupError):
        projects.create_project(tmp_path, "occupied", "o/r", "g", "s")


def test_create_project_rejects_empty_multiline_and_overlong(tmp_path):
    with pytest.raises(projects.ProjectSetupError):
        projects.create_project(tmp_path, "demo", "o/r", "  ", "s")  # goal 空白
    with pytest.raises(projects.ProjectSetupError, match="单行"):
        projects.create_project(tmp_path, "demo", "o/r", "a\nb", "s")  # 换行注入
    with pytest.raises(projects.ProjectSetupError, match="单行"):
        projects.create_project(tmp_path, "demo", "o/r", "a\rb", "s")
    with pytest.raises(projects.ProjectSetupError, match="上限"):
        projects.create_project(tmp_path, "demo", "o/r", "字" * 251, "s")  # 251 汉字=753B>500
    assert not (tmp_path / "demo").exists()  # 拒绝即零落盘


def test_scalar_escapes_only_backslash_and_quote():
    assert projects._scalar('a"b') == '"a\\"b"'
    assert projects._scalar("a\\b") == '"a\\\\b"'
    assert projects._scalar("普通文本：#, -, []") == '"普通文本：#, -, []"'  # YAML 元字符在双引号 scalar 内无需转义
