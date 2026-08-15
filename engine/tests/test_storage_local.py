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


def test_write_text_round_trips(tmp_path):
    store = LocalRunStore(root=tmp_path)
    run = store.new_run_dir(prefix="recordings")

    blob_ref = run.write_text("codegen.py", "print('hi')")

    assert blob_ref == f"{run.blob_ref}/codegen.py"
    assert (run.path / "codegen.py").read_text() == "print('hi')"


def test_read_bytes_and_read_text_on_run_dir(tmp_path):
    store = LocalRunStore(root=tmp_path)
    run = store.new_run_dir(prefix="recordings")
    (run.path / "codegen.py").write_text("print('hi')")
    (run.path / "screenshot.png").write_bytes(b"\x89PNG\r\n")

    assert run.read_text("codegen.py") == "print('hi')"
    assert run.read_bytes("screenshot.png") == b"\x89PNG\r\n"


def test_run_dir_upload_is_a_noop_for_local_store(tmp_path):
    """Local backend: the file written to run.path IS the canonical
    location already — upload() just confirms it's there and returns the
    same blob_ref, no copying."""
    store = LocalRunStore(root=tmp_path)
    run = store.new_run_dir(prefix="goldens")
    (run.path / "golden.py").write_text("print('golden')")

    blob_ref = run.upload("golden.py")

    assert blob_ref == f"{run.blob_ref}/golden.py"


def test_store_level_read_methods_resolve_an_arbitrary_blob_ref(tmp_path):
    """Loading content written by a PREVIOUS run/process — the store must
    resolve any blob_ref, not just ones from a live RunDir instance."""
    store = LocalRunStore(root=tmp_path)
    run = store.new_run_dir(prefix="recordings")
    trajectory_ref = run.write_json("trajectory.json", {"steps": []})

    fresh_store = LocalRunStore(root=tmp_path)
    assert fresh_store.read_json(trajectory_ref) == {"steps": []}
    assert fresh_store.read_text(trajectory_ref) == json.dumps({"steps": []}, indent=2)

    bytes_ref = run.write_bytes("video.webm", b"webm-bytes")
    assert fresh_store.read_bytes(bytes_ref) == b"webm-bytes"


def test_store_level_read_raises_file_not_found_for_missing_blob_ref(tmp_path):
    store = LocalRunStore(root=tmp_path)
    import pytest
    with pytest.raises(FileNotFoundError):
        store.read_text("recordings/does-not-exist/trajectory.json")


def test_local_path_for_returns_the_real_path(tmp_path):
    store = LocalRunStore(root=tmp_path)
    run = store.new_run_dir(prefix="goldens")
    ref = run.write_bytes("golden.py", b"print('x')")

    resolved = store.local_path_for(ref)

    assert resolved == run.path / "golden.py"
    assert resolved.read_bytes() == b"print('x')"


def test_url_for_returns_none_for_local_store(tmp_path):
    store = LocalRunStore(root=tmp_path)
    run = store.new_run_dir(prefix="recordings")
    ref = run.write_bytes("video.webm", b"x")

    assert store.url_for(ref) is None


def test_exists_is_false_for_missing_and_empty_blobs_true_for_real_ones(tmp_path):
    store = LocalRunStore(root=tmp_path)
    run = store.new_run_dir(prefix="recordings")
    ref = run.write_text("codegen.py", "print('hi')")
    (run.path / "empty.py").touch()

    assert store.exists(ref) is True
    assert store.exists(f"{run.blob_ref}/empty.py") is False
    assert store.exists(f"{run.blob_ref}/missing.py") is False


def test_store_level_write_text_and_write_bytes_at_an_arbitrary_blob_ref(tmp_path):
    """Used for retrofit/cache-writes to a blob_ref not created via
    new_run_dir() in this process — e.g. caching a lazily-generated
    codegen script back under an existing recording's prefix."""
    store = LocalRunStore(root=tmp_path)

    store.write_text("recordings/abc/codegen.py", "print('cached')")
    store.write_bytes("recordings/abc/thumb.png", b"\x89PNG")

    assert store.read_text("recordings/abc/codegen.py") == "print('cached')"
    assert store.read_bytes("recordings/abc/thumb.png") == b"\x89PNG"
