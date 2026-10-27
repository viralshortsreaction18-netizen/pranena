"""Language-aware prompts for extraction and verification.

The extraction prompt is the single most important lever on accuracy.  It is
written to fight the three failure modes that hurt most:

  1. *Normalisation* – the model "correcting" a student's spelling or a
     teacher's typo.  We forbid it explicitly.
  2. *Hallucination* – the model completing a cut-off sentence.  We tell it
     to mark illegible text instead of guessing.
  3. *Script damage* – Devanagari matras, nukta and anusvara being dropped.
     We spell out the rules per script.
"""

from __future__ import annotations

from ..schemas import Language

LANGUAGE_LABEL = {
    "en": "English (Latin script)",
    "hi": "Hindi (Devanagari script)",
    "sa": "Sanskrit (Devanagari script, classical orthography)",
    "mixed": "Mixed - English and/or Hindi and/or Sanskrit",
}

LANGUAGE_FOCUS = {
    "en": """Transcribe English exactly as written. Preserve original spelling,
capitalisation, punctuation and any errors made by the writer. Do not
grammar-correct. If a word is illegible write [?] in its place.""",
    "hi": """Transcribe Hindi exactly as written in Devanagari. Every matra
(vowel sign) matters: write ख vs खा vs खि vs खी exactly as seen. Preserve
anusvara (ं) vs candrabindu (ँ) vs visarga (ः) exactly. Preserve nukta
(़) exactly - क़ is not क, ज़ is not ज. If a character is illegible write [?]
in its place rather than guessing.""",
    "sa": """Transcribe Sanskrit exactly as written in Devanagari, with classical
orthography preserved: final visarga (ः), anusvara (ं), sandhi forms, the
halant (्) before a following consonant or vowel, and Vedic accents if present.
Do not sandhi-correct or modernise the spelling. Preserve nukta exactly.
If a character is illegible write [?] rather than guessing.""",
    "mixed": """The page may mix English, Hindi and/or Sanskrit. Detect the script
of each line and transcribe each exactly as written. Keep Devanagari matras,
anusvara, candrabindu, visarga and nukta exactly as they appear - never drop
or "correct" a diacritic. Preserve original spelling in every language.
If a character is illegible write [?] rather than guessing.""",
}


def build_extraction_prompt(language: Language) -> str:
    focus = LANGUAGE_FOCUS.get(language, LANGUAGE_FOCUS["mixed"])
    label = LANGUAGE_LABEL.get(language, language)
    return f"""You are a meticulous OCR engine for handwritten school examination papers.

TASK
Read the attached image of a handwritten exam paper and transcribe EVERY piece of
text into structured blocks.

PRIMARY LANGUAGE: {label}

{focus}

ABSOLUTE RULES
1. Transcribe verbatim. Never translate. Never summarise. Never expand
   abbreviations. Never fix spelling, grammar or arithmetic.
2. Never invent content that is not visible. If part of a line is cut off or
   illegible, transcribe what is visible and put [?] exactly where the missing
   text is.
3. Preserve the writer's line breaks inside a block using \\n when a block
   spans multiple lines. Do not merge separate paragraphs into one block.
4. Preserve every mathematical symbol, superscript, fraction, unit and
   diagram label. Write fractions as (a/b) and superscripts as ^2.
5. Capture the marks/points annotation (e.g. "(5)", "[3]", "5 marks", "5 अंक")
   in the "marks" field of the block, and keep it out of "text".
6. Capture the question number in "question_number" (just the number, e.g. "3").
7. Give every block a bbox as [x1, y1, x2, y2] normalised to 0..1 with the
   origin at the TOP-LEFT of the image. Be precise - these drive the review
   overlay. Use [0,0,0,0] only if you truly cannot locate the block.
8. Set "confidence" between 0 and 1. Be honest: use a low value (< 0.6) when
   the handwriting is unclear, ambiguous or you had to guess.
9. Keep the reading order: blocks must appear in the order they are read.

BLOCK TYPES - choose exactly one per block:
- title           : the paper / exam / subject heading
- subtitle        : a secondary heading (class, date, duration, marks)
- section         : a section or part heading (Section A, भाग अ, "A. ...")
- instruction     : directions to the students ("Instructions:", "निर्देश:")
- question        : a numbered question
- options         : the answer choices of a question (a) (b) (c) (d)
- subcompletion   : a sub-part of a question (i), (ii), (a), (b)
- marks           : a standalone marks / points annotation
- note            : a note, remark or observation
- diagram_caption : text attached to a figure or diagram
- table           : tabular content (transcribe row by row with \\n)
- header          : running header at the very top of the page
- footer          : running footer, page number
- unknown         : you cannot classify it

Return ONLY the JSON object described by the schema. No prose, no markdown
fence, no commentary."""


def build_verification_prompt(language: Language) -> str:
    label = LANGUAGE_LABEL.get(language, language)
    return f"""You are proofreading a machine transcription of a handwritten exam
paper ({label}).

You are given the IMAGE and the CURRENT TRANSCRIPTION, block by block.

Compare them carefully and report ONLY genuine differences.

REPORT A CORRECTION WHEN:
- the image clearly shows different characters, words, numbers or symbols;
- characters were dropped, duplicated or swapped (especially Devanagari
  matras, anusvara, candrabindu, visarga and nukta);
- a word was mis-spelled or mis-split;
- a mark/points value is wrong;
- a block was mis-typed (e.g. a question typed as an instruction);
- text was truncated or hallucinated.

DO NOT REPORT A CORRECTION WHEN:
- the transcription matches the image (even if the handwriting itself is
  misspelt or the grammar is wrong - transcribe, do not edit);
- you are merely unsure - leave it alone;
- the difference is only whitespace or capitalisation.

For each correction give the 0-based index of the block in the array you were
given, the original text, the corrected text, a short reason, and a confidence
between 0 and 1 (how sure you are the image really supports the change).

If the transcription is accurate, return an empty corrections array.
Return ONLY the JSON object described by the schema."""


def build_multipage_prompt(language: Language) -> str:
    return f"""You are transcribing a multi-page handwritten exam paper.

The images are consecutive pages of the SAME paper, in order. Transcribe every
page into its own entry in the "pages" array, preserving global reading order.

{build_extraction_prompt(language)}

Add a top level "pages" array. Each entry contains "page_index" (0-based) and a
"blocks" array with the schema described above."""
