"""Ollama engine - fully offline transcription with a local vision model.

Qwen3-VL-8B scores chrF++ 75.2 on real Devanagari scans, which beats GPT-5.5
(58.5) and approaches Mistral.  On a machine with a 24 GB GPU this is the
strongest privacy-preserving option.
"""

from __future__ import annotations

import httpx

from ...schemas import EngineSpec
from ..base import BaseEngine, EngineError, parse_json_loose
from ..imageprep import PrepResult
from ..schema import EXTRACTION_SCHEMA, VERIFY_SCHEMA


class OllamaEngine(BaseEngine):
    id = "ollama"

    def __init__(self, spec: EngineSpec):
        super().__init__(spec)
        self.base_url = (self.base_url or "http://127.0.0.1:11434").rstrip("/")

    async def _request_json(
        self,
        prompt: str,
        prep: PrepResult,
        *,
        schema_kind: str = "extraction",
        extra_payload: list[dict] | None = None,
    ) -> dict:
        schema = EXTRACTION_SCHEMA if schema_kind == "extraction" else VERIFY_SCHEMA
        content = prompt
        if extra_payload is not None:
            import json as _json

            content += (
                "\n\nCURRENT TRANSCRIPTION (JSON):\n"
                + _json.dumps(extra_payload, ensure_ascii=False, indent=2)
            )
        body = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": content, "images": [self._b64(prep)]}
            ],
            "stream": False,
            "format": schema,
            "options": {"temperature": self.spec.temperature},
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
            try:
                resp = await client.post(f"{self.base_url}/api/chat", json=body)
            except httpx.TransportError as exc:
                raise EngineError(
                    f"cannot reach Ollama at {self.base_url} - is it running?"
                ) from exc
            if resp.status_code >= 400:
                raise EngineError(f"HTTP {resp.status_code}: {resp.text[:500]}")
            data = resp.json()
        text = (data.get("message") or {}).get("content") or ""
        return parse_json_loose(text)
