"""RSI01: candidate-controlled bytes may not become executable authority."""

import json
import multiprocessing
from pathlib import Path

import pytest

from tools.ai_inference_lab.rsi.snapshots import Store, SnapshotError, atomic_create


def resources(strategy="baseline", prompt="只提取有来源的字段"):
    return {"prompt.txt": prompt, "mock.json": json.dumps({"strategy": strategy})}


def writer(path, queue):
    try:
        atomic_create(Path(path), b"same")
        queue.put("ok")
    except FileExistsError:
        queue.put("exists")


def test_snapshot_lineage_and_actual_policy(tmp_path):
    store = Store(tmp_path)
    base = store.snapshot(resources(), reason="基线")
    changed = store.snapshot(resources("gross-label-safe"), parent=base["id"], reason="合计标签")
    assert changed["id"] != base["id"]
    assert changed["root_id"] == base["id"]
    assert store.diff(base["id"], changed["id"]) == ["mock.json"]
    assert store.policy(changed["id"]).strip_invoice_zeros is False
    assert "小写" in store.policy(changed["id"]).gross_pattern
    assert store.snapshot(resources(), reason="同内容新说明")["id"] == base["id"]


@pytest.mark.parametrize("parent", [False, 0, ""])
def test_invalid_falsy_parent_never_creates_unloadable_snapshot(tmp_path, parent):
    with pytest.raises(SnapshotError):
        Store(tmp_path).snapshot(resources(), reason="无效父项", parent=parent)
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize(
    "bad",
    [
        {"schema.py": "changed"},
        {"../prompt.txt": "escape"},
        {"C:/prompt.txt": "escape"},
        {"//server/share": "escape"},
        {"prompt.txt": "only"},
        {"prompt.txt": "p", "mock.json": '{"strategy":"baseline","strategy":"baseline"}'},
        {"prompt.txt": "p", "mock.json": '{"strategy":"baseline","budget":999}'},
        {"prompt.txt": "p", "mock.json": '{"strategy":true}'},
        {"prompt.txt": "p", "mock.json": '{"strategy":"eval"}'},
        {"prompt.txt": "x" * 40000, "mock.json": '{"strategy":"baseline"}'},
    ],
)
def test_reject_untrusted_resources(tmp_path, bad):
    with pytest.raises(SnapshotError):
        Store(tmp_path).snapshot(bad, reason="越界")
    assert not list(tmp_path.glob("*.json"))


def test_tamper_missing_parent_and_dependency_binding(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(SnapshotError):
        store.snapshot(resources(), parent="f" * 64, reason="缺父项")
    base = store.snapshot(resources(), reason="基线")
    child = store.snapshot(resources(prompt="变化"), parent=base["id"], reason="修改")
    path = tmp_path / (child["id"] + ".json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["resources"]["prompt.txt"] = "伪造"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SnapshotError):
        store.load(child["id"])


def test_atomic_two_process_no_overwrite(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    path = tmp_path / "immutable"
    jobs = [ctx.Process(target=writer, args=(str(path), queue)) for _ in range(2)]
    for job in jobs:
        job.start()
    for job in jobs:
        job.join(20)
        assert job.exitcode == 0
    assert sorted([queue.get(timeout=2), queue.get(timeout=2)]) == ["exists", "ok"]
    assert path.read_bytes() == b"same"


def test_failed_save_keeps_previous(tmp_path, monkeypatch):
    import os

    store = Store(tmp_path)
    base = store.snapshot(resources(), reason="基线")

    def fail(*args):
        raise OSError("injected disk failure")

    monkeypatch.setattr(os, "link", fail)
    with pytest.raises(OSError):
        store.snapshot(resources(prompt="变化"), reason="修改")
    assert store.load(base["id"]) == base


def test_directory_links_extra_files_and_credentials_rejected(tmp_path):
    store = Store(tmp_path / "store")
    source = tmp_path / "resources"
    source.mkdir()
    (source / "prompt.txt").write_text("提取票号", encoding="utf-8")
    (source / "mock.json").write_text('{"strategy":"baseline"}', encoding="utf-8")
    assert store.from_directory(source, reason="受控文件")
    (source / "schema.json").write_text("{}")
    with pytest.raises(SnapshotError):
        store.from_directory(source, reason="额外文件")
    (source / "schema.json").unlink()
    (source / "prompt.txt").unlink()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    import os

    if os.name == "nt":
        # Windows junctions need no SeCreateSymbolicLinkPrivilege.
        import _winapi

        outside_dir = tmp_path / "outside-dir"
        outside_dir.mkdir()
        _winapi.CreateJunction(str(outside_dir), str(source / "prompt.txt"))
    else:
        (source / "prompt.txt").symlink_to(outside)
    with pytest.raises(SnapshotError):
        store.from_directory(source, reason="链接越界")
    with pytest.raises(SnapshotError):
        store.snapshot(resources(prompt="API_KEY=not-a-real-key"), reason="误放凭据")


def test_snapshot_cli_has_no_database_or_network_side_effects(tmp_path, monkeypatch, capsys):
    import socket
    import sqlite3
    from tools.ai_inference_lab.rsi.__main__ import main

    def forbidden(*a, **kw):
        pytest.fail("offline CLI used I/O service")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    store = Store(tmp_path)
    base = store.snapshot(resources(), reason="CLI")
    assert main(["--store", str(tmp_path), "inspect", base["id"]]) == 0
    assert base["id"] in capsys.readouterr().out


def test_loaded_snapshot_rejects_invalid_reason_even_if_content_hash_matches(tmp_path):
    store = Store(tmp_path)
    base = store.snapshot(resources(), reason="说明")
    path = tmp_path / (base["id"] + ".json")
    base["reason"] = True
    path.write_text(json.dumps(base), encoding="utf-8")
    with pytest.raises(SnapshotError):
        store.load(base["id"])
