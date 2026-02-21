from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib import request


DEFAULT_NEMOTRON_MODEL = "nvidia/nemotron-3-nano-30b-a3b"
DEFAULT_NVIDIA_API_BASE_URL = "https://integrate.api.nvidia.com/v1"


class NemotronError(RuntimeError):
    pass


@dataclass(slots=True)
class NemotronMode:
    name: str
    temperature: float
    max_tokens: int
    enable_thinking: bool


FAST_MODE = NemotronMode(
    name="fast",
    temperature=0.4,
    max_tokens=800,
    enable_thinking=False,
)

DEEP_MODE = NemotronMode(
    name="deep",
    temperature=0.2,
    max_tokens=2400,
    enable_thinking=True,
)


@dataclass(slots=True)
class NemotronConfig:
    model: str = DEFAULT_NEMOTRON_MODEL
    base_url: str = DEFAULT_NVIDIA_API_BASE_URL
    api_key: str | None = None
    api_key_env: str = "NVIDIA_API_KEY"
    timeout_s: float = 60.0

    def resolved_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise NemotronError(
                f"Missing API key; set {self.api_key_env} or provide api_key in NemotronConfig."
            )
        return key


@dataclass(slots=True)
class NemotronUsage:
    mode: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str


@dataclass(slots=True)
class NemotronResult:
    payload: dict[str, Any]
    usage: NemotronUsage
    raw_text: str


class NemotronClient:
    def __init__(self, config: NemotronConfig) -> None:
        self.config = config
        self.base_url = config.base_url.rstrip("/")

    def chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        mode: NemotronMode = FAST_MODE,
    ) -> NemotronResult:
        body = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": mode.temperature,
            "max_tokens": mode.max_tokens,
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": mode.enable_thinking},
        }
        req = request.Request(
            f"{self.base_url}/chat/completions",
            method="POST",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.resolved_api_key()}",
                "Content-Type": "application/json",
            },
        )

        start = time.perf_counter()
        try:
            with request.urlopen(req, timeout=self.config.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
        except Exception as exc:  # pragma: no cover - network path
            raise NemotronError(f"nemotron request failed: {exc}") from exc
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise NemotronError(f"nemotron response is not valid JSON: {exc}") from exc

        content = self._extract_content(data)
        payload = self._extract_json_payload(content)
        usage_data = data.get("usage", {})
        usage = NemotronUsage(
            mode=mode.name,
            latency_ms=elapsed_ms,
            prompt_tokens=int(usage_data.get("prompt_tokens", 0)),
            completion_tokens=int(usage_data.get("completion_tokens", 0)),
            total_tokens=int(usage_data.get("total_tokens", 0)),
            model=str(data.get("model", self.config.model)),
        )
        return NemotronResult(payload=payload, usage=usage, raw_text=content)

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        choices = data.get("choices", [])
        if not choices:
            raise NemotronError("nemotron response missing choices")
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                elif isinstance(item, str):
                    parts.append(item)
            content = "".join(parts)
        if not isinstance(content, str):
            raise NemotronError("nemotron content is not a string")
        return content

    @staticmethod
    def _extract_json_payload(content: str) -> dict[str, Any]:
        content = content.strip()
        if not content:
            raise NemotronError("nemotron content is empty")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start < 0 or end < start:
                raise NemotronError("nemotron content does not contain a JSON object")
            payload = json.loads(content[start : end + 1])
        if not isinstance(payload, dict):
            raise NemotronError("nemotron payload is not an object")
        return payload
