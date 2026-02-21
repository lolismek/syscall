from __future__ import annotations

import json
import unittest
from unittest import mock

from kernelswarm.nemotron import NemotronClient, NemotronConfig


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class NemotronTests(unittest.TestCase):
    def test_chat_json_parses_strict_payload_and_usage(self) -> None:
        response = {
            "model": "nvidia/nemotron-3-nano-30b-a3b",
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "reject": False,
                                "params_patch": {"unroll": 4},
                                "launch_patch": {"block_size": 256},
                            }
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        with mock.patch("kernelswarm.nemotron.request.urlopen", return_value=_FakeHTTPResponse(response)):
            client = NemotronClient(
                NemotronConfig(
                    api_key="test-key",
                    base_url="https://example.invalid/v1",
                )
            )
            result = client.chat_json(system_prompt="s", user_prompt="u")

        self.assertIn("params_patch", result.payload)
        self.assertEqual(result.usage.total_tokens, 15)
        self.assertEqual(result.usage.model, "nvidia/nemotron-3-nano-30b-a3b")

    def test_deepinfra_payload_omits_nvidia_only_fields(self) -> None:
        response = {
            "model": "nvidia/Nemotron-3-Nano-30B-A3B",
            "choices": [{"message": {"content": "{\"ok\": true}"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

        seen_body: dict[str, object] = {}

        def _fake_urlopen(req, timeout=None):  # type: ignore[no-untyped-def]
            del timeout
            seen_body.update(json.loads(req.data.decode("utf-8")))  # type: ignore[arg-type]
            return _FakeHTTPResponse(response)

        with mock.patch("kernelswarm.nemotron.request.urlopen", side_effect=_fake_urlopen):
            client = NemotronClient(
                NemotronConfig(
                    provider="deepinfra",
                    api_key="test-key",
                    base_url="https://api.deepinfra.com/v1/openai",
                    model="nvidia/Nemotron-3-Nano-30B-A3B",
                )
            )
            client.chat_json(system_prompt="s", user_prompt="u")

        self.assertNotIn("response_format", seen_body)
        self.assertNotIn("chat_template_kwargs", seen_body)


if __name__ == "__main__":
    unittest.main()
