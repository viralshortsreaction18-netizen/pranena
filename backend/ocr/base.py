"""Base classes and shared plumbing for the OCR engines."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..schemas import EngineSpec
from .imageprep import PrepResult

log = logging.getLogger("pranena.ocr")

DEFAULT_TIMEOUT = httpx.Timeout(180.0, connect=20.0)


@dataclass
class RawBlock:
    type: str = "unknown"
    text: str = ""
    bbox: list[float] | None = None
    confidence: float = 0.0
    question_number: str | None = None
    marks: str | None = None
    script: str = "unknown"


@dataclass
class EngineResult:
    engine_id: str
    model: str
    language: str = "mixed"
    page_title: str = ""
    blocks: list[RawBlock] = field(default_factory=list)
    notes: str = ""
    error: str | None = None
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None


class EngineError(RuntimeError):
    """Raised when an engine cannot complete a request."""


class BaseEngine(ABC):
    """A vision engine that can transcribe and verify a page."""

    id: str = "base"

    def __init__(self, spec: EngineSpec):
        self.spec = spec
        self.model = spec.model
        self.api_key = spec.api_key
        self.base_url = (spec.base_url or "").rstrip("/")

    # -- public API ---------------------------------------------------------

    async def extract(self, prep: PrepResult, language: str) -> EngineResult:
        from .prompts import build_extraction_prompt

        prompt = build_extraction_prompt(language)  # type: ignore[arg-type]
        started = time.time()
        try:
            payload = await self._request_json(prompt, prep, schema_kind="extraction")
        except Exception as exc:
            return EngineResult(
                engine_id=self.id, model=self.model, language=language,
                error=f"{type(exc).__name__}: {exc}",
                latency_ms=int((time.time() - started) * 1000),
            )
        latency = int((time.time() - started) * 1000)
        blocks: list[RawBlock] = []
        for b in payload.get("blocks") or []:
            if not isinstance(b, dict):
                continue
            text = str(b.get("text") or "")
            if not text.strip():
                continue
            blocks.append(
                RawBlock(
                    type=str(b.get("type") or "unknown"),
                    text=text,
                    bbox=_coerce_bbox(b.get("bbox")),
                    confidence=_coerce_float(b.get("confidence"), 0.7),
                    question_number=_opt_str(b.get("question_number")),
                    marks=_opt_str(b.get("marks")),
                    script=str(b.get("script") or "unknown"),
                )
            )
        return EngineResult(
            engine_id=self.id,
            model=self.model,
            language=str(payload.get("language") or language),
            page_title=str(payload.get("page_title") or ""),
            blocks=blocks,
            notes=str(payload.get("notes") or ""),
            latency_ms=latency,
            raw=payload,
        )

    async def verify(
        self, prep: PrepResult, blocks: list[dict[str, Any]], language: str
    ) -> dict[str, Any]:
        """Ask the model to check a transcription against the image."""
        from .prompts import build_verification_prompt

        prompt = build_verification_prompt(language)  # type: ignore[arg-type]
        payload = await self._request_json(
            prompt, prep, schema_kind="verify", extra_payload=blocks
        )
        return payload

    # -- provider hooks -----------------------------------------------------

    @abstractmethod
    async def _request_json(
        self,
        prompt: str,
        prep: PrepResult,
        *,
        schema_kind: str = "extraction",
        extra_payload: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Call the provider and return a parsed JSON object."""

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _b64(prep: PrepResult) -> str:
        return base64.standard_b64encode(prep.data).decode("ascii")

    @staticmethod
    def _retry(fn, attempts: int = 3, base_delay: float = 1.5):
        """Retry with exponential backoff on transient HTTP errors."""
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                return fn()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in (408, 409, 429) or 500 <= status < 600:
                    last = exc
                    if attempt < attempts - 1:
                        delay = base_delay * (2 ** attempt)
                        log.warning("retrying after %s in %.1fs", status, delay)
                        time.sleep(delay)
                        continue
                raise EngineError(
                    f"HTTP {status}: {exc.response.text[:400]}"
                ) from exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
                if attempt < attempts - 1:
                    delay = base_delay * (2 ** attempt)
                    log.warning("retrying after %s in %.1fs", type(exc).__name__, delay)
                    time.sleep(delay)
                    continue
                raise EngineError(f"network error: {exc}") from exc
        raise EngineError(f"request failed: {last}")


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json_loose(text: str) -> dict[str, Any]:
    """Parse a JSON object out of a model response, tolerating stray prose."""
    if not text:
        raise EngineError("empty response")
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    m = _FENCE_RE.search(text)
    if m:
        try:
            obj = json.loads(m.group(1).strip())
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    # Brace-balanced scan for the first complete object.
    start = text.find("{")
    if start >= 0:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break
    raise EngineError("could not parse JSON from model response")


def _coerce_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    out: list[float] = []
    for v in value:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        out.append(max(0.0, min(1.0, f)))
    if out[2] <= out[0] or out[3] <= out[1]:
        return None
    return out


def _coerce_float(value: Any, default: float) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f != f:  # NaN
        return default
    return max(0.0, min(1.0, f))


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None
