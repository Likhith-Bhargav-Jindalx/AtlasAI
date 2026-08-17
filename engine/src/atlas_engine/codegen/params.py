import re
from dataclasses import dataclass

from atlas_engine.brain.models import ReferenceTrajectory


@dataclass(frozen=True)
class SecretParam:
    step_index: int
    label: str
    cli_name: str
    default_value: str


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
    recorded value should be (a) a secret — username/password — or (b) a
    plain CLI parameter. Both are baked in as an overridable --flag default,
    exactly the same mechanism; "secret" only affects which login-block
    generation path emit.py takes for a step, not how its value reaches the
    script. EVERY password-matching step (fill or type) is classified
    "password", all sharing the identical --password flag (a "confirm
    password" field should get the same value as the first); the fill/type
    immediately preceding the FIRST password step —
    walking back over any intervening non-fill steps (focus clicks on the
    username/password fields, "Next" buttons on two-screen logins) — is
    classified "username". That special case is scoped to exactly the
    first password step, never a later one. Every other fill/type step
    becomes a CLI param."""
    steps = list(trajectory.steps)
    password_indices = [i for i, s in enumerate(steps) if _is_password_step(s)]

    secrets: list[SecretParam] = []
    secret_indices: set[int] = set()
    if password_indices:
        for password_index in password_indices:
            password_step = steps[password_index]
            secrets.append(SecretParam(
                step_index=password_step.index, label="password",
                cli_name="password", default_value=password_step.value or "",
            ))
            secret_indices.add(password_index)

        first_password_index = password_indices[0]
        # The username is the nearest fill/type BEFORE the first password
        # step. Portals that split login into two screens (eCW) record
        # intervening actions: click-username-field -> fill username ->
        # click Next -> click password-field -> fill password. The step
        # immediately before the password fill is then a CLICK, not a fill,
        # so walk backward over non-fill steps (focus clicks, Next buttons)
        # to find the real username. The walk is bounded so an unrelated
        # fill from far back in the flow is never grabbed, and stops at an
        # explicit page.goto (a true navigation boundary). NOTE: a "Next"
        # click that merely changes the URL (expected_effect=url_changed)
        # is NOT a boundary — two-screen logins do exactly that between the
        # username and password screens.
        username_index: int | None = None
        max_walk = 4
        i = first_password_index - 1
        walked = 0
        while i >= 0 and walked < max_walk:
            s = steps[i]
            if s.kind == "goto":
                break
            if s.kind in ("fill", "type"):
                username_index = i
                break
            i -= 1
            walked += 1
        if username_index is not None:
            username_step = steps[username_index]
            secrets.append(SecretParam(
                step_index=username_step.index, label="username",
                cli_name="username", default_value=username_step.value or "",
            ))
            secret_indices.add(username_index)

    params = [
        CliParam(step_index=step.index, cli_name=_cli_name(step), default_value=step.value or "")
        for i, step in enumerate(steps)
        if step.kind in ("fill", "type") and i not in secret_indices
    ]

    return ClassifiedParams(secrets=tuple(secrets), params=tuple(params))
