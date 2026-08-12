from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TrajectoryStep:
    index: int
    kind: str
    selector_candidates: list[dict]
    value: str | None
    expected_effect: Literal["dom_changed", "url_changed", "none"]


@dataclass(frozen=True)
class ReferenceTrajectory:
    steps: tuple[TrajectoryStep, ...]
