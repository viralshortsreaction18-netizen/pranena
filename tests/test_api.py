"""API-level tests using FastAPI's TestClient."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.main import app
from backend.settings import state


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def make_image(text: str = "1. What is 2 + 2?", size=(1000, 1400)) -> bytes:
    img = Image.new("RGB", size, "white")
    from PIL import ImageDraw

    d = ImageDraw.Draw(img)
    d.text((60, 60), text, fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class TestHealthAndProviders:
    def test_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_providers_listed(self, client):
        r = client.get("/api/providers")
        assert r.status_code == 200
        ids = {p["id"] for p in r.json()["providers"]}
        assert {"anthropic", "gemini", "openai", "ollama", "demo"} <= ids

    def test_engine_requires_a_key(self, client):
        r = client.post("/api/engines", json={
            "id": "anthropic", "label": "Claude", "provider": "anthropic",
            "model": "claude-sonnet-5-5", "api_key": "",
        })
        assert r.status_code == 400

    def test_engine_can_be_added_and_removed(self, client):
        r = client.post("/api/engines", json={
            "id": "testprov", "label": "Test", "provider": "gemini",
            "model": "gemini-3.8-flash", "api_key": "abc123",
        })
        assert r.status_code == 200
        assert "testprov" in state.engines
        assert client.delete("/api/engines/testprov").status_code == 200
        assert "testprov" not in state.engines


class TestDocumentFlow:
    def test_create_and_delete(self, client):
        r = client.post("/api/documents", json={"name": "My Paper"})
        assert r.status_code == 200
        doc_id = r.json()["id"]
        assert client.get(f"/api/documents/{doc_id}").status_code == 200
        assert client.delete(f"/api/documents/{doc_id}").status_code == 200
        assert client.get(f"/api/documents/{doc_id}").status_code == 404

    def test_upload_page(self, client):
        doc_id = client.post("/api/documents", json={}).json()["id"]
        r = client.post(
            f"/api/documents/{doc_id}/pages",
            files=[("files", ("page1.png", make_image(), "image/png"))],
        )
        assert r.status_code == 200
        pages = r.json()["pages"]
        assert len(pages) == 1
        assert pages[0]["width"] == 1000

        # The image must be retrievable for the review overlay.
        img = client.get(f"/api/documents/{doc_id}/pages/{pages[0]['id']}/image")
        assert img.status_code == 200
        assert len(img.content) > 100

    def test_process_with_demo_engine(self, client):
        doc_id = client.post("/api/documents", json={}).json()["id"]
        client.post(
            f"/api/documents/{doc_id}/pages",
            files=[("files", ("page1.png", make_image(), "image/png"))],
        )
        # Enable the demo engine for this test.
        state.engines["demo"].enabled = True
        r = client.post(f"/api/documents/{doc_id}/process", json={"language": "en"})
        assert r.status_code == 200

        import time

        for _ in range(60):
            doc = client.get(f"/api/documents/{doc_id}").json()["document"]
            if doc["status"] != "running":
                break
            time.sleep(0.15)

        assert doc["status"] == "idle"
        assert len(doc["pages"][0]["blocks"]) > 0
        block = doc["pages"][0]["blocks"][0]
        assert block["text"]
        assert block["confidence"] > 0

    def test_block_editing(self, client):
        doc_id = client.post("/api/documents", json={}).json()["id"]
        client.post(
            f"/api/documents/{doc_id}/pages",
            files=[("files", ("page1.png", make_image(), "image/png"))],
        )
        state.engines["demo"].enabled = True
        client.post(f"/api/documents/{doc_id}/process", json={"language": "en"})
        import time

        for _ in range(60):
            doc = client.get(f"/api/documents/{doc_id}").json()["document"]
            if doc["status"] != "running":
                break
            time.sleep(0.15)

        block_id = doc["pages"][0]["blocks"][0]["id"]
        r = client.patch(
            f"/api/documents/{doc_id}/blocks/{block_id}",
            json={"text": "Edited text", "review_status": "verified"},
        )
        assert r.status_code == 200
        assert r.json()["block"]["text"] == "Edited text"
        assert r.json()["block"]["review_status"] == "verified"

        # Progress must reflect the review.
        prog = client.get(f"/api/documents/{doc_id}").json()["progress"]
        assert prog["verified"] >= 1

    def test_settings_update(self, client):
        doc_id = client.post("/api/documents", json={}).json()["id"]
        r = client.patch(
            f"/api/documents/{doc_id}/settings",
            json={"title": "वार्षिक परीक्षा", "font_devanagari": "Mangal",
                  "renumber_questions": True},
        )
        assert r.status_code == 200
        assert r.json()["settings"]["title"] == "वार्षिक परीक्षा"

    def test_export_docx(self, client):
        doc_id = client.post("/api/documents", json={}).json()["id"]
        client.post(
            f"/api/documents/{doc_id}/pages",
            files=[("files", ("page1.png", make_image(), "image/png"))],
        )
        state.engines["demo"].enabled = True
        client.post(f"/api/documents/{doc_id}/process", json={"language": "en"})
        import time

        for _ in range(60):
            doc = client.get(f"/api/documents/{doc_id}").json()["document"]
            if doc["status"] != "running":
                break
            time.sleep(0.15)

        r = client.post(f"/api/documents/{doc_id}/export", json={"fmt": "docx"})
        assert r.status_code == 200
        assert len(r.content) > 1000
        assert "wordprocessingml" in r.headers["content-type"]

    def test_export_without_content_is_rejected(self, client):
        doc_id = client.post("/api/documents", json={}).json()["id"]
        r = client.post(f"/api/documents/{doc_id}/export", json={"fmt": "docx"})
        assert r.status_code == 400

    def test_frontend_is_served(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "Pranena" in r.text
