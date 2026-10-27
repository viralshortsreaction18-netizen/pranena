"""In-memory document store with JSON persistence.

A school scanning session is short-lived, so an in-process store with a JSON
sidecar is the right amount of machinery.  Uploaded images live on disk under
``DATA_DIR/uploads``; the structured content is kept in memory and mirrored to
``DATA_DIR/documents/<id>.json`` so a server restart does not lose work.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Iterator

from .schemas import Document, Page
from .settings import DATA_DIR, UPLOAD_DIR

log = logging.getLogger("pranena.store")

DOC_DIR = DATA_DIR / "documents"
DOC_DIR.mkdir(parents=True, exist_ok=True)

MAX_DOCUMENTS = 50


class DocumentStore:
    def __init__(self) -> None:
        self._docs: dict[str, Document] = {}

    # -- CRUD ---------------------------------------------------------------

    def create(self, name: str = "Untitled paper") -> Document:
        doc = Document(name=name)
        self._docs[doc.id] = doc
        self._evict_if_needed()
        self.save(doc)
        return doc

    def get(self, doc_id: str) -> Document | None:
        return self._docs.get(doc_id)

    def require(self, doc_id: str) -> Document:
        doc = self._docs.get(doc_id)
        if doc is None:
            raise KeyError(doc_id)
        return doc

    def delete(self, doc_id: str) -> bool:
        doc = self._docs.pop(doc_id, None)
        if doc is None:
            return False
        for page in doc.pages:
            if page.image_path:
                try:
                    Path(page.image_path).unlink(missing_ok=True)
                except OSError:
                    pass
        (DOC_DIR / f"{doc_id}.json").unlink(missing_ok=True)
        return True

    def list(self) -> list[Document]:
        return sorted(self._docs.values(), key=lambda d: d.updated_at, reverse=True)

    def update(self, doc: Document) -> Document:
        doc.updated_at = time.time()
        self._docs[doc.id] = doc
        self.save(doc)
        return doc

    # -- persistence --------------------------------------------------------

    def save(self, doc: Document) -> None:
        try:
            path = DOC_DIR / f"{doc.id}.json"
            path.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
        except OSError:
            log.exception("could not persist document %s", doc.id)

    def load_all(self) -> int:
        count = 0
        for path in DOC_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                doc = Document.model_validate(data)
                self._docs[doc.id] = doc
                count += 1
            except Exception:
                log.warning("skipping unreadable document file %s", path)
        return count

    def _evict_if_needed(self) -> None:
        while len(self._docs) > MAX_DOCUMENTS:
            oldest = min(self._docs.values(), key=lambda d: d.updated_at)
            self.delete(oldest.id)


store = DocumentStore()


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------


def image_path_for(page_id: str, filename: str) -> Path:
    safe = Path(filename).name or "page.png"
    ext = Path(safe).suffix.lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"):
        ext = ".png"
    return UPLOAD_DIR / f"{page_id}{ext}"


def read_image(path: str | Path) -> bytes:
    return Path(path).read_bytes()


def add_page(doc: Document, raw: bytes, filename: str) -> Page:
    page = Page(filename=filename, order=len(doc.pages))
    path = image_path_for(page.id, filename)
    path.write_bytes(raw)
    page.image_path = str(path)
    doc.pages.append(page)
    return page
