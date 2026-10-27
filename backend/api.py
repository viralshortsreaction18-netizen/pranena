"""HTTP API for Pranena."""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response

from .export import build_docx, build_html, build_markdown, build_text
from .ocr.pipeline import process_page, verify_page
from .postprocess.verify import document_issues, quality_score
from .schemas import (
    BlockUpdate,
    Correction,
    ReviewStatus,
    DocSettings,
    Document,
    EngineSpec,
    ExportRequest,
    Language,
    ProcessRequest,
    ReorderRequest,
)
from .settings import EXPORT_DIR, PROVIDERS, state
from .store import add_page, image_path_for, store

log = logging.getLogger("pranena.api")

router = APIRouter(prefix="/api")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_CONTENT = {"image/png", "image/jpeg", "image/webp", "image/gif",
                   "image/bmp", "image/tiff", "application/pdf"}


# ---------------------------------------------------------------------------
# Health & providers
# ---------------------------------------------------------------------------


@router.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "engines": [
            {"id": e.id, "label": e.label, "enabled": e.enabled,
             "has_key": bool(e.api_key), "local": e.local, "model": e.model}
            for e in state.engines.values()
        ],
        "documents": len(store.list()),
    }


@router.get("/providers")
async def providers() -> dict[str, Any]:
    out = []
    for pid, p in PROVIDERS.items():
        out.append({
            "id": pid,
            "label": p.label,
            "kind": p.kind,
            "default_model": p.default_model,
            "recommended_models": list(p.recommended_models),
            "base_url": p.default_base_url,
            "key_url": p.key_url,
            "local": p.local,
            "notes": p.notes,
            "configured": bool(p.env_key and state.engines.get(pid, None)
                               and state.engines[pid].api_key),
        })
    return {"providers": out}


@router.get("/engines")
async def list_engines() -> dict[str, Any]:
    return {
        "engines": [
            {
                "id": e.id, "label": e.label, "provider": e.provider,
                "model": e.model, "base_url": e.base_url,
                "enabled": e.enabled, "local": e.local,
                "has_key": bool(e.api_key),
                "temperature": e.temperature, "max_tokens": e.max_tokens,
            }
            for e in state.engines.values()
        ]
    }


@router.post("/engines")
async def upsert_engine(spec: EngineSpec = Body(...)) -> dict[str, Any]:
    """Add or update an engine.  The API key is stored in the running process."""
    if spec.provider not in PROVIDERS and spec.provider != "custom":
        raise HTTPException(400, f"unknown provider '{spec.provider}'")
    if spec.provider in PROVIDERS and not PROVIDERS[spec.provider].local and not spec.api_key:
        raise HTTPException(400, "An API key is required for this provider.")
    spec.id = spec.id or spec.provider
    state.engines[spec.id] = spec
    return {"ok": True, "engine_id": spec.id}


@router.delete("/engines/{engine_id}")
async def delete_engine(engine_id: str) -> dict[str, Any]:
    if engine_id not in state.engines:
        raise HTTPException(404, "engine not found")
    del state.engines[engine_id]
    return {"ok": True}


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@router.get("/documents")
async def list_documents() -> dict[str, Any]:
    return {
        "documents": [
            {
                "id": d.id, "name": d.name, "created_at": d.created_at,
                "updated_at": d.updated_at, "pages": len(d.pages),
                "status": d.status, "progress": d.progress(),
            }
            for d in store.list()
        ]
    }


@router.post("/documents")
async def create_document(payload: dict[str, Any] = Body(default={})) -> Document:
    doc = store.create(payload.get("name") or "Untitled paper")
    return doc


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str) -> dict[str, Any]:
    doc = store.get(doc_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    blocks = doc.all_blocks()
    return {
        "document": doc.model_dump(),
        "issues": [i.__dict__ for i in document_issues(blocks)],
        "quality_score": quality_score(blocks),
        "progress": doc.progress(),
    }


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str) -> dict[str, Any]:
    if not store.delete(doc_id):
        raise HTTPException(404, "document not found")
    return {"ok": True}


@router.patch("/documents/{doc_id}/settings")
async def update_settings(doc_id: str, settings: DocSettings = Body(...)) -> Document:
    doc = store.require(doc_id)
    doc.settings = settings
    return store.update(doc)


@router.patch("/documents/{doc_id}")
async def update_document(doc_id: str, payload: dict[str, Any] = Body(...)) -> Document:
    doc = store.require(doc_id)
    if "name" in payload:
        doc.name = str(payload["name"])
    if "language" in payload:
        doc.language = payload["language"]
    return store.update(doc)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@router.post("/documents/{doc_id}/pages")
async def upload_pages(
    doc_id: str,
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    doc = store.require(doc_id)
    added = []
    for uf in files:
        raw = await uf.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, f"{uf.filename} is larger than 25 MB")
        if not raw:
            continue
        page = add_page(doc, raw, uf.filename or "page.png")
        # Read the dimensions so the frontend can size the preview correctly.
        try:
            from PIL import Image

            with Image.open(io.BytesIO(raw)) as im:
                page.width, page.height = im.size
        except Exception:
            pass
        added.append(page.model_dump())
    store.update(doc)
    return {"pages": added}


@router.delete("/documents/{doc_id}/pages/{page_id}")
async def delete_page(doc_id: str, page_id: str) -> dict[str, Any]:
    doc = store.require(doc_id)
    page = next((p for p in doc.pages if p.id == page_id), None)
    if page is None:
        raise HTTPException(404, "page not found")
    for path in (page.image_path, page.prepared_path):
        if path:
            Path(path).unlink(missing_ok=True)
    doc.pages = [p for p in doc.pages if p.id != page_id]
    for i, p in enumerate(doc.pages):
        p.order = i
    return store.update(doc).model_dump()


@router.post("/documents/{doc_id}/pages/reorder")
async def reorder_pages(doc_id: str, payload: ReorderRequest) -> Document:
    doc = store.require(doc_id)
    index = {p.id: p for p in doc.pages}
    ordered = [index[i] for i in payload.block_ids if i in index]  # page ids
    ordered += [p for p in doc.pages if p.id not in payload.block_ids]
    for i, p in enumerate(ordered):
        p.order = i
    doc.pages = ordered
    return store.update(doc)


@router.get("/documents/{doc_id}/pages/{page_id}/image")
async def page_image(doc_id: str, page_id: str, variant: str = Query("prepared")):
    doc = store.get(doc_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    page = next((p for p in doc.pages if p.id == page_id), None)
    if page is None:
        raise HTTPException(404, "page not found")
    path = page.prepared_path if variant == "prepared" and page.prepared_path else page.image_path
    if not path or not Path(path).exists():
        raise HTTPException(404, "image not available")
    return FileResponse(path)


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------


def _selected_engines(requested: list[str] | None) -> list[EngineSpec]:
    specs = [e for e in state.engines.values() if e.enabled]
    if requested:
        specs = [s for s in specs if s.id in requested]
    if not specs:
        raise HTTPException(400, "No OCR engine is enabled. Add an API key in Settings.")
    return specs


@router.post("/documents/{doc_id}/process")
async def process(doc_id: str, request: ProcessRequest = Body(default=ProcessRequest())):
    """Start OCR for every page that has not been processed yet."""
    doc = store.require(doc_id)
    specs = _selected_engines(request.engines)
    doc.engines = [s.id for s in specs]

    targets = [
        p for p in doc.pages
        if request.force or not p.blocks or p.status != "done"
    ]
    if not targets:
        return {"status": "idle", "message": "All pages are already processed."}

    doc.status = "running"
    doc.job_message = f"Processing {len(targets)} page(s)..."
    store.update(doc)

    async def _worker() -> None:
        for page in targets:
            page.status = "running"
            page.error = None
            store.update(doc)
            try:
                raw = Path(page.image_path).read_bytes()
                outcome = await process_page(
                    raw, specs, request.language, doc.settings
                )
                if outcome.prep is not None:
                    prep_path = image_path_for(page.id + "_prep", "prep.jpg")
                    prep_path.write_bytes(outcome.prep.data)
                    page.prepared_path = str(prep_path)
                    page.width, page.height = outcome.prep.width, outcome.prep.height
                    page.preprocessed = True
                page.blocks = outcome.blocks
                page.agreement = outcome.agreement
                page.status = "done" if outcome.blocks else "error"
                if outcome.errors:
                    page.error = "; ".join(f"{k}: {v}" for k, v in outcome.errors.items())
                if outcome.errors and not outcome.blocks:
                    doc.job_message = f"Page {page.order + 1} failed: {page.error}"
            except Exception as exc:
                log.exception("page processing failed")
                page.status = "error"
                page.error = f"{type(exc).__name__}: {exc}"
                doc.job_message = f"Page {page.order + 1} failed: {exc}"
            store.update(doc)
        doc.status = "idle"
        doc.job_message = "Processing complete."
        store.update(doc)

    asyncio.create_task(_worker())
    return {"status": "running", "message": f"Started processing {len(targets)} page(s)."}


@router.post("/documents/{doc_id}/verify")
async def verify(doc_id: str, payload: dict[str, Any] = Body(default={})):
    """Run the AI verification pass over the current transcription."""
    doc = store.require(doc_id)
    engine_id = payload.get("engine") or (doc.engines[0] if doc.engines else None)
    spec = state.engines.get(engine_id) if engine_id else None
    if spec is None:
        raise HTTPException(400, "No engine selected for verification.")
    apply_changes = bool(payload.get("apply", False))

    total_corrections = 0
    for page in doc.pages:
        if not page.blocks or not page.image_path:
            continue
        try:
            raw = Path(page.image_path).read_bytes()
            result = await verify_page(raw, page.blocks, spec, doc.language)
        except Exception as exc:
            log.exception("verification failed")
            page.error = f"verification failed: {exc}"
            continue
        page.corrections = []
        for c in result.get("corrections", []):
            corr = Correction(
                block_id=c["block_id"],
                original=c["original"],
                corrected=c["corrected"],
                reason=c["reason"],
                confidence=c["confidence"],
                applied=False,
            )
            if apply_changes and c["confidence"] >= 0.75:
                for b in page.blocks:
                    if b.id == c["block_id"]:
                        b.text = c["corrected"]
                        b.edited_by_user = True
                        b.review_status = b.review_status.__class__.CORRECTED
                        b.note = f"Auto-corrected: {c['reason']}"
                        corr.applied = True
                        break
            page.corrections.append(corr)
            total_corrections += 1
        if result.get("overall_note"):
            doc.verification_notes.append(f"Page {page.order + 1}: {result['overall_note']}")
    store.update(doc)
    return {
        "corrections": total_corrections,
        "applied": apply_changes,
        "notes": doc.verification_notes,
    }


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------


@router.patch("/documents/{doc_id}/blocks/{block_id}")
async def update_block(doc_id: str, block_id: str, payload: BlockUpdate) -> dict[str, Any]:
    doc = store.require(doc_id)
    for page in doc.pages:
        for block in page.blocks:
            if block.id != block_id:
                continue
            if payload.text is not None and payload.text != block.text:
                block.text = payload.text
                block.edited_by_user = True
                block.review_status = ReviewStatus.CORRECTED
            if payload.type is not None:
                block.type = payload.type
            if payload.review_status is not None:
                block.review_status = payload.review_status
            if payload.marks is not None:
                block.marks = payload.marks or None
            if payload.question_number is not None:
                block.question_number = payload.question_number or None
            if payload.language is not None:
                block.language = payload.language
            if payload.note is not None:
                block.note = payload.note
            store.update(doc)
            return {"ok": True, "block": block.model_dump()}
    raise HTTPException(404, "block not found")


@router.post("/documents/{doc_id}/blocks/reorder")
async def reorder_blocks(doc_id: str, payload: ReorderRequest) -> Document:
    doc = store.require(doc_id)
    for page in doc.pages:
        index = {b.id: b for b in page.blocks}
        if not any(bid in index for bid in payload.block_ids):
            continue
        ordered = [index[i] for i in payload.block_ids if i in index]
        ordered += [b for b in page.blocks if b.id not in index]
        page.blocks = ordered
    return store.update(doc)


@router.post("/documents/{doc_id}/blocks/{block_id}/review")
async def mark_reviewed(doc_id: str, block_id: str, payload: dict[str, Any] = Body(...)):
    """Verify / un-verify a block (or every block)."""
    doc = store.require(doc_id)
    status = ReviewStatus(payload.get("status", "verified"))
    target = payload.get("scope", "block")
    count = 0
    for page in doc.pages:
        for block in page.blocks:
            if target == "page":
                if page.id != payload.get("page_id"):
                    continue
            elif block.id != block_id:
                continue
            block.review_status = status
            count += 1
    store.update(doc)
    return {"ok": True, "updated": count, "progress": doc.progress()}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@router.post("/documents/{doc_id}/export")
async def export(doc_id: str, request: ExportRequest = Body(default=ExportRequest())):
    doc = store.require(doc_id)
    if not doc.all_blocks():
        raise HTTPException(400, "Nothing to export yet.")

    # Optionally drop blocks the reviewer has not cleared.
    if request.only_unreviewed:
        for page in doc.pages:
            page.blocks = [b for b in page.blocks if b.review_status == ReviewStatus.PENDING]

    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_name = re.sub(r"[^A-Za-z0-9 _-]+", "", doc.name).strip() or "exam-paper"

    if request.fmt == "docx":
        data = build_docx(doc)
        path = EXPORT_DIR / f"{safe_name}-{stamp}.docx"
        path.write_bytes(data)
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=f"{safe_name}.docx",
        )
    if request.fmt == "markdown":
        return PlainTextResponse(build_markdown(doc), media_type="text/markdown")
    if request.fmt == "html":
        return HTMLResponse(build_html(doc))
    return PlainTextResponse(build_text(doc), media_type="text/plain")


@router.get("/documents/{doc_id}/export/preview")
async def export_preview(doc_id: str) -> dict[str, Any]:
    """A quick plain-text preview of what will be exported."""
    doc = store.require(doc_id)
    return {"preview": build_text(doc)[:20000]}
