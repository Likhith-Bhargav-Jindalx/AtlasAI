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
