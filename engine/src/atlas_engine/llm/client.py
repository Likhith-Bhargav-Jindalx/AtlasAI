import json
from typing import Protocol

import httpx


class LlmClient(Protocol):
    def complete(
        self,
        prompt: str,
        *,
        images: list[bytes] | None = None,
        json_mode: bool = True,
        temperature: float = 0.0,
    ) -> dict: ...


class FakeLlmClient:
    """Test double — queue canned responses, record every call made."""

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(
        self,
        prompt: str,
        *,
        images: list[bytes] | None = None,
        json_mode: bool = True,
        temperature: float = 0.0,
    ) -> dict:
        self.calls.append(
            {"prompt": prompt, "images": images, "json_mode": json_mode, "temperature": temperature}
        )
        return self._responses.pop(0)


class AzureOpenAiClient:
    """Real client — used in production, never exercised by this plan's
    automated tests (no credentials required to run the test suite)."""

    def __init__(self, endpoint: str, api_key: str, deployment: str, api_version: str = "2024-10-21"):
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.deployment = deployment
        self.api_version = api_version

    def complete(
        self,
        prompt: str,
        *,
        images: list[bytes] | None = None,
        json_mode: bool = True,
        temperature: float = 0.0,
    ) -> dict:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for image_bytes in images or []:
            import base64

            b64 = base64.b64encode(image_bytes).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})

        url = (
            f"{self.endpoint}/openai/deployments/{self.deployment}/chat/completions"
            f"?api-version={self.api_version}"
        )
        payload = {
            "messages": [{"role": "user", "content": content}],
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        response = httpx.post(url, headers={"api-key": self.api_key}, json=payload, timeout=60.0)
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]["content"]
        return json.loads(message)
