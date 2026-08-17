import json
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobServiceClient, BlobSasPermissions, generate_blob_sas

# get_store() constructs a fresh AzureBlobRunStore on every call (no
# singleton), so without this, __init__'s create_container() would fire a
# real create-container request -- and log a noisy 409 ContainerAlreadyExists
# -- on every single blob read/write in a process's lifetime, not just the
# first. Keyed by container name since a process could plausibly talk to
# more than one.
_confirmed_containers: set[str] = set()


def _with_retry(func, *, attempts: int = 3, base_delay: float = 0.5):
    """Retries a transient Azure Blob Storage network call up to `attempts`
    times with exponential backoff (0.5s, 1s, 2s, ...) before giving up —
    'a wrong cached action is worse than a slow one' extends to uploads
    too: a completed-but-slow upload beats a lost recording."""
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return func()
        except Exception as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(base_delay * (2 ** attempt))
    raise last_exc


def _account_key(blob_service_client: BlobServiceClient) -> str:
    return blob_service_client.credential.account_key


@dataclass
class AzureRunDir:
    """Same shape as storage.local.RunDir: `.path` is a real local
    directory (some consumers, like certify's subprocess.run, need an
    actual file) that gets uploaded to Blob Storage as the durable copy.
    """

    path: Path
    blob_ref: str
    _store: "AzureBlobRunStore" = field(repr=False)

    def write_json(self, name: str, data: Any) -> str:
        (self.path / name).write_text(json.dumps(data, indent=2))
        return self.upload(name)

    def write_bytes(self, name: str, data: bytes) -> str:
        (self.path / name).write_bytes(data)
        return self.upload(name)

    def write_text(self, name: str, content: str) -> str:
        (self.path / name).write_text(content)
        return self.upload(name)

    def read_json(self, name: str) -> Any:
        return json.loads(self.read_text(name))

    def read_bytes(self, name: str) -> bytes:
        local = self.path / name
        if local.exists():
            return local.read_bytes()
        return self._store.read_bytes(f"{self.blob_ref}/{name}")

    def read_text(self, name: str) -> str:
        return self.read_bytes(name).decode("utf-8")

    def upload(self, name: str) -> str:
        """Uploads a file already written to `self.path / name` (directly,
        or via write_json/write_bytes) to Blob Storage. Returns the full
        blob_ref."""
        blob_ref = f"{self.blob_ref}/{name}"
        blob_client = self._store._container_client.get_blob_client(blob_ref)

        def _do_upload():
            with open(self.path / name, "rb") as fh:
                blob_client.upload_blob(fh, overwrite=True)

        _with_retry(_do_upload)
        return blob_ref


class AzureBlobRunStore:
    """Azure Blob Storage-backed implementation of the same interface as
    storage.local.LocalRunStore — a `blob_ref` is a blob name within
    `container`, the same shape LocalRunStore uses relative to its root.
    """

    def __init__(self, connection_string: str, container: str):
        self._client = BlobServiceClient.from_connection_string(connection_string)
        self._container_client = self._client.get_container_client(container)
        if container not in _confirmed_containers:
            try:
                self._container_client.create_container()
            except ResourceExistsError:
                pass
            _confirmed_containers.add(container)
        self._staging_root = Path(tempfile.mkdtemp(prefix="atlas-azure-staging-"))

    def new_run_dir(self, prefix: str) -> AzureRunDir:
        run_id = str(uuid.uuid4())
        blob_ref = f"{prefix}/{run_id}"
        path = self._staging_root / blob_ref
        path.mkdir(parents=True, exist_ok=True)
        return AzureRunDir(path=path, blob_ref=blob_ref, _store=self)

    def read_bytes(self, blob_ref: str) -> bytes:
        try:
            return self._container_client.download_blob(blob_ref).readall()
        except ResourceNotFoundError as exc:
            raise FileNotFoundError(f"No such blob: {blob_ref}") from exc

    def exists(self, blob_ref: str) -> bool:
        """True if the blob exists AND is non-empty — matches how callers
        use this (e.g. "is there a real codegen script here")."""
        blob_client = self._container_client.get_blob_client(blob_ref)
        try:
            props = blob_client.get_blob_properties()
        except ResourceNotFoundError:
            return False
        return props.size > 0

    def read_text(self, blob_ref: str) -> str:
        return self.read_bytes(blob_ref).decode("utf-8")

    def read_json(self, blob_ref: str) -> Any:
        return json.loads(self.read_text(blob_ref))

    def write_bytes(self, blob_ref: str, data: bytes) -> str:
        _with_retry(lambda: self._container_client.get_blob_client(blob_ref).upload_blob(data, overwrite=True))
        return blob_ref

    def write_text(self, blob_ref: str, content: str) -> str:
        return self.write_bytes(blob_ref, content.encode("utf-8"))

    def write_json(self, blob_ref: str, data: Any) -> str:
        return self.write_text(blob_ref, json.dumps(data))

    def local_path_for(self, blob_ref: str) -> Path:
        """Downloads the blob to a real local file — for consumers (like
        certify's subprocess.run) that need an actual path, not just
        bytes."""
        data = self.read_bytes(blob_ref)
        local_path = self._staging_root / "downloaded" / blob_ref
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(data)
        return local_path

    def url_for(self, blob_ref: str, expiry_seconds: int = 3600) -> str | None:
        """A read-only SAS URL for direct client access to this blob —
        used to redirect video serving straight to Blob Storage instead of
        proxying bytes through the app server."""
        blob_client = self._container_client.get_blob_client(blob_ref)
        sas_token = generate_blob_sas(
            account_name=self._client.account_name,
            container_name=self._container_client.container_name,
            blob_name=blob_ref,
            account_key=_account_key(self._client),
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(seconds=expiry_seconds),
        )
        return f"{blob_client.url}?{sas_token}"
