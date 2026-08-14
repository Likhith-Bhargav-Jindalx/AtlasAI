import json

import httpx
import pytest

from atlas_engine.llm.client import AzureOpenAiClient, FakeLlmClient


def test_fake_llm_client_returns_queued_responses_in_order():
    client = FakeLlmClient(responses=[{"action": "replace_selector", "selector": "#new"}])

    result = client.complete("diagnose step 4", images=[b"fake-png-bytes"])

    assert result == {"action": "replace_selector", "selector": "#new"}
    assert len(client.calls) == 1
    assert client.calls[0]["prompt"] == "diagnose step 4"
    assert client.calls[0]["images"] == [b"fake-png-bytes"]


def test_fake_llm_client_raises_when_exhausted():
    client = FakeLlmClient(responses=[])

    with pytest.raises(IndexError):
        client.complete("diagnose step 4")


def test_azure_openai_client_constructs_proper_messages_and_headers(monkeypatch):
    captured_request = {}

    def mock_post(url, headers=None, json=None, timeout=None):
        captured_request["url"] = url
        captured_request["headers"] = headers
        captured_request["json"] = json

        class MockResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": '```json\n{"action": "replace_selector", "selector": "#repaired-btn"}\n```'
                            }
                        }
                    ]
                }

        return MockResponse()

    monkeypatch.setattr(httpx, "post", mock_post)

    client = AzureOpenAiClient(
        endpoint="https://myportal.openai.azure.com",
        api_key="secret-key-123",
        deployment="gpt-4o-mini",
    )

    response = client.complete(
        "User diagnostic prompt",
        system="System prompt here",
        images=[
            {"bytes": b"live-img", "detail": "high"},
            {"bytes": b"ref-img", "detail": "low"},
        ],
        json_mode=True,
        temperature=0.0,
    )

    assert response == {"action": "replace_selector", "selector": "#repaired-btn"}
    assert captured_request["headers"]["api-key"] == "secret-key-123"
    assert "https://myportal.openai.azure.com/openai/deployments/gpt-4o-mini/chat/completions" in captured_request["url"]

    payload = captured_request["json"]
    assert payload["messages"][0] == {"role": "system", "content": "System prompt here"}
    user_msg = payload["messages"][1]
    assert user_msg["role"] == "user"
    assert user_msg["content"][0] == {"type": "text", "text": "User diagnostic prompt"}
    assert user_msg["content"][1]["type"] == "image_url"
    assert user_msg["content"][1]["image_url"]["detail"] == "high"
    assert user_msg["content"][2]["image_url"]["detail"] == "low"
    assert payload["response_format"] == {"type": "json_object"}


def test_azure_client_invokes_on_usage_with_token_counts(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 45},
            }

    monkeypatch.setattr("httpx.post", lambda *a, **k: FakeResponse())

    client = AzureOpenAiClient(
        endpoint="https://x", api_key="k", deployment="gpt-4o",
        on_usage=lambda usage: captured.update(usage),
    )
    client.complete("prompt", json_mode=True)

    assert captured == {"model": "gpt-4o", "input_tokens": 120, "output_tokens": 45}


def test_azure_client_on_usage_never_raises_on_malformed_usage(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}]}  # no "usage" key at all

    monkeypatch.setattr("httpx.post", lambda *a, **k: FakeResponse())

    def blow_up(usage):
        raise RuntimeError("should never propagate")

    client = AzureOpenAiClient(endpoint="https://x", api_key="k", deployment="gpt-4o", on_usage=blow_up)
    result = client.complete("prompt", json_mode=True)
    assert result == {}


def test_azure_client_without_on_usage_is_unaffected(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr("httpx.post", lambda *a, **k: FakeResponse())

    client = AzureOpenAiClient(endpoint="https://x", api_key="k", deployment="gpt-4o")
    assert client.complete("prompt", json_mode=True) == {"ok": True}

