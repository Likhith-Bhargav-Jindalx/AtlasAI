import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class RunDir:
    path: Path
    blob_ref: str

    def write_json(self, name: str, data: Any) -> str:
        (self.path / name).write_text(json.dumps(data, indent=2))
        return f"{self.blob_ref}/{name}"

    def read_json(self, name: str) -> Any:
        return json.loads((self.path / name).read_text())

    def write_bytes(self, name: str, data: bytes) -> str:
        (self.path / name).write_bytes(data)
        return f"{self.blob_ref}/{name}"

    def write_text(self, name: str, content: str) -> str:
        (self.path / name).write_text(content)
        return f"{self.blob_ref}/{name}"

    def read_bytes(self, name: str) -> bytes:
        return (self.path / name).read_bytes()

    def read_text(self, name: str) -> str:
        return (self.path / name).read_text()

    def upload(self, name: str) -> str:
        """Confirms a file written directly to `self.path / name` (e.g. by
        a subprocess or another library that needs a real local path, like
        certify's script execution) is durably stored. Local backend: the
        file is already in its canonical location — no-op. Exists so
        callers can write local-first and call this uniformly regardless
        of backend (Azure's RunDir uploads here instead)."""
        return f"{self.blob_ref}/{name}"


class LocalRunStore:
    """On-disk stand-in for the cloud Blob Storage backend.

    A `RunDir`'s `blob_ref` is a path relative to `root` — the same shape
    a Blob Storage key will take later, so callers never need to know
    whether they're talking to local disk or Blob Storage.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def new_run_dir(self, prefix: str) -> RunDir:
        run_id = str(uuid.uuid4())
        blob_ref = f"{prefix}/{run_id}"
        path = self.root / blob_ref
        path.mkdir(parents=True, exist_ok=True)
        return RunDir(path=path, blob_ref=blob_ref)

    def _resolve(self, blob_ref: str) -> Path:
        path = self.root / blob_ref
        if not path.exists():
            raise FileNotFoundError(f"No such blob: {blob_ref}")
        return path

    def exists(self, blob_ref: str) -> bool:
        """True if the blob exists AND is non-empty — matches how callers
        use this (e.g. "is there a real codegen script here")."""
        path = self.root / blob_ref
        return path.exists() and path.stat().st_size > 0

    def read_json(self, blob_ref: str) -> Any:
        return json.loads(self._resolve(blob_ref).read_text())

    def read_bytes(self, blob_ref: str) -> bytes:
        return self._resolve(blob_ref).read_bytes()

    def read_text(self, blob_ref: str) -> str:
        return self._resolve(blob_ref).read_text()

    def write_text(self, blob_ref: str, content: str) -> str:
        path = self.root / blob_ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return blob_ref

    def write_bytes(self, blob_ref: str, data: bytes) -> str:
        path = self.root / blob_ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return blob_ref

    def write_json(self, blob_ref: str, data: Any) -> str:
        return self.write_text(blob_ref, json.dumps(data))

    def local_path_for(self, blob_ref: str) -> Path:
        """A real local filesystem path with this blob's content — for
        consumers (like certify's subprocess.run) that need an actual
        file, not just bytes. Local backend: the canonical path itself."""
        return self._resolve(blob_ref)

    def url_for(self, blob_ref: str, expiry_seconds: int = 3600) -> str | None:
        """A direct, shareable URL for this blob (e.g. a SAS URL) — None
        means "no direct URL; serve the bytes yourself", which is always
        the case for local disk."""
        return None
