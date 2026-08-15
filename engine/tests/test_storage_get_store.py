import pytest

from atlas_engine.storage import get_store
from atlas_engine.storage.azure_blob import AzureBlobRunStore
from atlas_engine.storage.local import LocalRunStore


def test_get_store_returns_local_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr("atlas_engine.config.settings.azure_storage_connection_string", None)
    monkeypatch.setattr("atlas_engine.config.settings.require_azure_storage", False)
    monkeypatch.setattr("atlas_engine.config.settings.run_root", tmp_path)

    store = get_store()

    assert isinstance(store, LocalRunStore)


def test_get_store_returns_azure_when_connection_string_configured(monkeypatch):
    monkeypatch.setattr(
        "atlas_engine.config.settings.azure_storage_connection_string",
        "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
        "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
        "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;",
    )
    monkeypatch.setattr("atlas_engine.config.settings.azure_storage_container", "atlas-runs-get-store-test")

    try:
        store = get_store()
    except Exception as exc:
        pytest.skip(f"Azurite not reachable: {exc}")

    assert isinstance(store, AzureBlobRunStore)


def test_get_store_raises_when_azure_required_but_not_configured(monkeypatch):
    monkeypatch.setattr("atlas_engine.config.settings.azure_storage_connection_string", None)
    monkeypatch.setattr("atlas_engine.config.settings.require_azure_storage", True)

    with pytest.raises(RuntimeError, match="ATLAS_REQUIRE_AZURE_STORAGE"):
        get_store()
