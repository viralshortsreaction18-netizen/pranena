"""Recognise the structure of an exam paper from a flat list of text blocks.

Works with two inputs:
  * the semantic ``type`` an OCR engine assigned to a block, and
  * purely textual heuristics (regexes) that survive a weak/partial engine.

The goal is not to be clever but to be *reliable*: every detection is a
heuristic that either confirms what the engine said or corrects it, and every
ambiguous case is left alone so the reviewer can decide.
"""

from __future__ import annotations

import re

from ..schemas import Block, BlockType, Flag

# ---------------------------------------------------------------------------
# Section headers
# ---------------------------------------------------------------------------

# "Section A", "SECTION – B", "Part II", "भाग अ", "खण्ड-ब"
# The label group must accept Devanagari letters as well as Latin ones.
_DEVA_LABEL = "ऀ-ॿ"

SECTION_PATTERNS = [
    re.compile(r"^\s*(?:section|sec\.?|part|unit|chapter|भाग|खण्ड|खंड|अंश|अनुभाग|पाठ)\s*"
               r"[-–—:.]?\s*([a-zA-Z]{1,3}|[०-९0-9]{1,3}|[IVXivx]{1,5}|"
               r"[" + _DEVA_LABEL + r"]{1,3})\s*"
               r"(?:[.:\-–—)]|\s*$)", re.IGNORECASE),
    re.compile(r"^\s*([A-Za-z])\s*[.)]\s+(?=\S)"),          # "A. Something"
    re.compile(r"^\s*([IVX]{1,4})\s*[.)]\s+(?=\S)"),        # "IV. Something"
    re.compile(r"^\s*([" + _DEVA_LABEL + r"])\s*[.)]\s+(?=\S)"),  # "अ. Something"
]

# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------

#: English instruction openers.
_EN_INSTRUCTION_STARTERS = (
    "instruction", "instructions", "general instruction", "general instructions",
    "note", "notes", "direction", "directions", "guideline", "guidelines",
    "remarks", "important note",
)

#: Hindi / Sanskrit instruction openers.
_DEVA_INSTRUCTION_STARTERS = (
    "निर्देश", "सूचना", "नोट", "सामान्य निर्देश", "अनुदेश", "आदेश",
    "विशेष निर्देश", "ध्यान दें", "ध्यान दीजिए", "नोट:-", "नोट –",
    "आवश्यक सूचना",
)

INSTRUCTION_STARTERS = _EN_INSTRUCTION_STARTERS + _DEVA_INSTRUCTION_STARTERS

# NOTE: we cannot use ``\b`` after a Devanagari alternative.  Python's ``\b``
# only treats characters as "word" characters when they are alphanumeric, and
# Devanagari vowel signs (category Mc) are not - so "सूचना " would fail the
# boundary test.  Instead we assert that no further Devanagari character
# follows, which means the opener was matched in full.
INSTRUCTION_RE = re.compile(
    r"^\s*(?:"
    r"(?:" + "|".join(re.escape(s) for s in _EN_INSTRUCTION_STARTERS) + r")\b"
    r"|"
    r"(?:" + "|".join(re.escape(s) for s in _DEVA_INSTRUCTION_STARTERS) + r")"
    r"(?![" + _DEVA_LABEL + r"])"
    r")",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Question numbering
# ---------------------------------------------------------------------------

QUESTION_PATTERNS = [
    # 1.  2)  3)   12.  13)
    re.compile(r"^\s*(\d{1,3})\s*([.)])\s+"),
    # (1)  (2)
    re.compile(r"^\s*[\(\[]\s*(\d{1,3})\s*[\)\]]\s*"),
    # Q1  Q.1  Q 1
    re.compile(r"^\s*(?:q(?:uestion)?\.?)\s*(\d{1,3})\s*[.):-]?\s+", re.IGNORECASE),
    # प्रश्न 1  प्रश्न-1  प्र. 1
    re.compile(r"^\s*(?:प्रश्न|प्र\.|प्रश्)\s*[-–—.]?\s*(\d{1,3})\s*[.):-]?\s*"),
    # 1.  Sanskrit digit numbering
    re.compile(r"^\s*([०-९]{1,3})\s*([.)])\s+"),
]

# Marks: "(5)", "[5]", "5 marks", "— 5", "5 अंक", "(2½)"
MARKS_RE = re.compile(
    r"(?:\(\s*(\d{1,3}(?:\.\d{1,2})?|[०-९]{1,3}(?:\.\d{1,2})?)\s*\)"
    r"|\[\s*(\d{1,3}|[०-९]{1,3})\s*\]"
    r"|(?:[—–\-]\s*)?(\d{1,3}|[०-९]{1,3})\s*(?:marks?|अंक|अंकों|marks)\b"
    r"|(?:[—–\-])\s*(\d{1,3}|[०-९]{1,3})\s*$)"
)

# Options: "(a) x (b) y", "(i) x (ii) y", "a) x b) y"
OPTION_LETTERS = "abcdABCD"
OPTION_ROMAN = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]


def _option_positions(text: str) -> list[tuple[int, str]]:
    """Find option markers and their positions."""
    found: list[tuple[int, str]] = []
    # (a) / (b) / a) / b)
    for m in re.finditer(r"[\(\[]?\s*([a-dA-D])\s*[\)\]]\s*(?=\S)", text):
        found.append((m.start(), m.group(1)))
    # (i) / (ii)
    for m in re.finditer(r"[\(\[]\s*(i{1,3}v?|iv|v|vi{0,3}|ix|x)\s*[\)\]]\s*(?=\S)",
                         text, re.IGNORECASE):
        found.append((m.start(), m.group(1).lower()))
    found.sort()
    return found


def looks_like_options(text: str) -> bool:
    pos = _option_positions(text)
    if len(pos) < 2:
        return False
    labels = [p[1].lower() for p in pos]
    # Sequential a,b,c,d  OR  i,ii,iii,iv
    if labels[:2] == ["a", "b"] or labels[:2] == ["i", "ii"]:
        return True
    return len(labels) >= 3


def split_options(text: str) -> list[tuple[str, str]]:
    """Split 'a) one b) two c) three' into [(label, body), ...]."""
    pos = _option_positions(text)
    if not pos:
        return []
    out: list[tuple[str, str]] = []
    for i, (start, label) in enumerate(pos):
        end = pos[i + 1][0] if i + 1 < len(pos) else len(text)
        body = text[start:end].strip()
        body = re.sub(r"^\s*[\(\[]?\s*[a-dA-Di]{1,3}\s*[\)\]]?\s*[.)]?\s*", "", body)
        out.append((label, body.strip()))
    return out


# ---------------------------------------------------------------------------
# Question detection
# ---------------------------------------------------------------------------


def detect_question_number(text: str) -> str | None:
    for pat in QUESTION_PATTERNS:
        m = pat.match(text)
        if m:
            return m.group(1)
    return None


def detect_marks(text: str) -> str | None:
    m = MARKS_RE.search(text)
    if not m:
        return None
    for g in m.groups():
        if g:
            return g
    return None


def strip_marks(text: str) -> tuple[str, str | None]:
    """Remove a trailing marks annotation, returning (clean_text, marks)."""
    marks = detect_marks(text)
    if not marks:
        return text, None
    cleaned = MARKS_RE.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    cleaned = re.sub(r"[\(\[\]]\s*$", "", cleaned).strip()
    cleaned = re.sub(r"[—–\-]\s*$", "", cleaned).strip()
    return cleaned, marks


def detect_section(text: str) -> str | None:
    for pat in SECTION_PATTERNS:
        m = pat.match(text)
        if m:
            return m.group(1)
    return None


def detect_instruction(text: str) -> bool:
    return bool(INSTRUCTION_RE.match(text))


# ---------------------------------------------------------------------------
# Structure inference over a page
# ---------------------------------------------------------------------------


def infer_structure(blocks: list[Block]) -> list[Block]:
    """Post-process a page's blocks: fix types, extract numbers and marks.

    The function is conservative.  It only rewrites a block's type when the
    text evidence is strong, and it always keeps the original text intact.
    """
    for i, block in enumerate(blocks):
        text = (block.text or "").strip()

        # --- empty blocks -------------------------------------------------
        if not text:
            if block.type not in (BlockType.MARKS,):
                block.type = BlockType.UNKNOWN
                block.flags.append(Flag.EMPTY)
            continue

        # --- marks extraction ---------------------------------------------
        if block.type == BlockType.QUESTION:
            cleaned, marks = strip_marks(text)
            if marks:
                block.marks = marks
                block.text = cleaned
                text = cleaned

        # --- question numbering -------------------------------------------
        if block.type in (BlockType.QUESTION, BlockType.SUBCOMPLETION, BlockType.UNKNOWN):
            qnum = detect_question_number(text)
            if qnum and block.question_number is None:
                block.question_number = qnum
                if block.type in (BlockType.UNKNOWN, BlockType.SUBCOMPLETION):
                    block.type = BlockType.QUESTION

        # --- options -------------------------------------------------------
        if block.type in (BlockType.OPTIONS, BlockType.UNKNOWN) and looks_like_options(text):
            if block.type == BlockType.UNKNOWN:
                block.type = BlockType.OPTIONS

        # --- instructions ---------------------------------------------------
        if block.type in (BlockType.INSTRUCTION, BlockType.UNKNOWN) and detect_instruction(text):
            block.type = BlockType.INSTRUCTION

        # --- section headers ------------------------------------------------
        if block.type in (BlockType.SECTION, BlockType.UNKNOWN, BlockType.TITLE):
            sec = detect_section(text)
            if sec and (block.type == BlockType.UNKNOWN or len(text) < 80):
                block.type = BlockType.SECTION

        # --- title ------------------------------------------------------------
        if i == 0 and block.type == BlockType.UNKNOWN and len(text) < 120:
            block.type = BlockType.TITLE

    _link_subquestions(blocks)
    return blocks


def _link_subquestions(blocks: list[Block]) -> None:
    """Mark '(a)/(i)' style sub-parts that belong to the previous question."""
    seen_question = False
    for block in blocks:
        if block.type == BlockType.QUESTION:
            seen_question = True
            continue
        if not seen_question:
            continue
        if block.type == BlockType.OPTIONS:
            continue
        text = (block.text or "").strip()
        if re.match(r"^\s*[\(\[]?\s*(?:[a-dA-D]|i{1,3}|iv|v)\s*[\)\]]\s*", text):
            if block.type == BlockType.UNKNOWN:
                block.type = BlockType.SUBCOMPLETION


def summarize(blocks: list[Block]) -> dict[str, object]:
    """Counts used by the UI header."""
    types: dict[str, int] = {}
    for b in blocks:
        types[b.type.value] = types.get(b.type.value, 0) + 1
    questions = [b for b in blocks if b.type == BlockType.QUESTION]
    marks_total = 0.0
    for b in questions:
        if b.marks:
            try:
                marks_total += float(b.marks)
            except ValueError:
                pass
    return {
        "types": types,
        "questions": len(questions),
        "marks_total": marks_total,
        "sections": [b.text for b in blocks if b.type == BlockType.SECTION],
    }
