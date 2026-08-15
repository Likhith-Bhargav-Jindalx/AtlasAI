"""Tests for AzureBlobRunStore, run against Azurite (the free local Azure
Storage emulator started via `docker compose -f infra/docker-compose.dev.yml
up -d azurite`). Skipped automatically if Azurite isn't reachable, so the
rest of the suite stays offline-friendly.
"""

import uuid

import pytest

from atlas_engine.storage.azure_blob import AzureBlobRunStore

_AZURITE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)


def _store() -> AzureBlobRunStore:
    container = f"atlas-runs-test-{uuid.uuid4().hex[:8]}"
    try:
        store = AzureBlobRunStore(connection_string=_AZURITE_CONNECTION_STRING, container=container)
    except Exception as exc:
        pytest.skip(f"Azurite not reachable: {exc}")
    return store


def test_write_and_read_json_round_trips():
    store = _store()
    run = store.new_run_dir(prefix="recordings")

    blob_ref = run.write_json("trajectory.json", {"steps": [1, 2, 3]})

    assert blob_ref == f"{run.blob_ref}/trajectory.json"
    assert run.read_json("trajectory.json") == {"steps": [1, 2, 3]}
    assert store.read_json(blob_ref) == {"steps": [1, 2, 3]}


def test_write_bytes_round_trips():
    store = _store()
    run = store.new_run_dir(prefix="recordings")

    blob_ref = run.write_bytes("video.webm", b"webm-bytes")

    assert run.read_bytes("video.webm") == b"webm-bytes"
    assert store.read_bytes(blob_ref) == b"webm-bytes"


def test_write_text_writes_locally_and_uploads():
    store = _store()
    run = store.new_run_dir(prefix="recordings")

    blob_ref = run.write_text("codegen.py", "print('hi')")

    assert (run.path / "codegen.py").read_text() == "print('hi')"
    assert store.read_text(blob_ref) == "print('hi')"


def test_run_dir_upload_pushes_a_locally_written_file_to_blob_storage():
    """Mirrors how golden.py/trajectory.json/codegen.py get written today:
    a plain Path.write_text() into run.path, needed locally (certify runs
    it as a real subprocess), then explicitly uploaded."""
    store = _store()
    run = store.new_run_dir(prefix="goldens")
    (run.path / "golden.py").write_text("print('golden')")

    blob_ref = run.upload("golden.py")

    assert blob_ref == f"{run.blob_ref}/golden.py"
    assert store.read_text(blob_ref) == "print('golden')"


def test_store_level_read_raises_file_not_found_for_missing_blob_ref():
    store = _store()
    with pytest.raises(FileNotFoundError):
        store.read_text("recordings/does-not-exist/trajectory.json")


def test_local_path_for_downloads_to_a_real_local_file():
    store = _store()
    run = store.new_run_dir(prefix="goldens")
    ref = run.write_bytes("golden.py", b"print('x')")

    resolved = store.local_path_for(ref)

    assert resolved.exists()
    assert resolved.read_bytes() == b"print('x')"


def test_exists_is_false_for_missing_and_empty_blobs_true_for_real_ones():
    store = _store()
    run = store.new_run_dir(prefix="recordings")
    ref = run.write_text("codegen.py", "print('hi')")
    empty_ref = run.write_bytes("empty.py", b"")

    assert store.exists(ref) is True
    assert store.exists(empty_ref) is False
    assert store.exists(f"{run.blob_ref}/missing.py") is False


def test_store_level_write_text_and_write_bytes_at_an_arbitrary_blob_ref():
    store = _store()

    store.write_text("recordings/abc/codegen.py", "print('cached')")
    store.write_bytes("recordings/abc/thumb.png", b"\x89PNG")

    assert store.read_text("recordings/abc/codegen.py") == "print('cached')"
    assert store.read_bytes("recordings/abc/thumb.png") == b"\x89PNG"


def test_url_for_returns_a_sas_url():
    store = _store()
    run = store.new_run_dir(prefix="recordings")
    ref = run.write_bytes("video.webm", b"x")

    url = store.url_for(ref)

    assert url is not None
    assert ref.split("/")[-1] in url
    assert "sig=" in url
