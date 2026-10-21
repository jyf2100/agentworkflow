"""meta_sign.py 单测——F10 署名 meta + run 名预分配（add-hub-skeleton 任务 4.1-4.2，spec: execution-hub「F10 署名 meta」）。"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import meta_sign


def test_sign_meta_writes_pinned_filename_with_four_fields(tmp_path: Path):
    run_dir = tmp_path / "runs" / "proj-a" / "20261021_my-prd"
    path = meta_sign.sign_meta(
        run_dir, "20261021", "my-prd",
        initiator="roc", queue_item_id=7, image_tag="pa-executor:v0.1.3",
        now=datetime(2026, 10, 21, 3, 17, 0, tzinfo=timezone.utc),
    )
    assert path.name == "20261021_my-prd.meta.json"
    assert re.fullmatch(meta_sign.META_NAME_RE, path.name)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {
        "initiator": "roc",
        "queue_item_id": 7,
        "image_tag": "pa-executor:v0.1.3",
        "written_at": "2026-10-21T03:17:00+00:00",
    }


def test_sign_meta_accepts_bumped_stamp_forms(tmp_path: Path):
    # stamp 实际形态：8 位日期 + 可选 mN 避碰后缀（_bump_stamp_suffix 事实，A1）
    path = meta_sign.sign_meta(tmp_path / "r1", "20261021m2", "x", initiator="u", queue_item_id=1, image_tag="t:v1")
    assert path.name == "20261021m2_x.meta.json"
    path = meta_sign.sign_meta(tmp_path / "r2", "20261021m", "x", initiator="u", queue_item_id=1, image_tag="t:v1")
    assert path.name == "20261021m_x.meta.json"


def test_sign_meta_rejects_non_meta_patterns(tmp_path: Path):
    bad = [
        ("2026-1021", "my-prd"),   # 日期带横线（首版凭印象的错形态）
        ("2026102", "my-prd"),      # 7 位
        ("20261021", "My_Prd"),     # 大写/下划线
        ("20261021", "../escape"),  # 路径注入
        ("20261021", "a/b"),        # 目录注入
        ("", "my-prd"), ("20261021", ""),  # 空段
        ("20261021_x", "y"),        # stamp 内含 slug 段
    ]
    for stamp, slug in bad:
        try:
            meta_sign.sign_meta(tmp_path / "r", stamp, slug, initiator="u", queue_item_id=1, image_tag="t:v1")
            raised = False
        except meta_sign.MetaSignError:
            raised = True
        assert raised, f"应拒绝：stamp={stamp!r} slug={slug!r}"


def test_sign_meta_target_stays_inside_run_dir(tmp_path: Path):
    run_dir = tmp_path / "runs" / "proj-a" / "20261021_x"
    path = meta_sign.sign_meta(run_dir, "20261021", "x", initiator="u", queue_item_id=1, image_tag="t:v1")
    assert path.resolve().parent == run_dir.resolve()  # 写面钉死：meta 只落 run 目录内（F10）


def test_allocate_fresh_stamp_bumps_only_on_manifest_collision(tmp_path: Path):
    state = tmp_path / "state"; state.mkdir()
    assert meta_sign.allocate_fresh_stamp(state, "20261021") == "20261021"  # 无撞名：原样沿用（inject 契约）
    (state / "prd_manifest_20261021.json").write_text("{}", encoding="utf-8")
    assert meta_sign.allocate_fresh_stamp(state, "20261021") == "20261021m"
    (state / "prd_manifest_20261021m.json").write_text("{}", encoding="utf-8")
    assert meta_sign.allocate_fresh_stamp(state, "20261021") == "20261021m2"  # m 后缀自增形态
    (state / "prd_manifest_20261021m2.json").write_text("{}", encoding="utf-8")
    assert meta_sign.allocate_fresh_stamp(state, "20261021") == "20261021m3"


def test_allocate_rejects_non_date_base(tmp_path: Path):
    try:
        meta_sign.allocate_fresh_stamp(tmp_path, "21-10-2026"); raised = False
    except meta_sign.MetaSignError:
        raised = True
    assert raised


def test_sign_meta_first_write_wins(tmp_path: Path):
    # B2 首写保真：已署名的 meta 不被重写——written_at 是首派署名时刻，非最后尝试时刻
    run_dir = tmp_path / "r"
    p1 = meta_sign.sign_meta(
        run_dir, "20261021", "x", initiator="u", queue_item_id=1, image_tag="t:v1",
        now=datetime(2026, 10, 21, 3, 0, 0, tzinfo=timezone.utc),
    )
    p2 = meta_sign.sign_meta(
        run_dir, "20261021", "x", initiator="u", queue_item_id=1, image_tag="t:v1",
        now=datetime(2026, 10, 21, 9, 30, 0, tzinfo=timezone.utc),
    )
    assert p1 == p2
    assert json.loads(p1.read_text(encoding="utf-8"))["written_at"] == "2026-10-21T03:00:00+00:00"


def test_module_exposes_no_generic_write():
    # 写面钉死（4.2）：本模块定义的函数仅署名/预分配两个入口——无任意路径写函数
    import inspect
    funcs = {n for n, f in inspect.getmembers(meta_sign, inspect.isfunction) if f.__module__ == "meta_sign"}
    assert funcs <= {"sign_meta", "allocate_fresh_stamp"}, funcs
