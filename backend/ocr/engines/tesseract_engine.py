"""Tesseract engine - local, free, offline.

Tesseract is good at *printed* text and weak on handwriting (word accuracy
around 20-40% on cursive).  It is included because it is genuinely useful for
the printed headings, rubrics and typed answer sheets that appear on many exam
papers, and because it costs nothing and never leaves the machine.
"""

from __future__ import annotations

import asyncio
import csv
import io
import shutil
import tempfile
from pathlib import Path

from ...schemas import EngineSpec
from ..base import BaseEngine, EngineError
from ..imageprep import PrepResult


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def available_languages() -> list[str]:
    if not tesseract_available():
        return []
    try:
        out = __import__("subprocess").run(
            ["tesseract", "--list-langs"], capture_output=True, text=True, timeout=20
        ).stdout
        return [l.strip() for l in out.splitlines()[1:] if l.strip()]
    except Exception:
        return []


class TesseractEngine(BaseEngine):
    id = "tesseract"

    def __init__(self, spec: EngineSpec):
        super().__init__(spec)
        if not tesseract_available():
            raise EngineError(
                "Tesseract is not installed. Install it with: "
                "apt-get install tesseract-ocr tesseract-ocr-hin tesseract-ocr-san"
            )
        self.langs = spec.model or "eng"

    def _run(self, prep: PrepResult) -> list[dict]:
        import subprocess

        with tempfile.TemporaryDirectory() as td:
            img_path = Path(td) / "page.png"
            img_path.write_bytes(prep.data)
            try:
                proc = subprocess.run(
                    ["tesseract", str(img_path), "stdout", "-l", self.langs, "tsv"],
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
            except subprocess.TimeoutExpired as exc:
                raise EngineError("Tesseract timed out") from exc
            if proc.returncode != 0:
                raise EngineError(f"Tesseract failed: {proc.stderr[:300]}")

        rows: list[dict] = []
        reader = csv.DictReader(io.StringIO(proc.stdout), delimiter="\t", quoting=csv.QUOTE_NONE)
        width = height = 1
        words: dict[tuple[int, int, int], dict] = {}
        for row in reader:
            try:
                level = int(row.get("level") or 0)
            except ValueError:
                continue
            if level == 1:
                try:
                    width = int(row.get("width") or 1)
                    height = int(row.get("height") or 1)
                except ValueError:
                    pass
                continue
            if level != 5:
                continue
            text = (row.get("text") or "").strip()
            if not text:
                continue
            try:
                key = (
                    int(row["block_num"]),
                    int(row["par_num"]),
                    int(row["line_num"]),
                )
                left, top = int(row["left"]), int(row["top"])
                w, h = int(row["width"]), int(row["height"])
                conf = float(row.get("conf") or 0)
            except (KeyError, ValueError):
                continue
            entry = words.setdefault(
                key,
                {
                    "words": [],
                    "left": left,
                    "top": top,
                    "right": left + w,
                    "bottom": top + h,
                    "conf": [],
                },
            )
            entry["words"].append(text)
            entry["left"] = min(entry["left"], left)
            entry["top"] = min(entry["top"], top)
            entry["right"] = max(entry["right"], left + w)
            entry["bottom"] = max(entry["bottom"], top + h)
            if conf >= 0:
                entry["conf"].append(conf)

        for entry in words.values():
            conf = (sum(entry["conf"]) / len(entry["conf"]) / 100.0) if entry["conf"] else 0.0
            rows.append({
                "type": "unknown",
                "text": " ".join(entry["words"]),
                "bbox": [
                    entry["left"] / max(1, width),
                    entry["top"] / max(1, height),
                    entry["right"] / max(1, width),
                    entry["bottom"] / max(1, height),
                ],
                "confidence": max(0.0, min(1.0, conf)),
                "question_number": "",
                "marks": "",
                "script": "unknown",
            })
        return rows

    async def extract(self, prep: PrepResult, language: str) -> "object":  # type: ignore[name-defined]
        from ..base import EngineResult, RawBlock

        rows = await asyncio.to_thread(self._run, prep)
        blocks = [
            RawBlock(
                type=r["type"],
                text=r["text"],
                bbox=r["bbox"],
                confidence=r["confidence"],
                question_number=r["question_number"] or None,
                marks=r["marks"] or None,
                script=r["script"],
            )
            for r in rows
        ]
        return EngineResult(
            engine_id=self.id,
            model=self.model,
            language=language,
            blocks=blocks,
            notes="Tesseract output - printed text only.",
        )

    async def verify(self, prep: PrepResult, blocks: list[dict], language: str) -> dict:
        return {"corrections": [], "overall_note": "Tesseract cannot verify."}

    async def _request_json(
        self,
        prompt: str,
        prep: PrepResult,
        *,
        schema_kind: str = "extraction",
        extra_payload: list[dict] | None = None,
    ) -> dict:
        from ..base import EngineError

        raise EngineError("Tesseract does not support the JSON engine protocol.")
