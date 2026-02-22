from __future__ import annotations

import http.client
import json
import logging
import os
import platform
import random
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib import request

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TCP keep-alive patch for urllib / http.client
#
# Python's urllib does not set SO_KEEPALIVE on sockets.  For long-running LLM
# inference calls (30-120s), intermediate load balancers silently drop idle
# TCP connections, causing RemoteDisconnected with no HTTP error code.
#
# We monkey-patch HTTPSConnection.connect to enable OS-level TCP keep-alive
# probes so the connection stays alive through proxies / NLBs.
# ---------------------------------------------------------------------------
_KEEPALIVE_PATCHED = False


def _patch_keepalive() -> None:
    global _KEEPALIVE_PATCHED  # noqa: PLW0603
    if _KEEPALIVE_PATCHED:
        return
    _KEEPALIVE_PATCHED = True

    _original_connect = http.client.HTTPSConnection.connect

    def _connect_with_keepalive(self: http.client.HTTPSConnection) -> None:
        _original_connect(self)
        # setsockopt works on SSLSocket directly — no unwrapping needed.
        sock = self.sock
        if sock is None:
            return
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            if platform.system() == "Darwin":
                # macOS uses TCP_KEEPALIVE (0x10) instead of TCP_KEEPIDLE.
                sock.setsockopt(socket.IPPROTO_TCP, 0x10, 30)
            else:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 6)
        except OSError:
            pass  # Non-fatal; keep-alive is best-effort.

    http.client.HTTPSConnection.connect = _connect_with_keepalive  # type: ignore[assignment]


_patch_keepalive()


DEFAULT_NEMOTRON_MODEL = os.environ.get("KERNELSWARM_NEMOTRON_MODEL", "")
DEFAULT_NVIDIA_API_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_DEEPINFRA_API_BASE_URL = "https://api.deepinfra.com/v1/openai"
DEFAULT_PROVIDER = "deepinfra"
DEFAULT_NVIDIA_API_KEY_ENV = "NVIDIA_API_KEY"
DEFAULT_DEEPINFRA_API_KEY_ENV = "DEEPINFRA_API_KEY"

_SEMAPHORE_LOCK = threading.Lock()
_SEMAPHORES: dict[tuple[str, str], threading.BoundedSemaphore] = {}
_SEMAPHORE_LIMITS: dict[tuple[str, str], int] = {}


def default_base_url(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized == "nvidia":
        return DEFAULT_NVIDIA_API_BASE_URL
    if normalized == "deepinfra":
        return DEFAULT_DEEPINFRA_API_BASE_URL
    return DEFAULT_DEEPINFRA_API_BASE_URL


def default_api_key_env(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized == "nvidia":
        return DEFAULT_NVIDIA_API_KEY_ENV
    if normalized == "deepinfra":
        return DEFAULT_DEEPINFRA_API_KEY_ENV
    return DEFAULT_DEEPINFRA_API_KEY_ENV


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
    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_NEMOTRON_MODEL
    base_url: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    timeout_s: float = 180.0
    max_concurrent_requests: int = 32

    def resolved_base_url(self) -> str:
        if self.base_url and self.base_url.strip():
            return self.base_url.strip()
        return default_base_url(self.provider)

    def resolved_api_key_env(self) -> str:
        if self.api_key_env and self.api_key_env.strip():
            return self.api_key_env.strip()
        return default_api_key_env(self.provider)

    def resolved_model(self) -> str:
        if not self.model or not self.model.strip():
            raise NemotronError(
                "Model undefined; set KERNELSWARM_NEMOTRON_MODEL in .env or provide --nemotron-model."
            )
        return self.model.strip()

    def resolved_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        key_env = self.resolved_api_key_env()
        key = os.environ.get(key_env, "").strip()
        if not key:
            raise NemotronError(
                f"Missing API key; set {key_env} or provide api_key in NemotronConfig."
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
        self.base_url = config.resolved_base_url().rstrip("/")
        limit = max(1, int(config.max_concurrent_requests))
        key = (self.base_url, config.model)
        with _SEMAPHORE_LOCK:
            semaphore = _SEMAPHORES.get(key)
            if semaphore is None or _SEMAPHORE_LIMITS.get(key) != limit:
                semaphore = threading.BoundedSemaphore(limit)
                _SEMAPHORES[key] = semaphore
                _SEMAPHORE_LIMITS[key] = limit
        self._semaphore = semaphore
        self._detected_reasoning_model = False

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
        }

        if self._supports_json_response_format():
            body["response_format"] = {"type": "json_object"}
        if self._supports_chat_template_kwargs():
            body["chat_template_kwargs"] = {"enable_thinking": mode.enable_thinking}

        # Always stream.  Long inference calls (30-120s) cause intermediate
        # load balancers to kill idle connections (RemoteDisconnected).
        # Streaming sends SSE chunks continuously, keeping the conn alive.
        body["stream"] = True

        url = f"{self.base_url}/chat/completions"
        api_key = self.config.resolved_api_key()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "kernelswarm/1.0",
            "Accept": "text/event-stream",
            "Connection": "keep-alive",
        }
        max_retries = 5
        start = time.perf_counter()
        for attempt in range(max_retries):
            try:
                req = request.Request(
                    url, method="POST",
                    data=json.dumps(body).encode("utf-8"),
                    headers=headers,
                )
                with self._semaphore:
                    with request.urlopen(req, timeout=self.config.timeout_s) as resp:
                        data = self._consume_stream(resp)
                break
            except Exception as exc:
                exc_chain = f"{type(exc).__name__}: {exc}"
                cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
                if cause:
                    exc_chain += f" <- {type(cause).__name__}: {cause}"
                    cause2 = getattr(cause, "__cause__", None) or getattr(cause, "__context__", None)
                    if cause2:
                        exc_chain += f" <- {type(cause2).__name__}: {cause2}"

                if attempt < max_retries - 1:
                    delay = (attempt + 1) * 2 + random.uniform(0, 2)
                    logger.warning(
                        "LLM request failed (attempt %d/%d): %s — retrying in %.1fs",
                        attempt + 1, max_retries, exc_chain, delay,
                    )
                    time.sleep(delay)
                else:
                    raise NemotronError(
                        f"nemotron request failed after {max_retries} attempts: {exc_chain}"
                    ) from exc
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        content = data.get("_content", "")
        if not content.strip() and data.get("_reasoning_content"):
            # Reasoning model detected at runtime — it burned all tokens on
            # reasoning_content.  Retry once with enable_thinking=false.
            if not self._detected_reasoning_model:
                self._detected_reasoning_model = True
                logger.warning(
                    "Detected reasoning model at runtime (model=%s). "
                    "Retrying with enable_thinking=false.",
                    data.get("model", "?"),
                )
                return self.chat_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    mode=mode,
                )
            logger.error(
                "LLM returned empty content even with enable_thinking=false: model=%s",
                data.get("model", "?"),
            )
        elif not content.strip():
            logger.error(
                "LLM returned empty content: model=%s, has_reasoning=%s",
                data.get("model", "?"),
                bool(data.get("_reasoning_content")),
            )
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
    def _consume_stream(resp: Any) -> dict[str, Any]:
        """Read an SSE stream and reassemble into a single response dict.

        Returns a dict with ``_content``, ``_reasoning_content``, ``model``,
        and ``usage`` keys — the same shape downstream code expects.
        """
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        model = ""
        usage: dict[str, Any] = {}
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not model:
                model = chunk.get("model", "")
            # usage is sent in the final chunk by DeepInfra.
            if "usage" in chunk:
                usage = chunk["usage"]
            choices = chunk.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            if delta.get("content"):
                content_parts.append(delta["content"])
            if delta.get("reasoning_content"):
                reasoning_parts.append(delta["reasoning_content"])
        return {
            "_content": "".join(content_parts),
            "_reasoning_content": "".join(reasoning_parts),
            "model": model,
            "usage": usage,
        }

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
        # Try full content first.
        try:
            payload = json.loads(content)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
        # Try raw_decode to grab the first valid JSON object.
        start = content.find("{")
        if start < 0:
            raise NemotronError("nemotron content does not contain a JSON object")
        decoder = json.JSONDecoder()
        try:
            payload, _ = decoder.raw_decode(content, start)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
        # Last resort: try progressively shorter slices from the last '}'.
        idx = len(content)
        while True:
            idx = content.rfind("}", start, idx)
            if idx < 0:
                break
            try:
                payload = json.loads(content[start : idx + 1])
                if isinstance(payload, dict):
                    return payload
            except json.JSONDecodeError:
                pass
        raise NemotronError("nemotron content does not contain a valid JSON object")

    def _supports_chat_template_kwargs(self) -> bool:
        # chat_template_kwargs (enable_thinking) is supported by reasoning
        # models on DeepInfra / NVIDIA.  Sending it to models that don't
        # understand the parameter (e.g. DeepSeek V3.2) causes DeepInfra to
        # drop the connection with RemoteDisconnected.
        #
        # Reasoning models that need this to control thinking output:
        #   - Nemotron-3-Nano, DeepSeek-R1, Kimi-K2.5, QwQ, etc.
        # Without enable_thinking=false, these models burn all max_tokens on
        # reasoning_content and return empty content.
        #
        # If we auto-detected a reasoning model at runtime (empty content +
        # reasoning_content present), always send it going forward.
        if self._detected_reasoning_model:
            return True
        model_lower = self.config.model.lower()
        _REASONING_INDICATORS = ("nemotron", "deepseek-r1", "kimi", "qwq", "glm")
        if any(tag in model_lower for tag in _REASONING_INDICATORS):
            provider = self.config.provider.strip().lower()
            if provider in ("nvidia", "deepinfra"):
                return True
            return "integrate.api.nvidia.com" in self.base_url
        return False

    def _supports_json_response_format(self) -> bool:
        provider = self.config.provider.strip().lower()
        if provider == "nvidia":
            return True
        if provider == "deepinfra":
            return True
        return "integrate.api.nvidia.com" in self.base_url
