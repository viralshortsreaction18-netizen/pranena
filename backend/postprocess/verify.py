"""Quality-assurance helpers used by the review queue.

These checks are deliberately cheap and deterministic so the UI can show the
reviewer *exactly* where a human should look.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..schemas import Block, Flag

#: Similarity below which two engine readings are considered to disagree.
DISAGREE = 0.82

#: Characters that are routinely confused by OCR engines in Devanagari.
HIGH_RISK_DEVANAGARI = set("छक्षशषटठडढऋॠञज्ञ")


@dataclass
class Issue:
    block_id: str
    severity: str  # "high" | "medium" | "low"
    kind: str
    message: str
    index: int | None = None


def check_block(block: Block) -> list[Issue]:
    """Return the issues worth surfacing for one block."""
    issues: list[Issue] = []
    text = block.text or ""
    if not text.strip():
        return issues

    if Flag.ENGINE_DISAGREEMENT in block.flags:
        readings = ", ".join(
            f"{name}: {value[:60]}" for name, value in block.engine_texts.items()
        )
        issues.append(Issue(
            block.id, "high", "disagreement",
            f"The OCR engines read this line differently. Readings: {readings}",
        ))

    if Flag.LOW_CONFIDENCE in block.flags:
        issues.append(Issue(
            block.id, "medium", "low_confidence",
            f"Low confidence ({block.confidence:.0%}) - check against the image.",
        ))

    if Flag.DEVANAGARI_CONFUSION in block.flags:
        issues.append(Issue(
            block.id, "medium", "devanagari",
            block.note or "Ambiguous Devanagari characters detected.",
        ))

    if Flag.UNKNOWN_WORD in block.flags:
        issues.append(Issue(
            block.id, "low", "unknown_word",
            block.note or "Contains words that are not in the lexicon.",
        ))

    if Flag.MISSING_MARKS in block.flags and block.type.value == "question":
        issues.append(Issue(
            block.id, "low", "missing_marks",
            "No marks value found for this question.",
        ))

    if Flag.SUSPECT_TRUNCATION in block.flags:
        issues.append(Issue(
            block.id, "high", "truncation",
            "This line looks truncated or partly illegible.",
        ))

    if Flag.EMPTY in block.flags:
        issues.append(Issue(block.id, "medium", "empty", "This block is empty."))

    return issues


def document_issues(blocks: list[Block]) -> list[Issue]:
    out: list[Issue] = []
    for b in blocks:
        out.extend(check_block(b))
    return out


def quality_score(blocks: list[Block]) -> float:
    """0..100 estimate of how much of the document still needs a human eye."""
    if not blocks:
        return 100.0
    good = 0
    for b in blocks:
        if b.review_status.value != "pending" and not b.flags:
            good += 1
        elif b.confidence >= 0.9 and not b.flags:
            good += 0.5
    return round(100.0 * good / len(blocks), 1)
