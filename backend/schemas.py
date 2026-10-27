"""Pydantic models for Pranena: handwritten exam paper -> editable Word.

The data model is deliberately page-centric: every page keeps its uploaded image
plus the list of extracted *blocks*.  A block is the atomic unit the human
reviews, edits, verifies and finally exports to .docx.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> float:
    return time.time()


# --------------------------------------------------------------------------
# Languages
# --------------------------------------------------------------------------

Language = Literal["en", "hi", "sa", "mixed"]
LANGUAGE_NAMES = {
    "en": "English",
    "hi": "हिन्दी (Hindi)",
    "sa": "संस्कृतम् (Sanskrit)",
    "mixed": "Mixed / Auto-detect",
}


class Script(str, Enum):
    LATIN = "latin"
    DEVANAGARI = "devanagari"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------
# Blocks
# --------------------------------------------------------------------------


class BlockType(str, Enum):
    """Semantic role of a block inside an exam paper."""

    TITLE = "title"
    SUBTITLE = "subtitle"
    SECTION = "section"
    INSTRUCTION = "instruction"
    QUESTION = "question"
    OPTIONS = "options"
    SUBCOMPLETION = "subcompletion"
    MARKS = "marks"
    NOTE = "note"
    DIAGRAM_CAPTION = "diagram_caption"
    TABLE = "table"
    HEADER = "header"
    FOOTER = "footer"
    UNKNOWN = "unknown"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    CORRECTED = "corrected"


class Flag(str, Enum):
    LOW_CONFIDENCE = "low_confidence"
    ENGINE_DISAGREEMENT = "engine_disagreement"
    UNKNOWN_WORD = "unknown_word"
    DEVANAGARI_CONFUSION = "devanagari_confusion"
    MISSING_MARKS = "missing_marks"
    SUSPECT_TRUNCATION = "suspect_truncation"
    HALLUCINATION_RISK = "hallucination_risk"
    EMPTY = "empty"


class Block(BaseModel):
    """One logical element extracted from a page."""

    id: str = Field(default_factory=lambda: _uid("blk"))
    type: BlockType = BlockType.UNKNOWN
    text: str = ""
    # Normalised [x1, y1, x2, y2] in 0..1 space relative to the page image.
    bbox: list[float] | None = None
    # Per-engine raw transcriptions keyed by engine name.
    engine_texts: dict[str, str] = Field(default_factory=dict)
    # 0..1 – agreement between engines, or the model's own confidence.
    confidence: float = 0.0
    flags: list[Flag] = Field(default_factory=list)
    review_status: ReviewStatus = ReviewStatus.PENDING
    # Set when a human edits the text (so we never overwrite their work).
    edited_by_user: bool = False
    question_number: str | None = None
    marks: str | None = None
    language: Language | None = None
    script: Script = Script.UNKNOWN
    # Free-form notes produced by the verification pass.
    note: str | None = None

    @property
    def is_flagged(self) -> bool:
        return bool(self.flags)


class Page(BaseModel):
    id: str = Field(default_factory=lambda: _uid("pg"))
    filename: str = ""
    order: int = 0
    width: int = 0
    height: int = 0
    language: Language = "en"
    status: Literal["pending", "running", "done", "error"] = "pending"
    error: str | None = None
    blocks: list[Block] = Field(default_factory=list)
    # Server-side path of the stored upload.
    image_path: str | None = None
    # Path of the pre-processed image the OCR model actually saw.  Bounding
    # boxes are expressed in ITS coordinate space, so the review overlay must
    # display this image rather than the raw upload.
    prepared_path: str | None = None
    # Whether preprocessing (deskew/denoise) was applied to the stored image.
    preprocessed: bool = False
    # Overall engine agreement for the page, 0..1.
    agreement: float = 0.0
    # Text the verification pass believes should replace the current text.
    corrections: list["Correction"] = Field(default_factory=list)


class Correction(BaseModel):
    """A proposed edit produced by the AI verification pass."""

    block_id: str
    original: str
    corrected: str
    reason: str = ""
    confidence: float = 0.0
    applied: bool = False


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------


class DocSettings(BaseModel):
    """Everything that controls how the final Word file is laid out."""

    title: str = ""
    subtitle: str = ""
    institution: str = ""
    subject: str = ""
    class_name: str = ""
    session: str = ""
    duration: str = ""
    max_marks: str = ""
    date: str = ""
    footer_text: str = ""
    # Formatting
    font_latin: str = "Times New Roman"
    font_devanagari: str = "Nirmala UI"
    font_size_pt: float = 12.0
    line_spacing: float = 1.5
    question_number_format: str = "{n}."
    show_marks_column: bool = True
    marks_position: Literal["right_tab", "inline", "bracket"] = "right_tab"
    renumber_questions: bool = False
    include_options: bool = True
    page_size: Literal["A4", "Letter"] = "A4"
    margin_in: float = 0.8
    # Post-processing
    normalize_devanagari: bool = True
    prefer_precomposed_nukta: bool = False
    convert_devanagari_digits: bool = False
    devanagari_danda_to_period: bool = False
    flag_low_confidence_only: bool = True
    languages: list[Language] = Field(default_factory=lambda: ["en", "hi", "sa"])


class Document(BaseModel):
    id: str = Field(default_factory=lambda: _uid("doc"))
    name: str = "Untitled paper"
    created_at: float = Field(default_factory=_now)
    updated_at: float = Field(default_factory=_now)
    language: Language = "mixed"
    settings: DocSettings = Field(default_factory=DocSettings)
    pages: list[Page] = Field(default_factory=list)
    engines: list[str] = Field(default_factory=list)
    verification_notes: list[str] = Field(default_factory=list)
    # Background job state.
    status: Literal["idle", "running", "error"] = "idle"
    job_message: str = ""

    def all_blocks(self) -> list[Block]:
        out: list[Block] = []
        for page in self.pages:
            out.extend(page.blocks)
        return out

    def progress(self) -> dict[str, Any]:
        blocks = self.all_blocks()
        total = len(blocks)
        verified = sum(1 for b in blocks if b.review_status != ReviewStatus.PENDING)
        flagged = sum(1 for b in blocks if b.flags)
        return {
            "blocks": total,
            "verified": verified,
            "flagged": flagged,
            "percent": round(100.0 * verified / total, 1) if total else 0.0,
        }


# --------------------------------------------------------------------------
# API payloads
# --------------------------------------------------------------------------


class EngineSpec(BaseModel):
    """A configured OCR engine."""

    id: str
    label: str
    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    # Extra options
    temperature: float = 0.0
    max_tokens: int = 8192
    enabled: bool = True
    # Only set for local engines (no key needed).
    local: bool = False


class ProcessRequest(BaseModel):
    engines: list[str] | None = None
    language: Language = "mixed"
    # Run the second "verify against the image" pass automatically.
    verify: bool = False
    # Re-run even if pages already have content.
    force: bool = False


class BlockUpdate(BaseModel):
    text: str | None = None
    type: BlockType | None = None
    review_status: ReviewStatus | None = None
    marks: str | None = None
    question_number: str | None = None
    language: Language | None = None
    note: str | None = None


class ReorderRequest(BaseModel):
    block_ids: list[str]


class ExportRequest(BaseModel):
    fmt: Literal["docx", "markdown", "html", "txt"] = "docx"
    include_flagged: bool = True
    only_unreviewed: bool = False


Page.model_rebuild()
