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
