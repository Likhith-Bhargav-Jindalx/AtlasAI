import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CertificationResult:
    runs: int
    succeeded: int

    @property
    def is_certified(self) -> bool:
        return self.runs > 0 and self.succeeded == self.runs


def certify_golden_script(script_path: Path, *, args: list[str] | None = None, runs: int = 2) -> CertificationResult:
    """Re-runs a golden script K times from a fresh process each time to
    catch flakiness before it's marked certified."""
    succeeded = 0
    for _ in range(runs):
        result = subprocess.run(
            [sys.executable, str(script_path), *(args or [])],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            succeeded += 1
    return CertificationResult(runs=runs, succeeded=succeeded)
