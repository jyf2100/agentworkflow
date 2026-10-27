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


# ── forge 形态（fix-hub-form-parity ④，spec: hub-panel「建项目表单」）──


def test_create_gitlab_project_writes_forge_keys(tmp_path):
    p = projects.create_project(tmp_path, "lab-a", "my-group/sub-team/demo-repo", "g", "s",
                                forge="gitlab", gitlab_host="http://172.32.153.183:8180")
    prof = (p / "profiles" / "lab-a.yaml").read_text(encoding="utf-8")
    assert 'forge: gitlab\n' in prof
    assert 'gitlab_host: "http://172.32.153.183:8180"\n' in prof
    assert 'repo: "my-group/sub-team/demo-repo"\n' in prof  # 全路径原样，不走 GitHub 归一
    assert (p / "sources.yaml").read_text(encoding="utf-8") == "sources: []\n"


def test_create_gitlab_missing_or_bad_host_rejected(tmp_path):
    with pytest.raises(projects.ProjectSetupError, match="gitlab_host 必填"):
        projects.create_project(tmp_path, "lab-a", "grp/repo", "g", "s", forge="gitlab")
    for bad in ("", "172.32.153.183:8180", "http://", "http://host:port", "http://host/path", "ftp://host"):
        with pytest.raises(projects.ProjectSetupError):
            projects.create_project(tmp_path, "lab-a", "grp/repo", "g", "s",
                                    forge="gitlab", gitlab_host=bad)
    assert not (tmp_path / "lab-a").exists()  # fail-fast 零落盘


def test_create_gitlab_repo_form_rejected(tmp_path):
    # forge repo 不走 GitHub 归一：URL / 本地路径 / 单段 / 空段全拒
    for bad in ("https://gitlab.com/grp/repo", "/abs/path/clone", "grp", "grp//repo", "grp/repo/", ""):
        with pytest.raises(projects.ProjectSetupError):
            projects.create_project(tmp_path, "lab-a", bad, "g", "s",
                                    forge="gitlab", gitlab_host="http://h:8180")


def test_create_gitcode_project_two_segments_only(tmp_path):
    p = projects.create_project(tmp_path, "code-a", "owner/repo", "g", "s", forge="gitcode")
    prof = (p / "profiles" / "code-a.yaml").read_text(encoding="utf-8")
    assert 'forge: gitcode\n' in prof and "gitlab_host" not in prof  # 无 host 键
    assert 'repo: "owner/repo"\n' in prof
    with pytest.raises(projects.ProjectSetupError, match="两级"):
        projects.create_project(tmp_path, "code-b", "grp/sub/repo", "g", "s", forge="gitcode")
    with pytest.raises(projects.ProjectSetupError, match="host 键"):
        projects.create_project(tmp_path, "code-c", "o/r", "g", "s", forge="gitcode", gitlab_host="http://h")
    assert not (tmp_path / "code-b").exists() and not (tmp_path / "code-c").exists()


def test_create_forge_invalid_value_rejected(tmp_path):
    for bad in ("svn", "GitHub", "git lab", "gitlab-host"):
        with pytest.raises(projects.ProjectSetupError, match="forge 非法"):
            projects.create_project(tmp_path, "x-a", "o/r", "g", "s", forge=bad)


def test_create_github_default_byte_identical_regression(tmp_path):
    """github 缺省回归锚：不传 forge（与显式 'github'）落盘与既有格式逐字节一致。"""
    expected = (
        "name: demo\n"
        "admission: true\n"
        'repo: "https://github.com/owner/repo"\n'
        'goal: "g"\n'
        "match_surface:\n"
        '  one_liner: "s"\n'
    )
    p1 = projects.create_project(tmp_path, "demo", "owner/repo", "g", "s")
    assert (p1 / "profiles" / "demo.yaml").read_text(encoding="utf-8") == expected
    p2 = projects.create_project(tmp_path, "demo2", "owner/repo", "g", "s", forge="github")
    assert (p2 / "profiles" / "demo2.yaml").read_text(encoding="utf-8") == expected.replace("demo", "demo2")
