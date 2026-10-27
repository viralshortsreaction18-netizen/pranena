"""Multi-engine consensus.

Running one OCR engine gives one opinion.  Running two and comparing them
gives you a *confidence estimate*, which is what makes a "no mistakes" promise
honest: where the engines agree we are confident, and where they disagree we
route the line to a human instead of guessing.

The algorithm:
  1. Align blocks across engines by bounding-box overlap **and** text
     similarity (a greedy maximum-weight matching).
  2. For aligned groups with two or more engines, synthesise a consensus text
     with character-level majority voting (a classic OCR ensemble).
  3. Score agreement; flag low agreement and single-source blocks for review.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from ..schemas import Block, BlockType, Flag, ReviewStatus, Script
from .base import EngineResult, RawBlock

#: Below this similarity the engines are considered to disagree.
DISAGREEMENT_THRESHOLD = 0.82
#: Below this, the block is marked low-confidence.
LOW_CONFIDENCE_THRESHOLD = 0.70


@dataclass
class Candidate:
    engine_id: str
    model: str
    text: str
    bbox: list[float] | None
    confidence: float
    btype: str
    question_number: str | None = None
    marks: str | None = None
    script: str = "unknown"


@dataclass
class ConsensusBlock:
    candidates: list[Candidate] = field(default_factory=list)
    text: str = ""
    confidence: float = 0.0
    agreement: float = 0.0
    bbox: list[float] | None = None
    btype: str = "unknown"
    question_number: str | None = None
    marks: str | None = None
    script: str = "unknown"
    flags: list[Flag] = field(default_factory=list)
    engine_texts: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def _iou(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != 4 or len(b) != 4:
        return 0.0
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _text_similarity(a: str, b: str) -> float:
    a, b = a.strip(), b.strip()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return fuzz.token_set_ratio(a, b) / 100.0


def _normalise_for_compare(text: str) -> str:
    """Collapse whitespace and case so trivial differences do not count."""
    return " ".join(text.split()).lower()


def _match_score(a: Candidate, b: Candidate) -> float:
    """Combined geometric + textual similarity used for alignment."""
    text = _text_similarity(a.text, b.text)
    has_geometry = bool(a.bbox and b.bbox)

    if not has_geometry:
        # At least one engine did not report coordinates (some OCR services
        # return plain Markdown).  Fall back to the text alone.
        return 0.5 + 0.5 * text if text >= 0.70 else 0.0

    geom = _iou(a.bbox, b.bbox)
    if text >= 0.95:
        # Near-identical text: trust the text, geometry is a tiebreaker.
        return 0.6 + 0.4 * geom
    if text < 0.35:
        return 0.0
    if geom == 0.0:
        # Both engines gave coordinates but they do not overlap: these are
        # different lines, even if the wording is similar.
        if text >= 0.90:
            return 0.5 * text
        return 0.0
    return 0.65 * text + 0.35 * geom


#: Minimum match score for two blocks to be considered the same line.
ALIGN_THRESHOLD = 0.55


# ---------------------------------------------------------------------------
# Character-level voting
# ---------------------------------------------------------------------------


def _char_vote(texts: list[str]) -> tuple[str, float]:
    """Synthesise one text from several candidates by character majority vote.

    Returns (consensus_text, mean_similarity_to_candidates).
    """
    if len(texts) == 1:
        return texts[0], 1.0

    # Pick the medoid as the reference: the candidate with the highest average
    # similarity to all the others.
    best_idx, best_score = 0, -1.0
    for i, t in enumerate(texts):
        avg = sum(_text_similarity(t, o) for j, o in enumerate(texts) if j != i)
        avg /= max(1, len(texts) - 1)
        if avg > best_score:
            best_idx, best_score = i, avg
    reference = texts[best_idx]

    # Align every candidate to the reference and vote character by character.
    voters: list[dict[int, str]] = []
    for t in texts:
        mapping: dict[int, str] = {}
        matcher = difflib.SequenceMatcher(None, reference, t, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    mapping[i1 + k] = t[j1 + k]
            elif tag == "replace":
                for k in range(i2 - i1):
                    if j1 + k < j2:
                        mapping[i1 + k] = t[j1 + k]
            # 'insert' and 'delete' produce no vote for the reference position.
        voters.append(mapping)

    out: list[str] = []
    for i, ch in enumerate(reference):
        votes: dict[str, int] = {}
        for v in voters:
            c = v.get(i)
            if c is not None:
                votes[c] = votes.get(c, 0) + 1
        if not votes:
            out.append(ch)
            continue
        # Tie-break towards the reference character so we never drift.
        best = max(votes.items(), key=lambda kv: (kv[1], kv[0] == ch))
        out.append(best[0])

    consensus = "".join(out)
    sims = [_text_similarity(consensus, t) for t in texts]
    return consensus, sum(sims) / len(sims)


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------


def _align(candidates: list[Candidate]) -> list[list[Candidate]]:
    """Group the candidates into lines.

    Candidates are considered in order of confidence and each one joins the
    group it matches best.  Unlike a strict one-to-one matching this lets a
    third engine join a group of two when two engines agree and the third
    reads the same line slightly differently - which is exactly the case the
    consensus exists to resolve.
    """
    groups: list[list[Candidate]] = []
    for cand in sorted(candidates, key=lambda c: c.confidence, reverse=True):
        best_group: list[Candidate] | None = None
        best_score = 0.0
        for group in groups:
            score = max(_match_score(cand, member) for member in group)
            if score > best_score:
                best_group, best_score = group, score
        if best_group is not None and best_score >= ALIGN_THRESHOLD:
            best_group.append(cand)
        else:
            groups.append([cand])
    return groups


def _merge_bbox(boxes: list[list[float] | None]) -> list[float] | None:
    valid = [b for b in boxes if b]
    if not valid:
        return None
    return [
        min(b[0] for b in valid),
        min(b[1] for b in valid),
        max(b[2] for b in valid),
        max(b[3] for b in valid),
    ]


def _pick_type(cands: list[Candidate]) -> str:
    """Majority vote on the block type; ties go to the highest-confidence one."""
    counts: dict[str, float] = {}
    for c in cands:
        counts[c.btype] = counts.get(c.btype, 0.0) + c.confidence
    best = max(counts.items(), key=lambda kv: kv[1])[0]
    return best


def _pick_field(cands: list[Candidate], field_name: str) -> str | None:
    for c in sorted(cands, key=lambda c: c.confidence, reverse=True):
        val = getattr(c, field_name, None)
        if val:
            return val
    return None


def _detect_script(text: str) -> str:
    if any(0x0900 <= ord(ch) <= 0x097F for ch in text):
        return "devanagari"
    if any(ch.isascii() and ch.isalpha() for ch in text):
        return "latin"
    return "unknown"


def build_consensus(results: list[EngineResult]) -> list[ConsensusBlock]:
    """Fuse the per-engine block lists into one consensus list."""
    candidates: list[Candidate] = []
    for res in results:
        for b in res.blocks:
            candidates.append(
                Candidate(
                    engine_id=res.engine_id,
                    model=res.model,
                    text=b.text,
                    bbox=b.bbox,
                    confidence=b.confidence,
                    btype=b.type,
                    question_number=b.question_number,
                    marks=b.marks,
                    script=b.script,
                )
            )

    groups = _align(candidates)
    out: list[ConsensusBlock] = []
    for group in groups:
        texts = [c.text for c in group]
        consensus_text, mean_sim = _char_vote(texts)
        engine_texts = {c.engine_id: c.text for c in group}
        best = max(group, key=lambda c: c.confidence)

        cb = ConsensusBlock(
            candidates=group,
            text=consensus_text,
            agreement=round(mean_sim, 3),
            bbox=_merge_bbox([c.bbox for c in group]),
            btype=_pick_type(group),
            question_number=_pick_field(group, "question_number"),
            marks=_pick_field(group, "marks"),
            script=_detect_script(consensus_text),
            engine_texts=engine_texts,
        )

        # Confidence blends the engines' own scores with their agreement.
        avg_self = sum(c.confidence for c in group) / len(group)
        cb.confidence = round(min(1.0, 0.6 * avg_self + 0.4 * mean_sim), 3)

        if len(group) == 1:
            # Another engine did not report this line at all.  That is only a
            # disagreement when we actually have something to compare against.
            if len(results) > 1:
                cb.flags.append(Flag.ENGINE_DISAGREEMENT)
        else:
            # Two engines reading the same line differently is a real defect
            # even when the strings are 95% similar - one character wrong in a
            # question is one character wrong.  We flag any *distinct* reading
            # and let the character vote decide which one wins.
            distinct = {_normalise_for_compare(c.text) for c in group}
            if len(distinct) > 1:
                cb.flags.append(Flag.ENGINE_DISAGREEMENT)
        if cb.confidence < LOW_CONFIDENCE_THRESHOLD:
            cb.flags.append(Flag.LOW_CONFIDENCE)
        out.append(cb)

    # Restore reading order: sort by vertical position, then horizontal.
    out.sort(key=lambda b: (
        round((b.bbox[1] if b.bbox else 1.0), 2),
        round((b.bbox[0] if b.bbox else 0.0), 2),
    ))
    return out


def to_blocks(consensus: list[ConsensusBlock]) -> list[Block]:
    """Convert consensus blocks into persisted Block models."""
    blocks: list[Block] = []
    for cb in consensus:
        try:
            btype = BlockType(cb.btype)
        except ValueError:
            btype = BlockType.UNKNOWN
        blocks.append(
            Block(
                type=btype,
                text=cb.text,
                bbox=cb.bbox,
                engine_texts=cb.engine_texts,
                confidence=cb.confidence,
                flags=list(dict.fromkeys(cb.flags)),
                review_status=ReviewStatus.PENDING,
                question_number=cb.question_number,
                marks=cb.marks,
                script=Script(cb.script) if cb.script in ("latin", "devanagari", "unknown")
                else Script.UNKNOWN,
            )
        )
    return blocks


def page_agreement(consensus: list[ConsensusBlock], engine_count: int) -> float:
    """Overall page-level agreement between the engines, 0..1."""
    if not consensus:
        return 0.0
    if engine_count <= 1:
        # Nothing to compare against - fall back to the engines' own scores.
        return round(sum(c.confidence for c in consensus) / len(consensus), 3)
    return round(sum(c.agreement for c in consensus) / len(consensus), 3)
