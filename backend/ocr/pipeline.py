"""The end-to-end OCR pipeline for one page.

    image -> prepare -> run engines in parallel -> consensus -> structure
          -> Devanagari normalisation -> quality flags

and a second path for the verification pass:

    image + current blocks -> verify -> proposed corrections
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from ..postprocess import devanagari as dev
from ..postprocess.structure import infer_structure
from ..schemas import Block, DocSettings, EngineSpec, Flag, Language, Script
from . import build_engine
from .base import BaseEngine, EngineError, EngineResult
from .consensus import build_consensus, page_agreement, to_blocks
from .imageprep import PrepResult, prepare

log = logging.getLogger("pranena.pipeline")


@dataclass
class PageOutcome:
    blocks: list[Block] = field(default_factory=list)
    engines_used: list[str] = field(default_factory=list)
    engine_results: dict[str, EngineResult] = field(default_factory=dict)
    agreement: float = 0.0
    prep: PrepResult | None = None
    errors: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _detect_language(text: str, fallback: Language) -> Language:
    if dev.contains_devanagari(text):
        # Distinguish Sanskrit from Hindi is a judgement call; treat a page with
        # Devanagari but no modern Hindi markers as Sanskrit.
        hindi_markers = (
            "है", "हैं", "का", "की", "के", "को", "में", "से", "और", "यह",
            "वह", "कि", "ने", "पर", "हो", "कर", "गया", "जा", "एक", "लिए",
            "प्रश्न", "अंक", "समय", "विषय", "निर्देश",
        )
        if any(m in text for m in hindi_markers):
            return "hi"
        return "sa" if text.strip() else fallback
    return "en"


def _apply_quality_flags(blocks: list[Block]) -> None:
    """Attach review flags based on the text itself."""
    for block in blocks:
        text = block.text or ""
        if not text.strip():
            continue

        if dev.contains_devanagari(text):
            # Only *unknown* words that become a known word after swapping a
            # look-alike character are treated as likely mis-reads.  A raw
            # look-alike scan flags nearly every Hindi word (श/स/ष, न/ण,
            # द/ध ... are all common in correct text) and buries the real
            # problems in noise.
            confusable = dev.find_confusable_words(text)
            rare = dev.find_rare_characters(text)
            virama = dev.find_suspicious_virama(text)
            if confusable or rare or virama:
                block.flags.append(Flag.DEVANAGARI_CONFUSION)
                details = []
                if confusable:
                    details.append(
                        "ambiguous characters: "
                        + ", ".join(
                            f"'{h['word']}' - '{h['char']}' may be "
                            f"'{'/'.join(h['alternatives'])}' "
                            f"(did you mean '{h['suggestion']}'?)"
                            for h in confusable[:4]
                        )
                    )
                if rare:
                    details.append(
                        "unusual codepoints: "
                        + ", ".join(f"'{h['char']}'" for h in rare[:4])
                    )
                if virama:
                    details.append("a virama (्) follows a vowel, which is invalid")
                block.note = " | ".join(details)

            unknown = dev.unknown_words(text)
            if unknown:
                block.flags.append(Flag.UNKNOWN_WORD)
                existing = block.note or ""
                extra = "words not in the lexicon: " + ", ".join(unknown[:5])
                block.note = f"{existing} | {extra}" if existing else extra

        if len(text) > 3 and text.rstrip().endswith(("...", "…")):
            block.flags.append(Flag.SUSPECT_TRUNCATION)
        if text.strip().count("?") >= 2 and "[?]" in text:
            block.flags.append(Flag.SUSPECT_TRUNCATION)


def _normalise_text(text: str, settings: DocSettings, language: Language) -> str:
    if language in ("hi", "sa", "mixed") or dev.contains_devanagari(text):
        if settings.normalize_devanagari:
            text = dev.normalize_devanagari(
                text,
                prefer_precomposed_nukta=settings.prefer_precomposed_nukta,
                convert_digits=settings.convert_devanagari_digits,
                danda_to_period=settings.devanagari_danda_to_period,
            )
    return text


async def _run_engine(
    spec: EngineSpec, prep: PrepResult, language: Language
) -> tuple[str, EngineResult | Exception]:
    try:
        engine: BaseEngine = build_engine(spec)
    except Exception as exc:
        return spec.id, exc
    try:
        result = await engine.extract(prep, language)
        return spec.id, result
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("engine %s failed", spec.id)
        return spec.id, exc


async def process_page(
    raw_image: bytes,
    specs: list[EngineSpec],
    language: Language = "mixed",
    settings: DocSettings | None = None,
    *,
    auto_rotate: bool = True,
    deskew: bool = True,
    enhance: bool = True,
) -> PageOutcome:
    """Run the full pipeline for one page image."""
    settings = settings or DocSettings()

    prep = prepare(
        raw_image,
        auto_rotate=auto_rotate,
        deskew=deskew,
        enhance=enhance,
    )
    if prep.notes:
        log.info("image prep notes: %s", prep.notes)

    usable = [s for s in specs if s.enabled]
    outcome = PageOutcome(prep=prep)

    if not usable:
        outcome.errors["_"] = "No OCR engine is enabled."
        return outcome

    gathered = await asyncio.gather(
        *(_run_engine(s, prep, language) for s in usable),
        return_exceptions=True,
    )

    results: list[EngineResult] = []
    for engine_id, item in gathered:
        if isinstance(item, Exception):
            outcome.errors[engine_id] = f"{type(item).__name__}: {item}"
            continue
        if item.error:
            outcome.errors[engine_id] = item.error
            continue
        results.append(item)
        outcome.engine_results[engine_id] = item

    if not results:
        outcome.errors.setdefault("_", "All OCR engines failed.")
        return outcome

    outcome.engines_used = [r.engine_id for r in results]
    consensus = build_consensus(results)
    blocks = to_blocks(consensus)

    # Determine the page language from the extracted text.
    joined = " ".join(b.text for b in blocks)
    detected = _detect_language(joined, language)
    for b in blocks:
        b.language = detected
        b.script = Script.DEVANAGARI if dev.contains_devanagari(b.text) else (
            Script.LATIN if any(c.isascii() and c.isalpha() for c in b.text)
            else Script.UNKNOWN
        )

    # Normalise + infer structure.
    for b in blocks:
        b.text = _normalise_text(b.text, settings, detected)
    infer_structure(blocks)
    _apply_quality_flags(blocks)

    # A question with no marks attached is worth a gentle flag.
    for b in blocks:
        if b.type.value == "question" and not b.marks and "missing_marks" not in [
            f.value for f in b.flags
        ]:
            b.flags.append(Flag.MISSING_MARKS)

    outcome.blocks = blocks
    outcome.agreement = page_agreement(consensus, len(results))
    outcome.notes = [r.notes for r in results if r.notes]
    return outcome


async def verify_page(
    raw_image: bytes,
    blocks: list[Block],
    spec: EngineSpec,
    language: Language = "mixed",
    *,
    auto_rotate: bool = True,
    deskew: bool = True,
    enhance: bool = True,
) -> dict[str, Any]:
    """Ask one engine to check the current transcription against the image."""
    prep = prepare(raw_image, auto_rotate=auto_rotate, deskew=deskew, enhance=enhance)

    payload = [
        {
            "index": i,
            "type": b.type.value,
            "text": b.text,
            "question_number": b.question_number or "",
            "marks": b.marks or "",
        }
        for i, b in enumerate(blocks)
    ]

    engine = build_engine(spec)
    raw = await engine.verify(prep, payload, language)

    corrections: list[dict[str, Any]] = []
    for c in raw.get("corrections") or []:
        if not isinstance(c, dict):
            continue
        try:
            idx = int(c.get("block_index"))
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(blocks):
            continue
        original = str(c.get("original") or "")
        corrected = str(c.get("corrected") or "")
        if original.strip() == corrected.strip():
            continue
        corrections.append({
            "block_index": idx,
            "block_id": blocks[idx].id,
            "original": original,
            "corrected": corrected,
            "reason": str(c.get("reason") or ""),
            "confidence": float(c.get("confidence") or 0.0),
        })
    return {
        "corrections": corrections,
        "overall_note": str(raw.get("overall_note") or ""),
    }
