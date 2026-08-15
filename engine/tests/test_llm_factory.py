from atlas_engine.llm.client import AzureOpenAiClient
from atlas_engine.llm.factory import create_llm_client

_ALL_LLM_ENV_VARS = (
    "ATLAS_AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_ENDPOINT",
    "ATLAS_AZURE_OPENAI_API_KEY", "AZURE_OPENAI_API_KEY",
    "ATLAS_AZURE_OPENAI_MODEL", "AZURE_OPENAI_MODEL",
    "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT_NAME",
)


def test_create_llm_client_reads_a_dotenv_file_when_no_real_env_vars_are_set(monkeypatch, tmp_path):
    """Users are told to keep Azure OpenAI credentials in a local .env file
    (see engine/.env.example) — create_llm_client must actually pick them up
    from there, not just from real exported shell env vars."""
    for var in _ALL_LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    (tmp_path / ".env").write_text(
        "ATLAS_AZURE_OPENAI_ENDPOINT=https://dotenv-example.openai.azure.com\n"
        "ATLAS_AZURE_OPENAI_API_KEY=dotenv-fake-key\n"
        "ATLAS_AZURE_OPENAI_MODEL=gpt-4o-mini\n"
    )
    monkeypatch.chdir(tmp_path)

    client = create_llm_client()

    assert isinstance(client, AzureOpenAiClient)
    assert client.endpoint == "https://dotenv-example.openai.azure.com"
    assert client.deployment == "gpt-4o-mini"


def test_create_llm_client_returns_none_when_unconfigured(monkeypatch, tmp_path):
    for var in _ALL_LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # Also isolate from any real engine/.env on this machine (create_llm_client
    # loads one via find_dotenv(usecwd=True)) — this test's "unconfigured"
    # premise must hold regardless of the developer's own local .env file.
    monkeypatch.chdir(tmp_path)

    assert create_llm_client() is None


def test_create_llm_client_returns_azure_client_when_configured(monkeypatch):
    monkeypatch.setenv("ATLAS_AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("ATLAS_AZURE_OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("ATLAS_AZURE_OPENAI_MODEL", "gpt-4o-mini")

    client = create_llm_client()

    assert isinstance(client, AzureOpenAiClient)
    assert client.endpoint == "https://example.openai.azure.com"
    assert client.deployment == "gpt-4o-mini"
