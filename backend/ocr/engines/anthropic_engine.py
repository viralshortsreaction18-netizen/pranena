"""Anthropic Claude engine.

Uses the Messages API with the native JSON-schema structured output
(``output_config.format``) which is generally available on the Claude
Developer Platform.  Note that *forced* tool use returns HTTP 400 on
Opus 5.5 / Sonnet 5.5, so we deliberately use native JSON mode instead.
"""

from __future__ import annotations

import httpx

from ...schemas import EngineSpec
from ..base import BaseEngine, EngineError, parse_json_loose
from ..imageprep import PrepResult
from ..schema import EXTRACTION_SCHEMA, VERIFY_SCHEMA

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicEngine(BaseEngine):
    id = "anthropic"

    def __init__(self, spec: EngineSpec):
        super().__init__(spec)
        if not self.api_key:
            raise EngineError("Anthropic API key is missing")
        self.base_url = self.base_url or "https://api.anthropic.com"

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key or "",
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def _body(
        self,
        prompt: str,
        prep: PrepResult,
        schema: dict,
        extra_payload: list[dict] | None,
    ) -> dict:
        content: list[dict] = [{"type": "text", "text": prompt}]
        if extra_payload is not None:
            import json as _json

            content.append({
                "type": "text",
                "text": "CURRENT TRANSCRIPTION (JSON):\n"
                        + _json.dumps(extra_payload, ensure_ascii=False, indent=2),
            })
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": prep.mime,
                "data": self._b64(prep),
            },
        })
        return {
            "model": self.model,
            "max_tokens": self.spec.max_tokens,
            "temperature": self.spec.temperature,
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
            "messages": [{"role": "user", "content": content}],
        }

    async def _request_json(
        self,
        prompt: str,
        prep: PrepResult,
        *,
        schema_kind: str = "extraction",
        extra_payload: list[dict] | None = None,
    ) -> dict:
        schema = EXTRACTION_SCHEMA if schema_kind == "extraction" else VERIFY_SCHEMA
        body = self._body(prompt, prep, schema, extra_payload)

        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=20.0)) as client:
            resp = await client.post(
                f"{self.base_url}/v1/messages",
                headers=self._headers(),
                json=body,
            )
            if resp.status_code >= 400:
                # Fall back to a plain JSON prompt if the model or account does
                # not support native structured output.
                if resp.status_code == 400 and "output_config" in resp.text:
                    body.pop("output_config", None)
                    body["system"] = (
                        "You return a single JSON object and nothing else. "
                        "Follow this schema exactly:\n"
                        + __import__("json").dumps(schema, ensure_ascii=False)
                    )
                    resp = await client.post(
                        f"{self.base_url}/v1/messages",
                        headers=self._headers(),
                        json=body,
                    )
                if resp.status_code >= 400:
                    raise EngineError(f"HTTP {resp.status_code}: {resp.text[:500]}")
            data = resp.json()

        text_parts: list[str] = []
        for item in data.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text") or "")
        return parse_json_loose("\n".join(text_parts))
