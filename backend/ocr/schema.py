"""The structured contract every OCR engine must satisfy.

One JSON schema drives Gemini's ``responseSchema``, OpenAI's
``response_format.json_schema`` and Anthropic's ``output_config.format`` so
all engines return the *same* shape and can be compared line by line.
"""

from __future__ import annotations

from typing import Any

BLOCK_TYPES = [
    "title",
    "subtitle",
    "section",
    "instruction",
    "question",
    "options",
    "subcompletion",
    "marks",
    "note",
    "diagram_caption",
    "table",
    "header",
    "footer",
    "unknown",
]

EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "language": {
            "type": "string",
            "enum": ["en", "hi", "sa", "mixed"],
            "description": "Primary language of the page.",
        },
        "page_title": {"type": "string"},
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": BLOCK_TYPES},
                    "text": {
                        "type": "string",
                        "description": "Verbatim transcription of the block.",
                    },
                    "bbox": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 4,
                        "maxItems": 4,
                        "description": "Normalised [x1,y1,x2,y2] in 0..1 image space.",
                    },
                    "confidence": {"type": "number"},
                    "question_number": {"type": "string"},
                    "marks": {"type": "string"},
                    "script": {
                        "type": "string",
                        "enum": ["latin", "devanagari", "unknown"],
                    },
                },
                "required": [
                    "type", "text", "bbox", "confidence",
                    "question_number", "marks", "script",
                ],
                "additionalProperties": False,
            },
        },
        "notes": {
            "type": "string",
            "description": "Anything illegible, ambiguous or worth flagging.",
        },
    },
    "required": ["language", "page_title", "blocks", "notes"],
    "additionalProperties": False,
}


#: Schema used by the "verify this transcription against the image" pass.
VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "corrections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "block_index": {
                        "type": "integer",
                        "description": "Index into the provided blocks array.",
                    },
                    "original": {"type": "string"},
                    "corrected": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["block_index", "original", "corrected", "reason", "confidence"],
                "additionalProperties": False,
            },
        },
        "overall_note": {"type": "string"},
    },
    "required": ["corrections", "overall_note"],
    "additionalProperties": False,
}


def clean_for_gemini(schema: dict[str, Any]) -> dict[str, Any]:
    """Gemini's responseSchema rejects some JSON-Schema keywords."""
    forbidden = {"additionalProperties", "$schema", "minItems", "maxItems", "default"}
    return _strip(schema, forbidden)


def _strip(node: Any, forbidden: set[str]) -> Any:
    if isinstance(node, dict):
        return {
            k: _strip(v, forbidden)
            for k, v in node.items()
            if k not in forbidden
        }
    if isinstance(node, list):
        return [_strip(v, forbidden) for v in node]
    return node
