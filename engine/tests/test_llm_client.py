import pytest

from atlas_engine.llm.client import FakeLlmClient


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
