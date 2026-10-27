"""Demo engine - a deterministic, key-free stand-in for a real OCR engine.

It exists so the whole application (upload -> blocks -> review -> Word export)
can be explored and tested without an API key.  It synthesises a realistic
handwritten-exam-paper transcription for the selected language, including a
couple of deliberately low-confidence lines so the review workflow is visible.
"""

from __future__ import annotations

import hashlib

from ...schemas import EngineSpec
from ..base import BaseEngine
from ..imageprep import PrepResult

_EN_PAPER = [
    ("title", "ANNUAL EXAMINATION 2025-26", [0.18, 0.04, 0.82, 0.09]),
    ("subtitle", "Subject: Mathematics        Class: X        Time: 3 Hours", [0.06, 0.10, 0.94, 0.14]),
    ("subtitle", "Maximum Marks: 80", [0.35, 0.14, 0.65, 0.18]),
    ("instruction", "Instructions: (i) All questions are compulsory. (ii) Write the answers neatly and legibly in the space provided. (iii) Use of calculators is not allowed.", [0.06, 0.19, 0.94, 0.27]),
    ("section", "SECTION A - Objective Questions (1 x 10 = 10 marks)", [0.06, 0.29, 0.94, 0.33]),
    ("question", "1. The largest prime number below 100 is:  [1]", [0.08, 0.35, 0.72, 0.39]),
    ("options", "(a) 97    (b) 89    (c) 91    (d) 99", [0.10, 0.40, 0.80, 0.44]),
    ("question", "2. The value of 15% of 240 is:  [1]", [0.08, 0.45, 0.70, 0.49]),
    ("options", "(a) 36    (b) 30    (c) 40    (d) 45", [0.10, 0.50, 0.80, 0.54]),
    ("question", "3. The angle of a regular hexagon is:  [1]", [0.08, 0.55, 0.72, 0.59]),
    ("options", "(a) 60 degrees    (b) 90 degrees    (c) 120 degrees    (d) 45 degrees", [0.10, 0.60, 0.85, 0.64]),
    ("section", "SECTION B - Short Answer Questions (2 x 5 = 10 marks)", [0.06, 0.66, 0.94, 0.70]),
    ("question", "4. The sum of the first 20 terms of the arithmetic progression 3, 7, 11, ... is:  [2]", [0.08, 0.72, 0.90, 0.77]),
    ("question", "5. In a right triangle, if one acute angle is 35 degrees, the other acute angle is:  [2]", [0.08, 0.79, 0.90, 0.84]),
    ("question", "6. The quadratic equation x^2 - 5x + 6 = 0 has roots:  [2]", [0.08, 0.86, 0.80, 0.91]),
]

_HI_PAPER = [
    ("title", "वार्षिक परीक्षा 2025-26", [0.22, 0.04, 0.78, 0.09]),
    ("subtitle", "विषय: गणित        कक्षा: दस        समय: 3 घंटे        अधिकतम अंक: 80", [0.06, 0.10, 0.94, 0.14]),
    ("instruction", "निर्देश: (i) सभी प्रश्न अनिवार्य हैं। (ii) उत्तर साफ और सुवाच्य लिखें। (iii) कैलकुलेटर का प्रयोग वर्जित है।", [0.06, 0.15, 0.94, 0.23]),
    ("section", "खण्ड अ - बहुविकल्पीय प्रश्न (1 x 10 = 10 अंक)", [0.06, 0.25, 0.94, 0.29]),
    ("question", "1. 100 से छोटी सबसे बड़ी अभाज्य संख्या है:  [1]", [0.08, 0.31, 0.80, 0.35]),
    ("options", "(a) 97    (b) 89    (c) 91    (d) 99", [0.10, 0.36, 0.80, 0.40]),
    ("question", "2. 240 का 15% मान है:  [1]", [0.08, 0.41, 0.70, 0.45]),
    ("options", "(a) 36    (b) 30    (c) 40    (d) 45", [0.10, 0.46, 0.80, 0.50]),
    ("question", "3. सम नियमित षट्भुज का प्रत्येक कोण होता है:  [1]", [0.08, 0.51, 0.80, 0.55]),
    ("options", "(a) 60 डिग्री    (b) 90 डिग्री    (c) 120 डिग्री    (d) 45 डिग्री", [0.10, 0.56, 0.88, 0.60]),
    ("section", "खण्ड ब - लघु उत्तरीय प्रश्न (2 x 5 = 10 अंक)", [0.06, 0.62, 0.94, 0.66]),
    ("question", "4. समांतर श्रेणी 3, 7, 11, ... के प्रथम 20 पदों का योग है:  [2]", [0.08, 0.68, 0.92, 0.72]),
    ("question", "5. समकोण त्रिभुज में यदि एक न्यून कोण 35 डिग्री हो, तो दूसरा न्यून कोण होगा:  [2]", [0.08, 0.74, 0.94, 0.79]),
    ("question", "6. द्विघात समीकरण x^2 - 5x + 6 = 0 के मूल हैं:  [2]", [0.08, 0.81, 0.86, 0.85]),
]

_SA_PAPER = [
    ("title", "वार्षिक परीक्षा - संस्कृतम्", [0.24, 0.04, 0.76, 0.09]),
    ("subtitle", "विषयः संस्कृतम्    कक्षाः दशमी    समयः 3 घण्टा    अधिकतम अंकः ८०", [0.06, 0.10, 0.94, 0.14]),
    ("instruction", "सूचना - सर्वे प्रश्नाः अनिवार्याः सन्ति। उत्तराणि स्पष्टरूपेण लिखत।", [0.06, 0.15, 0.94, 0.21]),
    ("section", "प्रथमः खण्डः - बहुविकल्पिकाः प्रश्नाः (१ x १० = १० अंकाः)", [0.06, 0.23, 0.94, 0.27]),
    ("question", "१. 'धर्मक्षेत्रे कुरुक्षेत्रे' इत्यत्र 'धर्मः' शब्दस्य अर्थः अस्ति:  [1]", [0.08, 0.29, 0.94, 0.34]),
    ("options", "(क) युद्धम्    (ख) धर्मचक्रम्    (ग) क्षेत्रम्    (घ) युद्धविवाहः", [0.10, 0.35, 0.92, 0.39]),
    ("question", "२. 'सत्यम् वद धर्मम् चर' - एतस्य वाक्यस्य कर्ता कः अस्ति?  [1]", [0.08, 0.41, 0.92, 0.45]),
    ("options", "(क) भीष्मः    (ख) कृष्णः    (ग) भीमः    (घ) द्रौपदी", [0.10, 0.47, 0.88, 0.51]),
    ("question", "३. संस्कृतसाहित्ये प्रथमः कविः कः अस्ति?  [1]", [0.08, 0.53, 0.80, 0.57]),
    ("section", "द्वितीयः खण्डः - लघूत्तरीयाः प्रश्नाः (२ x ५ = १० अंकाः)", [0.06, 0.60, 0.94, 0.64]),
    ("question", "४. धर्मस्य लक्षणं निरूपयतु।  [2]", [0.08, 0.66, 0.70, 0.70]),
    ("question", "५. संस्कृतभाषायाः उत्पत्तिः कस्मात् आजायत?  [2]", [0.08, 0.72, 0.86, 0.76]),
    ("question", "६. 'अहिंसा' शब्दस्य सार्थकं विवरणं लिखत।  [2]", [0.08, 0.78, 0.86, 0.82]),
]


class DemoEngine(BaseEngine):
    """Produces a realistic sample transcription without touching the network."""

    id = "demo"

    def __init__(self, spec: EngineSpec):
        super().__init__(spec)

    async def extract(self, prep: PrepResult, language: str) -> "object":  # type: ignore[name-defined]
        from ..base import EngineResult, RawBlock

        lang = language if language in ("en", "hi", "sa") else "mixed"
        table = {"en": _EN_PAPER, "hi": _HI_PAPER, "sa": _SA_PAPER}.get(lang, _EN_PAPER)

        # Make the output deterministic per image so re-running is stable.
        seed = hashlib.sha256(prep.data[:4096]).hexdigest()
        jitter = int(seed[:2], 16) / 255.0

        blocks: list[RawBlock] = []
        for idx, (btype, text, bbox) in enumerate(table):
            confidence = 0.97 - 0.04 * ((idx + int(jitter * 5)) % 3)
            # Mark one line as genuinely uncertain so the review queue is not empty.
            if idx == 7:
                confidence = 0.54
            blocks.append(
                RawBlock(
                    type=btype,
                    text=text,
                    bbox=list(bbox),
                    confidence=round(confidence, 2),
                    question_number=None,
                    marks=None,
                    script="devanagari" if lang in ("hi", "sa") else "latin",
                )
            )

        title = next((t for t, x, _ in table if t == "title"), "")
        return EngineResult(
            engine_id=self.id,
            model=self.model,
            language=lang,
            page_title=title,
            blocks=blocks,
            notes=(
                "Demo engine: this is a synthetic sample, not a real reading of "
                "your image. Add an API key in Settings for genuine transcription."
            ),
            latency_ms=120,
        )

    async def verify(
        self, prep: PrepResult, blocks: list[dict], language: str
    ) -> dict:
        return {"corrections": [], "overall_note": "Demo engine - nothing to verify."}

    async def _request_json(
        self,
        prompt: str,
        prep: PrepResult,
        *,
        schema_kind: str = "extraction",
        extra_payload: list[dict] | None = None,
    ) -> dict:
        """Not used - the demo engine short-circuits extract()/verify()."""
        from ..base import EngineError

        raise EngineError("The demo engine does not make network requests.")
