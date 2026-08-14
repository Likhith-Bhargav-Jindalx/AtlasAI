import base64
import json
import re
from typing import Any, Protocol

import httpx


class LlmClient(Protocol):
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        images: list[dict | bytes] | None = None,
        json_mode: bool = True,
        temperature: float = 0.0,
        max_tokens: int | None = 700,
        model: str | None = None,
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
        system: str | None = None,
        images: list[dict | bytes] | None = None,
        json_mode: bool = True,
        temperature: float = 0.0,
        max_tokens: int | None = 700,
        model: str | None = None,
    ) -> dict:
        self.calls.append(
            {
                "prompt": prompt,
                "system": system,
                "images": images,
                "json_mode": json_mode,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "model": model,
            }
        )
        return self._responses.pop(0)


class AzureOpenAiClient:
    """Azure OpenAI implementation of LlmClient — faithful port of legacy
    AzureOpenAiClient.ts."""

    def __init__(self, endpoint: str, api_key: str, deployment: str, api_version: str = "2024-10-21"):
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.deployment = deployment
        self.api_version = api_version

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        images: list[dict | bytes] | None = None,
        json_mode: bool = True,
        temperature: float = 0.0,
        max_tokens: int | None = 700,
        model: str | None = None,
    ) -> dict:
        user_content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for item in images or []:
            if isinstance(item, dict):
                img_bytes = item.get("bytes")
                detail = item.get("detail", "auto")
            else:
                img_bytes = item
                detail = "auto"

            if img_bytes:
                b64 = base64.b64encode(img_bytes).decode("ascii")
                img_entry: dict[str, Any] = {"url": f"data:image/png;base64,{b64}"}
                if detail:
                    img_entry["detail"] = detail
                user_content.append({"type": "image_url", "image_url": img_entry})

        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_content})

        target_model = model or self.deployment
        url = (
            f"{self.endpoint}/openai/deployments/{target_model}/chat/completions"
            f"?api-version={self.api_version}"
        )

        payload: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens or 4096,
        }

        # gpt-5 reasoning models reject a non-default temperature and use reasoning_effort instead
        is_reasoning = target_model.startswith("gpt-5") and "chat" not in target_model
        if is_reasoning:
            payload["reasoning_effort"] = "low"
        else:
            payload["temperature"] = temperature

        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        response = httpx.post(url, headers={"api-key": self.api_key}, json=payload, timeout=60.0)
        response.raise_for_status()
        raw_text = response.json()["choices"][0]["message"]["content"]
        # Clean potential markdown wrappers
        cleaned = re.sub(r"```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE)
        cleaned = cleaned.replace("```", "").strip()
        return json.loads(cleaned)

