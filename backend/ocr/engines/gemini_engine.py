"""Google Gemini engine (Google AI Studio / Gemini Developer API).

Gemini is the best zero-cost starting point: the Flash family has a free
tier, no card required, and it scores at the top of the leaderboard for
*real* (not synthetic) Devanagari scans.
"""

from __future__ import annotations

import httpx

from ...schemas import EngineSpec
from ..base import BaseEngine, EngineError, parse_json_loose
from ..imageprep import PrepResult
from ..schema import EXTRACTION_SCHEMA, VERIFY_SCHEMA, clean_for_gemini


class GeminiEngine(BaseEngine):
    id = "gemini"

    def __init__(self, spec: EngineSpec):
        super().__init__(spec)
        if not self.api_key:
            raise EngineError("Gemini API key is missing")
        self.base_url = self.base_url or "https://generativelanguage.googleapis.com"

    def _endpoint(self) -> str:
        return (
            f"{self.base_url}/v1beta/models/{self.model}:generateContent"
            f"?key={self.api_key}"
        )

    def _body(
        self,
        prompt: str,
        prep: PrepResult,
        schema: dict,
        extra_payload: list[dict] | None,
    ) -> dict:
        parts: list[dict] = []
        if extra_payload is not None:
            import json as _json

            parts.append({
                "text": "CURRENT TRANSCRIPTION (JSON):\n"
                        + _json.dumps(extra_payload, ensure_ascii=False, indent=2)
            })
        parts.append({"text": prompt})
        parts.append({
            "inline_data": {"mime_type": prep.mime, "data": self._b64(prep)}
        })
        return {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": self.spec.temperature,
                "maxOutputTokens": self.spec.max_tokens,
                "responseMimeType": "application/json",
                "responseSchema": clean_for_gemini(schema),
            },
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
            resp = await client.post(self._endpoint(), json=body)
            if resp.status_code >= 400:
                # Some models reject responseSchema; retry with JSON mime only.
                if resp.status_code == 400:
                    body["generationConfig"].pop("responseSchema", None)
                    resp = await client.post(self._endpoint(), json=body)
                if resp.status_code >= 400:
                    raise EngineError(f"HTTP {resp.status_code}: {resp.text[:500]}")
            data = resp.json()

        candidates = data.get("candidates") or []
        if not candidates:
            raise EngineError(f"no candidates returned: {str(data)[:300]}")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "\n".join(p.get("text", "") for p in parts if isinstance(p, dict))
        return parse_json_loose(text)
