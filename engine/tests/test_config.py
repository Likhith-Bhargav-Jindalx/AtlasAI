from atlas_engine.config import Settings


def test_settings_tolerates_unrelated_atlas_prefixed_env_keys(monkeypatch, tmp_path):
    """The .env file is shared with atlas_engine.llm.factory.create_llm_client
    (ATLAS_AZURE_OPENAI_*), which Settings doesn't declare fields for.
    pydantic-settings defaults BaseSettings to extra="forbid", so any
    ATLAS_-prefixed key not matching a declared field must not crash the
    whole app at import time — it should just be ignored by Settings."""
    for var in ("ATLAS_AZURE_OPENAI_ENDPOINT", "ATLAS_AZURE_OPENAI_API_KEY", "ATLAS_AZURE_OPENAI_MODEL"):
        monkeypatch.delenv(var, raising=False)

    (tmp_path / ".env").write_text(
        "ATLAS_AZURE_OPENAI_ENDPOINT=https://example.openai.azure.com\n"
        "ATLAS_AZURE_OPENAI_API_KEY=fake-key\n"
        "ATLAS_AZURE_OPENAI_MODEL=gpt-4.1-mini\n"
    )
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    assert settings.database_url
