from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.codegen.params import classify_step_values, _env_var, _cli_name


def test_password_step_becomes_a_secret_with_a_domain_derived_env_var():
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
        TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "hunter2", "none"),
        TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
    ))

    result = classify_step_values(trajectory, "portal.example.com")

    secrets_by_label = {s.label: s for s in result.secrets}
    assert secrets_by_label["password"].step_index == 1
    assert secrets_by_label["password"].env_var == "SECRET_PORTAL_EXAMPLE_COM_PASSWORD"
    assert secrets_by_label["username"].step_index == 0
    assert secrets_by_label["username"].env_var == "SECRET_PORTAL_EXAMPLE_COM_USERNAME"


def test_username_is_not_classified_when_a_navigation_separates_fill_and_password():
    """A fill that happens on an EARLIER page of a multi-page login (separated
    from the password step by an explicit page.goto) is NOT assumed to be the
    username — the walk-back stops at a goto, not blindly grabbing the nearest
    prior fill."""
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#search", "rank": 0}], "foo", "none"),
        TrajectoryStep(1, "goto", [{"selector": "https://x.com/login", "rank": 0}], None, "url_changed"),
        TrajectoryStep(2, "fill", [{"selector": "#password", "rank": 0}], "hunter2", "none"),
    ))

    result = classify_step_values(trajectory, "portal.example.com")

    labels = {s.step_index: s.label for s in result.secrets}
    assert labels == {2: "password"}  # step 0 was on the PREVIOUS page — not the username


def test_username_walks_back_over_focus_and_next_clicks_before_first_password_fill():
    """eCW-style two-screen login: click-username -> fill username -> click
    Next -> click password field -> fill password -> click Log In. The step
    immediately before the password fill is a CLICK, not a fill, so the
    username special-case must walk back over the intervening clicks to find
    the real username (previously missed -> login block filled a password
    field that didn't exist yet and every cert run failed). A URL-only change
    on the Next click must NOT stop the walk — two-screen logins do exactly
    that between the username and password screens."""
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "click", [{"selector": "#usernameField", "rank": 0}], None, "none"),
        TrajectoryStep(1, "fill", [{"selector": "#usernameField", "rank": 0}], "demo", "none"),
        TrajectoryStep(2, "click", [{"selector": "#next", "rank": 0}], None, "url_changed"),
        TrajectoryStep(3, "click", [{"selector": "#passwordField", "rank": 0}], None, "none"),
        TrajectoryStep(4, "fill", [{"selector": "#passwordField", "rank": 0}], "hunter2", "none"),
        TrajectoryStep(5, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
    ))

    result = classify_step_values(trajectory, "portal.example.com")

    secrets_by_label = {s.label: s for s in result.secrets}
    assert secrets_by_label["username"].step_index == 1
    assert secrets_by_label["password"].step_index == 4
    assert {p.step_index for p in result.params} == set()


def test_every_other_fill_becomes_a_kebab_case_cli_param_with_the_recorded_default():
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#password", "rank": 0}], "hunter2", "none"),
        TrajectoryStep(1, "fill", [{"selector": "#startDate", "rank": 0}], "01/01/2026", "none"),
        TrajectoryStep(2, "fill", [{"selector": "#endDate", "rank": 0}], "07/07/2026", "none"),
    ))

    result = classify_step_values(trajectory, "portal.example.com")

    params_by_index = {p.step_index: p for p in result.params}
    assert params_by_index[1].cli_name == "start-date"
    assert params_by_index[1].default_value == "01/01/2026"
    assert params_by_index[2].cli_name == "end-date"
    assert params_by_index[2].default_value == "07/07/2026"
    assert 0 not in params_by_index  # the password step is a secret, not a param


def test_no_password_step_means_no_secrets_at_all():
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "click", [{"selector": "#continue", "rank": 0}], None, "none"),
        TrajectoryStep(1, "fill", [{"selector": "#search", "rank": 0}], "foo", "none"),
    ))

    result = classify_step_values(trajectory, "portal.example.com")

    assert result.secrets == ()
    params_by_index = {p.step_index: p for p in result.params}
    assert params_by_index[1].cli_name == "search"


def test_cli_name_derivation_strips_non_alphanumerics_and_camel_case():
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#reportStartDate2", "rank": 0}], "x", "none"),
    ))

    result = classify_step_values(trajectory, "portal.example.com")

    assert result.params[0].cli_name == "report-start-date2"


# Direct unit tests for helper functions

def test_env_var_sanitizes_domain_with_dots():
    """Verify _env_var converts dots to underscores and uppercases the label."""
    assert _env_var("portal.example.com", "password") == "SECRET_PORTAL_EXAMPLE_COM_PASSWORD"
    assert _env_var("portal.example.com", "username") == "SECRET_PORTAL_EXAMPLE_COM_USERNAME"


def test_env_var_sanitizes_domain_with_hyphens_and_other_special_chars():
    """Verify _env_var converts non-alphanumeric characters to underscores."""
    assert _env_var("my-portal.site-name.org", "password") == "SECRET_MY_PORTAL_SITE_NAME_ORG_PASSWORD"
    assert _env_var("portal@example_site.com", "username") == "SECRET_PORTAL_EXAMPLE_SITE_COM_USERNAME"


def test_cli_name_plain_lowercase_selector():
    """Verify _cli_name handles plain lowercase selectors."""
    step = TrajectoryStep(0, "fill", [{"selector": "#search", "rank": 0}], "x", "none")
    assert _cli_name(step) == "search"


def test_cli_name_camel_case_selector():
    """Verify _cli_name splits camelCase into kebab-case."""
    step = TrajectoryStep(0, "fill", [{"selector": "#startDate", "rank": 0}], "x", "none")
    assert _cli_name(step) == "start-date"


def test_cli_name_selector_with_digits():
    """Verify _cli_name preserves digits and splits correctly."""
    step = TrajectoryStep(0, "fill", [{"selector": "#reportStartDate2", "rank": 0}], "x", "none")
    assert _cli_name(step) == "report-start-date2"


# ── I3: type-kind passwords, and multiple password fields ──────────────────


def test_type_kind_password_step_is_classified_as_a_secret_not_a_literal():
    """A password entered via a 'type'-kind step (press_sequentially) must be
    classified as a secret exactly like a 'fill'-kind password step — the
    previous _is_password_step only checked kind == 'fill', silently letting
    a type-kind password slip through as a literal-valued CliParam."""
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
        TrajectoryStep(1, "type", [{"selector": "#password", "rank": 0}], "hunter2", "none"),
    ))

    result = classify_step_values(trajectory, "portal.example.com")

    secrets_by_label = {s.label: s for s in result.secrets}
    assert secrets_by_label["password"].step_index == 1
    assert 1 not in {p.step_index for p in result.params}


def test_two_password_matching_steps_both_become_secrets_sharing_the_same_env_var():
    """A flow with a second password field (re-auth/confirm-password) must
    have BOTH steps classified as secrets, reading the identical env var —
    not just the first one found, and not leaking the second as a literal
    CliParam."""
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
        TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "hunter2", "none"),
        TrajectoryStep(2, "fill", [{"selector": "#confirmPassword", "rank": 0}], "hunter2", "none"),
    ))

    result = classify_step_values(trajectory, "portal.example.com")

    password_secrets = [s for s in result.secrets if s.label == "password"]
    assert {s.step_index for s in password_secrets} == {1, 2}
    assert len({s.env_var for s in password_secrets}) == 1
    assert password_secrets[0].env_var == "SECRET_PORTAL_EXAMPLE_COM_PASSWORD"
    # Neither password step should leak through as a CliParam
    assert {p.step_index for p in result.params} == set()
    # The username special-case is still scoped to only the FIRST password step
    assert {s.label for s in result.secrets} == {"username", "password"}


def test_type_kind_non_password_step_still_becomes_a_cli_param():
    """A 'type'-kind step that ISN'T a password must still be classified as a
    plain CLI param (not silently dropped) — required for C1's value_expr
    substitution to have something to point at for type-kind steps too."""
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "type", [{"selector": "#searchBox", "rank": 0}], "some query", "none"),
    ))

    result = classify_step_values(trajectory, "portal.example.com")

    assert result.secrets == ()
    assert result.params[0].cli_name == "search-box"
    assert result.params[0].default_value == "some query"
