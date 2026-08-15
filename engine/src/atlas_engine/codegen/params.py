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
    implemented with an id/name/type containing "password"."""
    return step.kind == "fill" and any(
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
    """Decides, for every fill step in the trajectory, whether its recorded
    value should be (a) a secret — never baked into the generated script,
    read from an env var at runtime — or (b) a plain CLI parameter — baked
    in as an overridable default. Exactly one fill step (the recorded
    password field) is ever classified "password"; the fill immediately
    preceding it (no other step in between) is classified "username". Every
    other fill step becomes a CLI param."""
    steps = list(trajectory.steps)
    password_index = next((i for i, s in enumerate(steps) if _is_password_step(s)), None)

    secrets: list[SecretParam] = []
    secret_indices: set[int] = set()
    if password_index is not None:
        password_step = steps[password_index]
        secrets.append(SecretParam(
            step_index=password_step.index, label="password",
            env_var=_env_var(portal_domain, "password"),
        ))
        secret_indices.add(password_index)
        if password_index > 0 and steps[password_index - 1].kind == "fill":
            username_step = steps[password_index - 1]
            secrets.append(SecretParam(
                step_index=username_step.index, label="username",
                env_var=_env_var(portal_domain, "username"),
            ))
            secret_indices.add(password_index - 1)

    params = [
        CliParam(step_index=step.index, cli_name=_cli_name(step), default_value=step.value or "")
        for i, step in enumerate(steps)
        if step.kind == "fill" and i not in secret_indices
    ]

    return ClassifiedParams(secrets=tuple(secrets), params=tuple(params))
