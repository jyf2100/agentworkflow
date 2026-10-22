"""stale_check.py 单测——镜像陈旧检测三态分流（add-hub-slice-4a，spec: hub-notifications「镜像陈旧告警」）。

fake remote 注入（Callable 替身，零真 gh/网络）；断言面 = stderr 告警文本 +
FakeMMClient 私信记录 + seen 去抖集合内容。
"""
from pathlib import Path

import config
import notify
import stale_check


class FakeRemote:
    """可编程 remote 客户端：result = (tag|None, err|None)，可中途改判（场景推进）。"""

    def __init__(self, result: tuple[str | None, str | None]):
        self.result = result
        self.calls: list[str] = []

    def __call__(self, repo: str) -> tuple[str | None, str | None]:
        self.calls.append(repo)
        return self.result


def _cfg(tmp_path: Path, repo="acme/pa-executor", tag="pa-executor:v0.1.3", **over) -> config.HubConfig:
    base = dict(
        user_id="roc", anthropic_base_url="u", anthropic_auth_token="t", gh_token="ghp",
        user_model=None, image_tag_default=tag, data_root=tmp_path / "data",
        max_containers=1, max_retries=0, image_template_repo=repo,
    )
    base.update(over)
    return config.HubConfig(**base)


def test_stale_alert_once_then_deduped(tmp_path, capsys):
    # FOUND 且 ≠ 默认 → 私信配置单用户 + stderr 留痕；同组合第二次去抖
    remote = FakeRemote(("pa-executor:v0.2.0", None))
    mm = notify.FakeMMClient(user_ids={"roc": "a" * 26})
    n = notify.Notifier(_cfg(tmp_path), mm=mm)
    seen: set = set()
    stale_check.check_stale(_cfg(tmp_path), seen, client=remote, notifier=n)
    stale_check.check_stale(_cfg(tmp_path), seen, client=remote, notifier=n)
    assert len(mm.dms) == 1 and "pa-executor:v0.2.0" in mm.dms[0][1] and "pa-executor:v0.1.3" in mm.dms[0][1]
    assert "镜像陈旧" in capsys.readouterr().err
    assert seen == {("acme/pa-executor", "pa-executor:v0.2.0", "pa-executor:v0.1.3")}


def test_new_remote_tag_alerts_again(tmp_path):
    # 去抖按组合：remote 又出新版（新组合）→ 再告一次（新陈旧是事实）
    remote = FakeRemote(("pa-executor:v0.2.0", None))
    mm = notify.FakeMMClient(user_ids={"roc": "a" * 26})
    n = notify.Notifier(_cfg(tmp_path), mm=mm)
    seen: set = set()
    stale_check.check_stale(_cfg(tmp_path), seen, client=remote, notifier=n)
    remote.result = ("pa-executor:v0.3.0", None)
    stale_check.check_stale(_cfg(tmp_path), seen, client=remote, notifier=n)
    assert len(mm.dms) == 2 and "v0.3.0" in mm.dms[1][1]


def test_tags_match_no_alert(tmp_path, capsys):
    remote = FakeRemote(("pa-executor:v0.1.3", None))
    seen: set = set()
    stale_check.check_stale(_cfg(tmp_path), seen, client=remote)
    assert seen == set() and "镜像陈旧" not in capsys.readouterr().err


def test_404_loud_config_error_once_not_compared(tmp_path, capsys):
    # 404 = 确定性配置错：stderr 高声（点名 HUB_IMAGE_TEMPLATE_REPO）+ 不进比对（私信无）+ 去抖一次
    remote = FakeRemote((None, "config_4xx"))
    mm = notify.FakeMMClient(user_ids={"roc": "a" * 26})
    n = notify.Notifier(_cfg(tmp_path), mm=mm)
    seen: set = set()
    stale_check.check_stale(_cfg(tmp_path), seen, client=remote, notifier=n)
    stale_check.check_stale(_cfg(tmp_path), seen, client=remote, notifier=n)
    err = capsys.readouterr().err
    assert err.count("404") == 1 and "HUB_IMAGE_TEMPLATE_REPO" in err  # 高声一次不重复
    assert mm.dms == []  # 配置错走 stderr 不走私信，不进陈旧比对
    assert seen == {("acme/pa-executor", "config_4xx")}


def test_unknown_no_alert_retry_next_round(tmp_path, capsys):
    # 网络错/5xx = UNKNOWN：无告警（不误报）、seen 不记；下轮恢复后正常比对告警
    remote = FakeRemote((None, None))
    mm = notify.FakeMMClient(user_ids={"roc": "a" * 26})
    n = notify.Notifier(_cfg(tmp_path), mm=mm)
    seen: set = set()
    stale_check.check_stale(_cfg(tmp_path), seen, client=remote, notifier=n)
    err = capsys.readouterr().err
    assert mm.dms == [] and "镜像陈旧" not in err and "ERROR" not in err  # 只有普通日志行
    assert "下轮再查" in err and seen == set()  # 不记去抖 → 下轮可再查
    remote.result = ("pa-executor:v0.2.0", None)  # 恢复
    stale_check.check_stale(_cfg(tmp_path), seen, client=remote, notifier=n)
    assert len(mm.dms) == 1  # 恢复后正常告警（未被去抖吞掉）


def test_repo_unconfigured_zero_behavior(tmp_path):
    remote = FakeRemote(("pa-executor:v0.9.0", None))
    seen: set = set()
    stale_check.check_stale(_cfg(tmp_path, repo=None), seen, client=remote)
    assert remote.calls == [] and seen == set()  # 未配置 = 检测关闭零行为


def test_gh_cli_missing_warn_once_detection_closed(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(stale_check.shutil, "which", lambda name: None)
    seen: set = set()
    stale_check.check_stale(_cfg(tmp_path), seen)  # client=None 走真客户端路径
    stale_check.check_stale(_cfg(tmp_path), seen)  # 第二次连 warn 都不再有
    err = capsys.readouterr().err
    assert err.count("gh CLI 缺失") == 1 and "检测关闭" in err
    assert "__no_gh__" in seen  # 去抖键留痕（进程生命周期内不再 warn）
