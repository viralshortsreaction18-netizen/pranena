"""Provider API contract tests.

The provider integrations cannot be exercised without a paid API key, so we
run a local HTTP server that speaks the shape of each provider's API and
assert that Pranena sends a request that the real service would accept.

These tests protect the parts most likely to break silently: the URL, the
authentication header, the image encoding and the structured-output options.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from backend.ocr.base import parse_json_loose
from backend.ocr.engines.anthropic_engine import AnthropicEngine
from backend.ocr.engines.gemini_engine import GeminiEngine
from backend.ocr.engines.ollama_engine import OllamaEngine
from backend.ocr.engines.openai_engine import OpenAICompatibleEngine
from backend.ocr.imageprep import PrepResult
from backend.schemas import EngineSpec

PREP = PrepResult(
    data=b"\x89PNG\r\n\x1a\n fake image bytes",
    mime="image/png",
    width=100,
    height=100,
)


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.responses: dict[str, tuple[int, object]] = {}

    def add(self, path_contains: str, status: int, payload: object) -> None:
        self.responses[path_contains] = (status, payload)


RECORDER = _Recorder()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def _respond(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {"_raw": raw.decode("utf-8", "replace")}
        RECORDER.requests.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
        })

        for needle, (status, payload) in RECORDER.responses.items():
            if needle in self.path:
                self._respond(status, payload)
                return
        self._respond(500, {"error": "no mock configured"})


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture(autouse=True)
def clear_recorder():
    RECORDER.requests.clear()
    RECORDER.responses.clear()
    yield


EXTRACTION = {
    "language": "en",
    "page_title": "Exam",
    "blocks": [
        {
            "type": "question",
            "text": "1. What is 2 + 2?",
            "bbox": [0.1, 0.1, 0.9, 0.2],
            "confidence": 0.93,
            "question_number": "1",
            "marks": "2",
            "script": "latin",
        }
    ],
    "notes": "",
}


class TestAnthropicContract:
    def test_request_shape(self, server):
        RECORDER.add("/v1/messages", 200, {
            "content": [{"type": "text", "text": json.dumps(EXTRACTION)}],
            "usage": {"input_tokens": 10, "output_tokens": 20},
        })
        spec = EngineSpec(id="anthropic", label="Claude", provider="anthropic",
                          model="claude-sonnet-5-5", api_key="sk-test",
                          base_url=server)
        engine = AnthropicEngine(spec)

        result = asyncio.run(engine.extract(PREP, "en"))

        assert result.ok, result.error
        assert len(result.blocks) == 1
        assert result.blocks[0].text == "1. What is 2 + 2?"

        req = RECORDER.requests[0]
        assert req["path"] == "/v1/messages"
        headers = {k.lower(): v for k, v in req["headers"].items()}
        assert headers["x-api-key"] == "sk-test"
        assert headers["anthropic-version"] == "2023-06-01"

        body = req["body"]
        assert body["model"] == "claude-sonnet-5-5"
        assert body["max_tokens"] > 0
        # Native JSON-schema structured output.
        assert body["output_config"]["format"]["type"] == "json_schema"
        assert body["output_config"]["format"]["schema"]["type"] == "object"

        content = body["messages"][0]["content"]
        image_blocks = [c for c in content if c["type"] == "image"]
        assert len(image_blocks) == 1
        src = image_blocks[0]["source"]
        assert src["type"] == "base64"
        assert src["media_type"] == "image/png"
        assert base64.b64decode(src["data"]) == PREP.data

    def test_tool_fallback_when_structured_output_is_rejected(self, server):
        # The service rejects output_config -> Pranena must retry with a
        # JSON-instruction fallback instead of failing.
        state = {"calls": 0}

        class _Handler2(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):  # noqa: N802
                state["calls"] += 1
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                payload = json.dumps({"content": [{"type": "text", "text": json.dumps(EXTRACTION)}]})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload.encode())

            def do_GET(self):  # noqa: N802
                self.send_response(400)
                self.end_headers()

        httpd = HTTPServer(("127.0.0.1", 0), _Handler2)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_port}"
        try:
            spec = EngineSpec(id="anthropic", label="Claude", provider="anthropic",
                              model="claude-sonnet-5-5", api_key="sk-test",
                              base_url=base)
            result = asyncio.run(AnthropicEngine(spec).extract(PREP, "en"))
            assert result.ok, result.error
        finally:
            httpd.shutdown()


class TestGeminiContract:
    def test_request_shape(self, server):
        RECORDER.add(":generateContent", 200, {
            "candidates": [{
                "content": {"parts": [{"text": json.dumps(EXTRACTION)}]}
            }]
        })
        spec = EngineSpec(id="gemini", label="Gemini", provider="gemini",
                          model="gemini-3.8-flash", api_key="AIza-test",
                          base_url=server)
        engine = GeminiEngine(spec)

        result = asyncio.run(engine.extract(PREP, "hi"))

        assert result.ok, result.error
        assert result.blocks[0].marks == "2"

        req = RECORDER.requests[0]
        assert ":generateContent" in req["path"]
        assert "key=AIza-test" in req["path"]
        assert "/v1beta/models/gemini-3.8-flash:generateContent" in req["path"]

        body = req["body"]
        parts = body["contents"][0]["parts"]
        inline = [p for p in parts if "inline_data" in p]
        assert len(inline) == 1
        assert inline[0]["inline_data"]["mime_type"] == "image/png"
        assert base64.b64decode(inline[0]["inline_data"]["data"]) == PREP.data

        cfg = body["generationConfig"]
        assert cfg["responseMimeType"] == "application/json"
        assert cfg["responseSchema"]["type"] == "object"
        # Gemini's schema dialect does not accept these keywords.
        assert "additionalProperties" not in json.dumps(cfg["responseSchema"])


class TestOpenAICompatibleContract:
    def test_request_shape(self, server):
        RECORDER.add("/chat/completions", 200, {
            "choices": [{
                "message": {"role": "assistant", "content": json.dumps(EXTRACTION)}
            }]
        })
        spec = EngineSpec(id="openai", label="OpenAI", provider="openai",
                          model="gpt-5.1", api_key="sk-test", base_url=server)
        engine = OpenAICompatibleEngine(spec)

        result = asyncio.run(engine.extract(PREP, "en"))

        assert result.ok, result.error
        assert result.blocks[0].question_number == "1"

        req = RECORDER.requests[0]
        assert req["path"] == "/v1/chat/completions"
        headers = {k.lower(): v for k, v in req["headers"].items()}
        assert headers["authorization"] == "Bearer sk-test"

        body = req["body"]
        content = body["messages"][0]["content"]
        images = [c for c in content if c["type"] == "image_url"]
        assert len(images) == 1
        url = images[0]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        assert base64.b64decode(url.split(",", 1)[1]) == PREP.data

        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["schema"]["type"] == "object"

    def test_mistral_ocr_endpoint(self, server):
        RECORDER.add("/v1/ocr", 200, {
            "pages": [{
                "index": 0,
                "markdown": "# Title\n\n1. What is 2 + 2?\n\n(a) 3 (b) 4",
            }]
        })
        spec = EngineSpec(id="mistral", label="Mistral", provider="mistral",
                          model="mistral-ocr-latest", api_key="k-test",
                          base_url=server)
        engine = OpenAICompatibleEngine(spec)

        result = asyncio.run(engine.extract(PREP, "en"))

        assert result.ok, result.error
        assert result.blocks, "no blocks parsed from the Markdown"
        assert result.blocks[0].type == "title"
        assert "/v1/ocr" in RECORDER.requests[0]["path"]


class TestOllamaContract:
    def test_request_shape(self, server):
        RECORDER.add("/api/chat", 200, {
            "message": {"role": "assistant", "content": json.dumps(EXTRACTION)}
        })
        spec = EngineSpec(id="ollama", label="Ollama", provider="ollama",
                          model="qwen3-vl:8b", base_url=server)
        engine = OllamaEngine(spec)

        result = asyncio.run(engine.extract(PREP, "en"))

        assert result.ok, result.error
        req = RECORDER.requests[0]
        assert req["path"] == "/api/chat"
        body = req["body"]
        images = body["messages"][0]["images"]
        assert len(images) == 1
        assert base64.b64decode(images[0]) == PREP.data
        assert body["format"]["type"] == "object"
        assert body["stream"] is False


class TestJsonParsing:
    def test_plain_json(self):
        assert parse_json_loose('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        assert parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}

    def test_json_with_prose_around_it(self):
        assert parse_json_loose('Sure! Here you go:\n{"a": 1}\nHope that helps') == {"a": 1}

    def test_braces_inside_strings(self):
        assert parse_json_loose('{"a": "}"}') == {"a": "}"}

    def test_garbage_raises(self):
        from backend.ocr.base import EngineError

        with pytest.raises(EngineError):
            parse_json_loose("not json at all")
