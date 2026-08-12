import os

from atlas_engine.llm.client import AzureOpenAiClient, LlmClient


def create_llm_client() -> LlmClient | None:
    """Reads Azure OpenAI config from the environment; returns None (no
    repair capability — an off-track step stays off-track) if unconfigured.
    Reads fresh on every call (no cached singleton) so tests can
    monkeypatch the environment freely. Kept provider-agnostic in shape
    even though only Azure is implemented today (architecture spec §11)."""
    endpoint = os.environ.get("ATLAS_AZURE_OPENAI_ENDPOINT")
    api_key = os.environ.get("ATLAS_AZURE_OPENAI_API_KEY")
    model = os.environ.get("ATLAS_AZURE_OPENAI_MODEL")

    if not (endpoint and api_key and model):
        return None

    return AzureOpenAiClient(endpoint=endpoint, api_key=api_key, deployment=model)
