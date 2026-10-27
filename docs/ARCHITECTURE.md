# How Pranena works

```
  page photo
      │
      ▼
┌──────────────────────┐
│ imageprep            │  EXIF orientation → 90/180/270° detection →
│ (imageprep.py)       │  deskew → contrast → resize to 2000 px
└──────────┬───────────┘
           │  prepared image (this is what the model sees)
           ▼
┌──────────────────────┐
│ engines (parallel)   │  Anthropic · Gemini · OpenAI-compatible ·
│                      │  Ollama · Tesseract · demo
└──────────┬───────────┘
           │  blocks: {type, text, bbox, confidence}
           ▼
┌──────────────────────┐
│ consensus            │  align by IoU + text similarity →
│ (consensus.py)       │  character-level majority vote →
│                      │  agreement score → flags
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ postprocess          │  Devanagari NFC + nukta → structure inference
│                      │  (questions, marks, options) → QA flags
└──────────┬───────────┘
           │
           ▼
       review queue  ←  human edits, AI verification pass
           │
           ▼
┌──────────────────────┐
│ export/docx_builder  │  complex-script fonts (w:cs, w:szCs, w:bCs),
│                      │  right-tab marks column, hanging indents
└──────────────────────┘
```

## Why the bounding boxes matter

The bounding boxes are expressed in the coordinate space of the **prepared**
image, not the raw upload — because the image is rotated, deskewed and padded
before the model sees it. The server therefore stores both and the UI displays
the prepared one, so the highlighted box lines up with what was actually
read.

## Why consensus is character-level

Two engines rarely disagree on *everything*. They disagree on one glyph —
`97` versus `9T`, or `श` versus `ष`. A whole-block vote would throw away the
rest of the line; a character-level vote keeps it and fixes only the
character that a majority of engines agree on.

## The DOCX complex-script trap

Word resolves a run's typeface through four slots. A Devanagari character is a
*complex script*, so Word reads:

* `w:rFonts/@w:cs`  — the typeface
* `w:szCs`         — the size
* `w:bCs` / `w:iCs`— bold and italic
* `w:lang/@w:bidi` — the language

Most libraries write only `w:ascii` / `w:hAnsi` / `w:sz` / `w:b`. Hindi and
Sanskrit then render in a fallback face — or as boxes. `docx_builder.py`
sets all four slots on every run *and* in `styles.xml`'s `docDefaults`.

## The Devanagari layer

Hindi and Sanskrit share one script, and handwriting OCR in that script fails
in *predictable* ways. `backend/postprocess/devanagari.py` turns each failure
into a review flag instead of a silent error.

### Lexicons

The spell-check lexicons ship as plain text next to the module, so they can be
extended without touching Python:

| File | Contents |
|---|---|
| `backend/postprocess/lexicon_hi.txt` | ~2 000 Hindi / school-paper words |
| `backend/postprocess/lexicon_sa.txt` | ~3 100 distinctly Sanskrit words |

One bare Devanagari word per line, `#` starts a comment. Entries are *stems*:
the suffix-stripping pass below turns `प्रश्नों` into `प्रश्न`. The loader
(`_load_lexicon`) tolerates a missing file, so an empty lexicon degrades the
spell-check instead of breaking the import.

### Word tokenisation

The word pattern excludes the danda pair (U+0964/0965) and the avagraha
(U+093D). Without that, `है।` is one token and never matches the lexicon
entry `है`, and `धर्मोऽर्थः` is one token instead of two words.

### Nukta positioning

A nukta belongs to the *consonant*, and always sits between the consonant and
its vowel sign. Handwriting puts the dot in the wrong place constantly, so
`normalize_nukta()` repairs both directions:

| Input | Output | Why |
|---|---|---|
| `बडी़` | `बड़ी` | the dot landed under the vowel sign |
| `़क` | `क़` | the dot was written in front of the letter |
| `क़ी` | `क़ी` | already correct — left alone |

A nukta that already follows a letter (or a virama) is never moved, so
`क़न` and `क़ा` survive untouched.

### Look-alike detection, without the noise

`श`, `स`, `ष`, `न`, `ण`, `द`, `ध`, `ब`, `व` … are all *genuinely* ambiguous
in handwriting, and all of them occur in perfectly correct Hindi. A raw
look-alike scan therefore flags nearly every line of a Devanagari paper and
buries the real problems.

`find_confusable_words()` applies two conditions before it reports anything:

1. the word is **not** in the lexicon (even after the inflectional suffixes
   have been stripped), and
2. swapping one ambiguous character for one of its look-alikes produces a
   word that **is** in the lexicon.

Condition 2 is what makes the flag actionable. `शमय` is reported as a
possible mis-reading of `समय`, with the suggestion attached to the flag.
A word that is merely absent from the lexicon gets the softer
`unknown_word` flag instead — "we do not recognise this word" is a different
claim from "this word is wrong in exactly this way".

Two further guards keep the suggestions honest:

* a suggestion must be a **concrete lexicon entry**, not a fragment
  produced by stripping a suffix (`हें` is two characters long but it is not
  a word, so it is never offered as a fix);
* two-character words are accepted as known without a lookup, because in
  Devanagari they are overwhelmingly grammatical particles (`है`, `का`,
  `को`, `में`) and flagging them would produce pure noise.

`find_lookalikes()` is still exported for callers that want every ambiguity,
unfiltered.

## Flags

| Flag | Meaning |
|---|---|
| `engine_disagreement` | the engines read this line differently |
| `low_confidence` | confidence below 70 % |
| `devanagari_confusion` | a word that is unknown but becomes a known word after swapping a look-alike character — or an invalid codepoint / virama |
| `unknown_word` | a word that is not in the Hindi/Sanskrit/English lexicon |
| `missing_marks` | a question with no marks value |
| `suspect_truncation` | the line looks cut off or partly illegible |
