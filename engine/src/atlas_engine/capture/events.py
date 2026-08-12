import time
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class RecordedAction:
    index: int
    kind: Literal["click", "fill", "navigate"]
    selector_candidates: list[dict]
    value: str | None
    url_before: str
    url_after: str
    dom_before: str
    dom_after: str
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))
