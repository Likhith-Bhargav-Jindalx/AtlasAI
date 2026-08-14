import os

from atlas_engine.llm.client import AzureOpenAiClient, LlmClient


def create_llm_client(*, usage_context: tuple[str, str | None] | None = None) -> LlmClient | None:
    """Reads Azure OpenAI config from the environment; returns None (no
    repair capability — an off-track step stays off-track) if unconfigured.
    Supports both ATLAS_AZURE_OPENAI_* and standard AZURE_OPENAI_* env vars
    for seamless parity with the previous codebase. `usage_context`, when
    given as (org_id, recording_id), records a UsageLedgerEntry per LLM call
    via the client's on_usage hook — purely additive telemetry, invisible to
    every caller inside brain/*."""
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

    on_usage = None
    if usage_context is not None:
        org_id, recording_id = usage_context
        from atlas_engine.llm.usage import record_llm_usage

        def on_usage(usage: dict) -> None:
            record_llm_usage(
                org_id, recording_id, model=usage["model"],
                input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
            )

    return AzureOpenAiClient(endpoint=endpoint, api_key=api_key, deployment=model, on_usage=on_usage)
