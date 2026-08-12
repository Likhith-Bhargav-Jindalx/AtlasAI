from atlas_engine.llm.client import AzureOpenAiClient
from atlas_engine.llm.factory import create_llm_client


def test_create_llm_client_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("ATLAS_AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("ATLAS_AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ATLAS_AZURE_OPENAI_MODEL", raising=False)

    assert create_llm_client() is None


def test_create_llm_client_returns_azure_client_when_configured(monkeypatch):
    monkeypatch.setenv("ATLAS_AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("ATLAS_AZURE_OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("ATLAS_AZURE_OPENAI_MODEL", "gpt-4o-mini")

    client = create_llm_client()

    assert isinstance(client, AzureOpenAiClient)
    assert client.endpoint == "https://example.openai.azure.com"
    assert client.deployment == "gpt-4o-mini"
