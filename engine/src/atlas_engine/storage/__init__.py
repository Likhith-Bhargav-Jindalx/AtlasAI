from atlas_engine.config import settings
from atlas_engine.storage.azure_blob import AzureBlobRunStore, AzureRunDir
from atlas_engine.storage.local import LocalRunStore, RunDir

__all__ = ["LocalRunStore", "RunDir", "AzureBlobRunStore", "AzureRunDir", "get_store"]


def get_store() -> LocalRunStore | AzureBlobRunStore:
    """Recordings/videos/screenshots/golden scripts default to local disk —
    set ATLAS_AZURE_STORAGE_CONNECTION_STRING to store them in Azure Blob
    Storage instead. ATLAS_REQUIRE_AZURE_STORAGE=true (set in production/
    .exe builds) fails loudly instead of silently falling back to local
    disk when Azure isn't configured."""
    if settings.azure_storage_connection_string:
        return AzureBlobRunStore(
            connection_string=settings.azure_storage_connection_string,
            container=settings.azure_storage_container,
        )
    if settings.require_azure_storage:
        raise RuntimeError(
            "ATLAS_REQUIRE_AZURE_STORAGE is set but ATLAS_AZURE_STORAGE_CONNECTION_STRING is missing"
        )
    return LocalRunStore(root=settings.ensure_run_root())
