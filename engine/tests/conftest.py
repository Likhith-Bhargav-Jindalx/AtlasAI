"""Shared pytest fixtures for the atlas_engine test suite.

Environment variables that configure atlas_engine.config.settings must be
set here, at module scope, before anything below imports atlas_engine —
pydantic-settings reads the environment once, at Settings() construction
time, so setting them inside a test function (even via monkeypatch) is too
late once another test module has already triggered the import.
"""

import os

os.environ.setdefault(
    "ATLAS_DATABASE_URL",
    "postgresql+psycopg://atlas:atlas_dev_password@localhost:5434/atlas_test",
)
os.environ.setdefault("ATLAS_RUN_ROOT", "/tmp/atlas-engine-test-runs")

# The emitted golden script's login block now genuinely requires SECRET_*
# env vars at runtime (Task 11) instead of baking in the recorded literal
# values. Several production entry points that emit/certify a golden script
# (authoring_service.author_from_recording, verify.heal.heal_golden_script,
# and the CLI's author-from-actions command, all outside this task's own
# file scope) call emit_golden_script without an explicit portal_domain, so
# it defaults to "unknown" -> the required names are SECRET_UNKNOWN_USERNAME
# / SECRET_UNKNOWN_PASSWORD (codegen/params.py::_env_var's sanitization).
# Every test in this suite that records a real login flow against the
# fixture portal and authors from it uses "demo"/"demo-pass" as the
# recorded values, so setting these here (module scope, unconditionally,
# same as ATLAS_DATABASE_URL/ATLAS_RUN_ROOT above) lets every such test's
# certify subprocess (which inherits this process's environment) actually
# log in, exactly as it did before Task 11.
os.environ.setdefault("SECRET_UNKNOWN_USERNAME", "demo")
os.environ.setdefault("SECRET_UNKNOWN_PASSWORD", "demo-pass")

import pytest  # noqa: E402

from atlas_engine.codegen.params import _env_var  # noqa: E402
from tests.fixtures.fixture_portal.server import FixturePortalServer  # noqa: E402

_LLM_ENV_VARS = (
    "ATLAS_AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_ENDPOINT",
    "ATLAS_AZURE_OPENAI_API_KEY", "AZURE_OPENAI_API_KEY",
    "ATLAS_AZURE_OPENAI_MODEL", "AZURE_OPENAI_MODEL",
    "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT_NAME",
)


@pytest.fixture(autouse=True)
def _no_leaked_llm_credentials(monkeypatch):
    """create_llm_client() loads a local .env via find_dotenv(usecwd=True),
    and python-dotenv writes straight into the real os.environ (unlike
    monkeypatch.setenv, that mutation is NOT auto-reverted). So the first
    test in the session that runs with CWD still at the repo root — where a
    developer's real engine/.env with real Azure credentials may live —
    permanently leaks those credentials into every test that runs after it,
    silently turning "no LLM configured" into "a real LLM is configured" for
    the rest of the suite. Clearing these before each test, via monkeypatch,
    guarantees any such leak from a prior test is undone before this one
    starts, and is itself cleanly undone after this test for the next one."""
    for var in _LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(scope="session")
def fixture_portal_server():
    server = FixturePortalServer(port=8731)
    server.start()

    # The golden script's login block now genuinely requires SECRET_* env
    # vars at runtime (Task 11) instead of baking in the recorded literal
    # values. Any test that records a real login/fill flow against this
    # fixture portal and then authors/certifies it (directly, via the
    # authoring orchestrator, the HTTP API, or the CLI) ends up with a
    # PortalProfile whose domain is derived straight from the recorded URL
    # (recording_service.py's `domain = target_url.split("/")[2]`), which
    # for this fixture portal is the literal "127.0.0.1:8731" — not
    # "unknown". Set the matching env vars once, for the whole session, so
    # every such test's certify subprocess (which inherits this process's
    # environment) can actually log in, exactly as it did before Task 11
    # when the values were baked in as literals. Set directly on
    # os.environ (not monkeypatch, which is function-scoped and would
    # revert before the next test) — mirrors this file's own
    # ATLAS_DATABASE_URL/ATLAS_RUN_ROOT pattern above.
    domain = server.base_url.split("//", 1)[1]
    os.environ[_env_var(domain, "username")] = "demo"
    os.environ[_env_var(domain, "password")] = "demo-pass"

    yield server
    server.stop()


@pytest.fixture(scope="session")
def fixture_portal_url(fixture_portal_server):
    return fixture_portal_server.base_url
