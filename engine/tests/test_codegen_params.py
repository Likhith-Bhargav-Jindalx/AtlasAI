from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.codegen.params import classify_step_values


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


def test_username_is_only_classified_when_it_immediately_precedes_the_password_fill():
    """A fill that happens well before the password step (e.g. a search box
    used earlier in a multi-page login) is NOT assumed to be the username —
    only the fill immediately preceding the password fill is."""
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#search", "rank": 0}], "foo", "none"),
        TrajectoryStep(1, "click", [{"selector": "#next", "rank": 0}], None, "none"),
        TrajectoryStep(2, "fill", [{"selector": "#password", "rank": 0}], "hunter2", "none"),
    ))

    result = classify_step_values(trajectory, "portal.example.com")

    labels = {s.step_index: s.label for s in result.secrets}
    assert labels == {2: "password"}  # step 0 (a click sits between it and
    # the password fill) is NOT auto-classified as username


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
