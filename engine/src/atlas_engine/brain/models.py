from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TrajectoryStep:
    """One recorded post-login action. `selector_candidates`/`value`/`kind`
    are HOW the human did it; `identity` + the `ref_shot_*` paths are WHAT it
    looked like in the reference replay; `expected_effect` + the `expects_*`
    flags are WHAT the recording says it should do — the brain's ground truth
    for deciding on-track vs off-track. The remaining fields are the knobs the
    brain turns when a step is off-track (wait ceiling, force click, press
    key, click coordinates)."""
    index: int
    kind: str
    selector_candidates: list[dict]
    value: str | None
    expected_effect: Literal["dom_changed", "url_changed", "none"]
    identity: dict | None = None
    ref_shot_before: str | None = None
    ref_shot_after: str | None = None
    expects_popup: bool = False
    expects_download: bool = False
    wait_ceiling_ms: int | None = None
    force_click: bool = False
    key: str | None = None
    x: float | None = None
    y: float | None = None


@dataclass(frozen=True)
class ReferenceTrajectory:
    steps: tuple[TrajectoryStep, ...]
