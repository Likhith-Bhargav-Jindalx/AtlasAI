import re
from dataclasses import dataclass

from atlas_engine.brain.models import ReferenceTrajectory


@dataclass(frozen=True)
class SecretParam:
    step_index: int
    label: str
    env_var: str


@dataclass(frozen=True)
class CliParam:
    step_index: int
    cli_name: str
    default_value: str


@dataclass(frozen=True)
class ClassifiedParams:
    secrets: tuple[SecretParam, ...]
    params: tuple[CliParam, ...]


def _is_password_step(step) -> bool:
    """Same heuristic portal_profile/infer.py::_is_password_action already
    uses to find the login step — password fields are near-universally
    implemented with an id/name/type containing "password". Matches both
    "fill" and "type" kinds — a password entered via press_sequentially
    (a "type"-kind step) is just as much a secret as one entered via
    fill()."""
    return step.kind in ("fill", "type") and any(
        "password" in c["selector"].lower() for c in step.selector_candidates
    )


def _env_var(portal_domain: str, label: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9]+", "_", portal_domain).strip("_").upper()
    return f"SECRET_{sanitized}_{label.upper()}"


def _cli_name(step) -> str:
    """Derives a kebab-case CLI flag name from a step's selector — strips
    the leading '#'/'.', splits camelCase, replaces non-alphanumerics."""
    raw = step.selector_candidates[0]["selector"] if step.selector_candidates else f"field{step.index}"
    raw = raw.lstrip("#.")
    # Split camelCase boundaries (reportStartDate2 -> report Start Date2)
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", raw)
    kebab = re.sub(r"[^A-Za-z0-9]+", "-", spaced).strip("-").lower()
    return kebab or f"field-{step.index}"


def classify_step_values(trajectory: ReferenceTrajectory, portal_domain: str) -> ClassifiedParams:
    """Decides, for every fill/type step in the trajectory, whether its
    recorded value should be (a) a secret — never baked into the generated
    script, read from an env var at runtime — or (b) a plain CLI parameter
    — baked in as an overridable default. EVERY password-matching step
    (fill or type) is classified "password", all sharing the identical env
    var (a "confirm password" field should get the same value as the
    first); the fill immediately preceding the FIRST password step (no
    other step in between) is classified "username" — that special case is
    scoped to exactly the first password step, never a later one. Every
    other fill/type step becomes a CLI param."""
    steps = list(trajectory.steps)
    password_indices = [i for i, s in enumerate(steps) if _is_password_step(s)]

    secrets: list[SecretParam] = []
    secret_indices: set[int] = set()
    if password_indices:
        password_env_var = _env_var(portal_domain, "password")
        for password_index in password_indices:
            password_step = steps[password_index]
            secrets.append(SecretParam(
                step_index=password_step.index, label="password",
                env_var=password_env_var,
            ))
            secret_indices.add(password_index)

        first_password_index = password_indices[0]
        if first_password_index > 0 and steps[first_password_index - 1].kind == "fill":
            username_step = steps[first_password_index - 1]
            secrets.append(SecretParam(
                step_index=username_step.index, label="username",
                env_var=_env_var(portal_domain, "username"),
            ))
            secret_indices.add(first_password_index - 1)

    params = [
        CliParam(step_index=step.index, cli_name=_cli_name(step), default_value=step.value or "")
        for i, step in enumerate(steps)
        if step.kind in ("fill", "type") and i not in secret_indices
    ]

    return ClassifiedParams(secrets=tuple(secrets), params=tuple(params))
