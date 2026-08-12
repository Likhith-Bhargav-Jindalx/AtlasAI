import json

from atlas_engine.storage.local import LocalRunStore


def test_new_run_dir_creates_isolated_directory(tmp_path):
    store = LocalRunStore(root=tmp_path)
    run_a = store.new_run_dir(prefix="recordings")
    run_b = store.new_run_dir(prefix="recordings")

    assert run_a.path.exists()
    assert run_a.path != run_b.path
    assert run_a.blob_ref.startswith("recordings/")


def test_write_and_read_json_round_trips(tmp_path):
    store = LocalRunStore(root=tmp_path)
    run = store.new_run_dir(prefix="recordings")

    blob_ref = run.write_json("trajectory.json", {"steps": [1, 2, 3]})

    assert blob_ref == f"{run.blob_ref}/trajectory.json"
    assert run.read_json("trajectory.json") == {"steps": [1, 2, 3]}
    on_disk = json.loads((run.path / "trajectory.json").read_text())
    assert on_disk == {"steps": [1, 2, 3]}


def test_write_bytes_round_trips(tmp_path):
    store = LocalRunStore(root=tmp_path)
    run = store.new_run_dir(prefix="recordings")

    blob_ref = run.write_bytes("screenshot.png", b"\x89PNG\r\n")

    assert blob_ref == f"{run.blob_ref}/screenshot.png"
    assert (run.path / "screenshot.png").read_bytes() == b"\x89PNG\r\n"
