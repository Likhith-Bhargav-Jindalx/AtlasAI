import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, find_dotenv

# Certification timeout per run — real portal golden scripts can take 2-3
# minutes (report generation, download waits, slow SPAs) for a simple flow,
# but a real multi-popup eBO/reports-style flow (open report in a new tab,
# fill a date-range iframe, generate, download) has been observed taking
# 13-15 real minutes end to end while still completing correctly -- 300s
# was failing those with a false "timed out" every time, not because
# anything was actually broken. 1200s (20 min) gives real headroom above
# the observed worst case while still bounding a genuinely hung/broken
# script eventually.
_CERTIFY_TIMEOUT_SECONDS = 1200

# How often the Popen-polling loop in certify_golden_script_once checks
# stop_event while a certify subprocess is running. Small enough that a
# Stop click takes effect promptly; large enough not to busy-loop.
_STOP_POLL_INTERVAL_SECONDS = 0.5


class CertifyCancelled(Exception):
    """Raised by certify_golden_script_once when stop_event fires while a
    certify subprocess is running. Callers (run_certify_phase) must not
    record this as a failed CertificationRun -- it was stopped on
    purpose, not broken."""


def _certify_env() -> dict[str, str]:
    """Explicit subprocess environment: the parent's os.environ (which may
    already carry SECRET_* vars if create_llm_client ran) merged with the
    engine's .env file, found deterministically from the package root rather
    than the CWD. Real exported env vars win (dotenv_values only fills keys
    that aren't already set). Login username/password are baked into the
    script itself now (as --username/--password argparse defaults), not
    read from the environment — this still matters for the optional
    email-OTP and security-question handlers, which do read their
    credentials/answers via os.environ.get(...) at runtime."""
    env = dict(os.environ)
    dotenv_path = find_dotenv(usecwd=True)
    if not dotenv_path:
        candidate = Path(__file__).resolve().parents[3] / ".env"
        if candidate.exists():
            dotenv_path = str(candidate)
    if dotenv_path:
        for key, value in dotenv_values(dotenv_path).items():
            if value is not None and key not in env:
                env[key] = value
    return env


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
            timeout=_CERTIFY_TIMEOUT_SECONDS,
            env=_certify_env(),
        )
        if result.returncode == 0:
            succeeded += 1
    return CertificationResult(runs=runs, succeeded=succeeded)


def certify_golden_script_once(
    script_path: Path, *, args: list[str] | None = None, diagnostics_dir: Path | None = None,
    stop_event: threading.Event | None = None,
) -> tuple[bool, str | None, dict | None]:
    """Runs a golden script once from a fresh process. Unlike
    certify_golden_script (which only returns an aggregate pass count),
    this exposes per-run failure evidence — the authoring orchestrator
    needs one CertificationRun row per attempt, each with its own
    evidence when it fails. The third element is the script's parsed
    stdout JSON result (fileCount/filesTransferred/extractions/
    downloaded_files) — None if the run failed or its stdout wasn't valid
    JSON (never turns a passing run into a failure over a reporting-only
    parse issue).

    `diagnostics_dir` is deliberately optional because this public helper is
    also useful for arbitrary Python scripts. The authoring pipeline supplies
    it for generated goldens so every fresh certification process writes its
    step screenshot/HTML evidence to an isolated directory, just as the old
    app's frozen-run runner did.

    `stop_event`, when given, is polled every _STOP_POLL_INTERVAL_SECONDS
    while the subprocess runs -- a plain subprocess.run(timeout=...) blocks
    the calling thread uninterruptibly for up to _CERTIFY_TIMEOUT_SECONDS
    (20 minutes), during which a Stop click from the UI had no way to take
    effect until this call returned on its own. Raises CertifyCancelled
    (not a return value) when stopped this way, so run_certify_phase's
    loop can distinguish "stopped on purpose" from an actual failure and
    skip recording a failed CertificationRun for it."""
    command_args = list(args or [])
    if diagnostics_dir is not None and "--diagnostics-dir" not in command_args:
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        command_args.extend(["--diagnostics-dir", str(diagnostics_dir)])

    process = subprocess.Popen(
        [sys.executable, str(script_path), *command_args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_certify_env(),
    )
    deadline = time.monotonic() + _CERTIFY_TIMEOUT_SECONDS
    stdout = stderr = None
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise CertifyCancelled()
            try:
                stdout, stderr = process.communicate(timeout=_STOP_POLL_INTERVAL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    process.kill()
                    stdout, stderr = process.communicate(timeout=5)
                    return False, f"timed out after {_CERTIFY_TIMEOUT_SECONDS}s\n{stdout}{stderr}", None
                continue
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    if process.returncode == 0:
        parsed = None
        try:
            parsed = json.loads((stdout or "").strip())
        except Exception:
            pass
        return True, None, parsed
    return False, (stdout or "") + (stderr or ""), None
