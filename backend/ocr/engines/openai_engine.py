"""OpenAI and OpenAI-compatible chat engines.

One implementation serves OpenAI, Groq, OpenRouter, Mistral chat models and any
other provider that speaks the ``/v1/chat/completions`` dialect.  Mistral's
dedicated OCR endpoint gets its own branch because it is the strongest
handwriting performer we benchmarked (88.9% internal accuracy vs 78.2% for
Azure) and it returns Markdown rather than JSON.
"""

from __future__ import annotations

import base64
import re

import httpx

from ...schemas import EngineSpec
from ..base import BaseEngine, EngineError, parse_json_loose
from ..imageprep import PrepResult
from ..schema import EXTRACTION_SCHEMA, VERIFY_SCHEMA

#: Models that reject an explicit temperature or max_tokens.
_NO_TEMPERATURE = ("gpt-5", "o1", "o3", "o4")


def _markdown_to_blocks(markdown: str) -> list[dict]:
    """Turn Mistral's Markdown output into our block schema."""
    blocks: list[dict] = []
    chunks = [c.strip() for c in re.split(r"\n\s*\n", markdown or "") if c.strip()]
    for chunk in chunks:
        # A markdown heading becomes a section / title candidate.
        m = re.match(r"^(#{1,6})\s*(.*)$", chunk, re.DOTALL)
        if m:
            text = m.group(2).strip()
            level = len(m.group(1))
            blocks.append({
                "type": "title" if level == 1 else "section",
                "text": text,
                "bbox": [0.0, 0.0, 0.0, 0.0],
                "confidence": 0.85,
                "question_number": "",
                "marks": "",
                "script": "unknown",
            })
            continue
        # A table becomes a table block.
        if "|" in chunk and re.search(r"^\s*\|?[\s:|-]+\|", chunk, re.MULTILINE):
            btype = "table"
        else:
            btype = "unknown"
        blocks.append({
            "type": btype,
            "text": chunk,
            "bbox": [0.0, 0.0, 0.0, 0.0],
            "confidence": 0.85,
            "question_number": "",
            "marks": "",
            "script": "unknown",
        })
    return blocks


class OpenAICompatibleEngine(BaseEngine):
    id = "openai"

    def __init__(self, spec: EngineSpec):
        super().__init__(spec)
        if not self.api_key:
            raise EngineError(f"{self.spec.label} API key is missing")
        self.base_url = self.base_url or "https://api.openai.com"

    # -- helpers ------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.spec.provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/pranena/ocr"
            headers["X-Title"] = "Pranena OCR"
        return headers

    def _url(self) -> str:
        base = self.base_url
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def _body(
        self,
        prompt: str,
        prep: PrepResult,
        schema: dict,
        extra_payload: list[dict] | None,
    ) -> dict:
        import json as _json

        content: list[dict] = [{"type": "text", "text": prompt}]
        if extra_payload is not None:
            content.append({
                "type": "text",
                "text": "CURRENT TRANSCRIPTION (JSON):\n"
                        + _json.dumps(extra_payload, ensure_ascii=False, indent=2),
            })
        data_url = f"data:{prep.mime};base64,{self._b64(prep)}"
        content.append({"type": "image_url", "image_url": {"url": data_url, "detail": "high"}})

        body: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "max_completion_tokens": self.spec.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "exam_paper_ocr",
                    "schema": schema,
                    "strict": False,
                },
            },
        }
        if not any(self.model.lower().startswith(p) for p in _NO_TEMPERATURE):
            body["temperature"] = self.spec.temperature
        return body

    # -- Mistral OCR branch --------------------------------------------------

    async def _mistral_ocr(self, prompt: str, prep: PrepResult) -> dict:
        url = f"{self.base_url}/v1/ocr"
        body = {
            "model": self.model,
            "document": {
                "type": "image_url",
                "image_url": f"data:{prep.mime};base64,{self._b64(prep)}",
            },
            "include_markdown": True,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=20.0)) as client:
            resp = await client.post(url, headers=self._headers(), json=body)
            if resp.status_code >= 400:
                raise EngineError(f"HTTP {resp.status_code}: {resp.text[:500]}")
            data = resp.json()

        pages = data.get("pages") or []
        blocks: list[dict] = []
        for page in pages:
            md = page.get("markdown") or ""
            for b in _markdown_to_blocks(md):
                blocks.append(b)
        return {
            "language": "mixed",
            "page_title": "",
            "blocks": blocks,
            "notes": "Transcribed via the Mistral OCR endpoint.",
        }

    # -- main ----------------------------------------------------------------

    async def _request_json(
        self,
        prompt: str,
        prep: PrepResult,
        *,
        schema_kind: str = "extraction",
        extra_payload: list[dict] | None = None,
    ) -> dict:
        if self.spec.provider == "mistral" and self.model.lower().startswith("mistral-ocr"):
            return await self._mistral_ocr(prompt, prep)

        schema = EXTRACTION_SCHEMA if schema_kind == "extraction" else VERIFY_SCHEMA
        body = self._body(prompt, prep, schema, extra_payload)

        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=20.0)) as client:
            resp = await client.post(self._url(), headers=self._headers(), json=body)

            if resp.status_code == 400:
                err = resp.text.lower()
                # max_tokens -> max_completion_tokens
                if "max_tokens" in err and "max_completion_tokens" in err:
                    body.pop("max_tokens", None)
                    resp = await client.post(self._url(), headers=self._headers(), json=body)
                # json_schema not supported -> plain JSON object mode
                elif "json_schema" in err or "response_format" in err:
                    body["response_format"] = {"type": "json_object"}
                    resp = await client.post(self._url(), headers=self._headers(), json=body)
                # temperature not supported -> drop it
                elif "temperature" in err:
                    body.pop("temperature", None)
                    resp = await client.post(self._url(), headers=self._headers(), json=body)

            if resp.status_code >= 400:
                raise EngineError(f"HTTP {resp.status_code}: {resp.text[:500]}")
            data = resp.json()

        choices = data.get("choices") or []
        if not choices:
            raise EngineError(f"no choices returned: {str(data)[:300]}")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):  # some gateways return content parts
            content = "\n".join(
                c.get("text", "") for c in content if isinstance(c, dict)
            )
        return parse_json_loose(content or "")
