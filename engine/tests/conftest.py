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

import pytest  # noqa: E402

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
    server = FixturePortalServer()
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def fixture_portal_url(fixture_portal_server):
    return fixture_portal_server.base_url
