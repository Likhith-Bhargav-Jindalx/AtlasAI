import os

from atlas_engine.llm.client import AzureOpenAiClient, LlmClient


def create_llm_client() -> LlmClient | None:
    """Reads Azure OpenAI config from the environment; returns None (no
    repair capability — an off-track step stays off-track) if unconfigured.
    Supports both ATLAS_AZURE_OPENAI_* and standard AZURE_OPENAI_* env vars
    for seamless parity with the previous codebase."""
    endpoint = os.environ.get("ATLAS_AZURE_OPENAI_ENDPOINT") or os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key = os.environ.get("ATLAS_AZURE_OPENAI_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY")
    model = (
        os.environ.get("ATLAS_AZURE_OPENAI_MODEL")
        or os.environ.get("AZURE_OPENAI_MODEL")
        or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        or os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME")
    )

    if not (endpoint and api_key and model):
        return None

    return AzureOpenAiClient(endpoint=endpoint, api_key=api_key, deployment=model)
