import time
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class RecordedAction:
    index: int
    kind: Literal["click", "fill", "navigate", "tab_opened", "tab_closed"]
    selector_candidates: list[dict]
    value: str | None
    url_before: str
    url_after: str
    dom_before: str
    dom_after: str
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    page_index: int = 0
    operator_note: str | None = None
    modifiers: list[str] = field(default_factory=list)
    tab_origin: Literal["preexisting", "manual", "popup"] | None = None
