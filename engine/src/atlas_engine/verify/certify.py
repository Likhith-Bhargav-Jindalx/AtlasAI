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


def certify_golden_script_once(script_path: Path, *, args: list[str] | None = None) -> tuple[bool, str | None]:
    """Runs a golden script once from a fresh process. Unlike
    certify_golden_script (which only returns an aggregate pass count),
    this exposes per-run failure evidence — the authoring orchestrator
    needs one CertificationRun row per attempt, each with its own
    evidence when it fails."""
    try:
        result = subprocess.run(
            [sys.executable, str(script_path), *(args or [])],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return False, f"timed out after 60s\n{stdout}{stderr}"
    if result.returncode == 0:
        return True, None
    return False, (result.stdout or "") + (result.stderr or "")
