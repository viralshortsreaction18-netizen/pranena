# Pranena — handwritten exam paper → editable Microsoft Word

Pranena turns a photograph of a **handwritten school examination paper** into a
clean, **fully editable `.docx`** file that opens in Microsoft Word (or
LibreOffice, or Google Docs) with correct Devanagari rendering.

It handles **English, हिन्दी (Hindi) and संस्कृतम् (Sanskrit)** — including
mixed-script papers — and it is built around a review workflow, because the
honest promise is not "zero errors" but **"nothing wrong gets through
unnoticed"**.

---

## Why a vision model, and not Tesseract?

Research on handwriting OCR (including the 2026 Devanagari stress-test
benchmark) is unambiguous:

| System | Handwriting (real scans) | Devanagari (real scans) |
|---|---|---|
| **Claude / Gemini / Mistral OCR** | **~89–95 %** | **chrF++ 77–86** |
| Qwen3-VL-8B (local, 24 GB GPU) | good | chrF++ 75.2 |
| Google Document AI | ~70–85 % | chrF++ 58.5 (on par with classic OCR) |
| Azure Document Intelligence | ~70–91 % | — |
| EasyOCR / Tesseract | 20–60 % | chrF++ 58.3 |

Classic OCR engines collapse on handwriting, and they collapse *furthest* on
Devanagari — exactly the scripts this tool is for. So Pranena uses **vision
language models** as the primary engine, and keeps Tesseract only as an
optional offline fallback.

---

## The accuracy model

No OCR engine is perfect, so Pranena does not pretend to be. It uses three
layers that together make errors *visible* instead of silent:

1. **Multi-engine consensus.** Run two or more engines over the same page.
   Their readings are aligned by geometry and text similarity, then fused with
   a character-level majority vote. Where they disagree, the line is flagged
   rather than guessed.
2. **An AI verification pass.** A second model is shown the *original image*
   and the current transcription side by side and is asked to report only
   genuine differences — dropped Devanagari matras, a mis-read digit, a wrong
   marks value. Nothing is auto-applied unless you accept it.
3. **A human review queue.** Every line carries a confidence score, a script
   tag, Devanagari look-alike warnings (श/ष, ट/ठ, ड/ढ, anusvara vs
   candrabindu, missing nukta) and a lexicon check. The review screen shows
   the page image with the line's bounding box next to the editable text, so
   verification is a glance, not an investigation.

---

## Features

- **Drag-and-drop page upload** — JPG, PNG, WebP; multiple pages; reordering.
- **Automatic image preparation** — EXIF orientation fix, 90°/180°/270°
  page-rotation detection (morphological text-direction analysis), small-angle
  deskew (projection-profile search), CLAHE contrast enhancement for faint
  pencil, and resizing to the resolution vision models actually use.
- **Structured extraction** — every line is classified as title, subtitle,
  section, instruction, question, options, sub-part, marks, note, caption or
  table, with a normalised bounding box.
- **Exam-paper aware** — question numbers and marks are extracted
  automatically (`(5)`, `[3]`, `5 marks`, `5 अंक`), MCQ options are
  recognised, and questions can be renumbered on export.
- **Devanagari post-processing** — NFC normalisation, nukta unification and
  *positioning* (क़ vs क + ़, and a dot that landed under the vowel sign),
  danda spacing rules, optional digit and danda conversion, and a
  Hindi/Sanskrit/English lexicon check.
- **Look-alike detection without the noise** — a word is only reported as a
  likely mis-read when it is unknown to the lexicon *and* swapping one
  ambiguous character (श/स/ष, ट/ठ, ड/ढ, द/ध, न/ण, ब/व …) turns it into a
  word the lexicon knows. The suggested correction is attached to the flag
  ("समय" for "शमय"), so the reviewer gets an answer, not a question mark.
- **In-place editor** — edit any line, change its type, set its marks, mark
  it reviewed. The image overlay stays in sync.
- **Word export that actually renders** — every run sets the
  *complex-script* font slot (`w:rFonts/@w:cs`), the complex-script size
  (`w:szCs`), bold (`w:bCs`) and language (`w:lang/@w:bidi`). This is the
  detail most tools get wrong, and it is the reason Hindi and Sanskrit
  otherwise appear as boxes or in the wrong typeface in Word.
- **Also exports** Markdown, HTML and plain text.
- **Works offline** with Ollama (Qwen3-VL) or Tesseract; the API keys you
  paste in are held in the running process and never written to disk.

---

## Quick start

```bash
git clone <your-repo> pranena
cd pranena

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

./run.sh                           # or: uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000>.

### 1. Add an API key (recommended)

Press **⚙ Settings** in the top bar and paste a key for one or more
providers. The best free option needs no card:

| Provider | Why | Free? |
|---|---|---|
| **Gemini** (`gemini-3.8-flash`) | Strongest on real Devanagari scans; free tier with no card | ✅ |
| **Claude** (`claude-sonnet-5-5`) | Excellent on messy handwriting; native JSON-schema output | paid |
| **OpenAI** (`gpt-5.1`) | Very accurate on clean handwriting | paid |
| **Mistral OCR** (`mistral-ocr-latest`) | Best internal handwriting score (88.9 %) | paid |
| **Ollama** (`qwen3-vl:8b`) | Fully offline; chrF++ 75.2 on Devanagari | free, needs a GPU |
| **Tesseract** | Free and instant, but printed text only | free |

Adding **two** providers is worth it: the engines then cross-check each
other and the consensus layer can tell you which lines are uncertain.

Without a key the app still runs with a **demo engine** that produces a
synthetic sample paper, so you can explore the whole workflow.

### 2. Use it

1. Drop the page photos into the left rail.
2. Pick the paper language (English / हिन्दी / संस्कृतम् / Mixed).
3. Press **▶ Read paper**.
4. Review: the page image is on the left with a box around every extracted
   line; the editable text is on the right. Click a line to see exactly where
   it came from. Orange dots mark lines that need a human eye.
5. Press **✓ Verify** for the AI proofread pass and accept any corrections.
6. Press **⭳ Export → Microsoft Word**.

---

## Configuration

Everything is optional; the defaults work.

| Environment variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude engine |
| `GEMINI_API_KEY` | Gemini engine |
| `OPENAI_API_KEY` | OpenAI engine |
| `MISTRAL_API_KEY` | Mistral OCR engine |
| `GROQ_API_KEY` | Groq (OpenAI-compatible) |
| `OPENROUTER_API_KEY` | OpenRouter (any model) |
| `PRANENA_DATA_DIR` | Where uploads and documents are stored (default `./data`) |

Models are chosen in the Settings panel, so you are never pinned to a model
ID that a provider retires.

---

## Project layout

```
backend/
  main.py                 FastAPI app
  api.py                  HTTP endpoints
  settings.py             provider registry & runtime config
  schemas.py              pydantic models (Document → Page → Block)
  store.py                document store + JSON persistence
  ocr/
    base.py               engine base class, JSON parsing, retries
    imageprep.py          rotation, deskew, contrast, resizing
    prompts.py            language-aware extraction & verification prompts
    schema.py             the JSON schema every engine must satisfy
    consensus.py          multi-engine alignment + character majority vote
    pipeline.py           per-page orchestration
    engines/              anthropic, gemini, openai-compatible, ollama,
                          tesseract, demo
  postprocess/
    devanagari.py         normalisation, look-alikes, lexicon
    lexicon_hi.txt        Hindi / school-paper word list (editable)
    lexicon_sa.txt        Sanskrit word list (editable)
    structure.py          questions, marks, sections, options, instructions
    verify.py             review-queue issue generation
  export/
    docx_builder.py       the Word writer (complex-script aware)
    __init__.py           Markdown / HTML / text writers
frontend/
  index.html, css/, js/   the single-page UI (no build step)
tests/
  test_*.py               130 pytest tests
  frontend/smoke.mjs      26 jsdom checks against a live server
```

---

## Tests

```bash
.venv/bin/python -m pytest            # 130 backend tests

# frontend (needs the server running)
cd tests/frontend && npm install && node smoke.mjs http://127.0.0.1:8000
```

The backend tests cover Devanagari normalisation, nukta positioning and
look-alike detection, the quality-flag precision guarantees (a clean Hindi or
Sanskrit paper must raise *no* flags), exam-paper structure inference, the
multi-engine consensus algorithm, image preparation, the DOCX writer
(including a check that every run carries the complex-script font slot) and
the full HTTP API.

---

## Notes and limits

- **Handwriting is hard.** A badly written 6 in a crowded answer box can still
  be read as a 0. That is why the tool shows you the original image next to
  the text and flags uncertainty instead of quietly choosing for you.
- **Science diagrams** are described, not redrawn. Export them as images and
  place them next to the extracted caption.
- **Mathematical notation** is written in plain text (`x^2`, `(a/b)`) so it
  stays editable; convert it to Word equations afterwards if you prefer.
- **Keys are not persisted.** They live in the server process; restarting the
  server clears them. That is deliberate for a tool that handles student data.
- Uploaded images are stored under `PRANENA_DATA_DIR` and are never sent
  anywhere except to the provider whose key you configured.

---

## License

MIT.
