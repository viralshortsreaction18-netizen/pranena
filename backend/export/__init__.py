"""Export formats: .docx (primary), plus Markdown / HTML / plain text."""

from __future__ import annotations

import html
import re

from ..schemas import Block, BlockType, Document as DocModel

from .docx_builder import build_docx  # noqa: F401

__all__ = ["build_docx", "build_markdown", "build_html", "build_text"]


def _clean_number_prefix(text: str) -> str:
    return re.sub(
        r"^\s*(?:\d{1,3}|[०-९]{1,3})\s*[.)]\s*"
        r"|^\s*[\(\[]\s*(?:\d{1,3}|[०-९]{1,3})\s*[\)\]]\s*"
        r"|^\s*(?:q(?:uestion)?\.?)\s*(?:\d{1,3}|[०-९]{1,3})\s*[.):-]?\s*"
        r"|^\s*(?:प्रश्न|प्र\.)\s*[-–—.]?\s*(?:\d{1,3}|[०-९]{1,3})\s*[.):-]?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )


def _iter_blocks(doc: DocModel):
    for page in doc.pages:
        for block in page.blocks:
            yield block


def build_markdown(doc: DocModel) -> str:
    s = doc.settings
    out: list[str] = []
    if s.title:
        out.append(f"# {s.title}\n")
    if s.subtitle:
        out.append(f"**{s.subtitle}**\n")
    meta = [
        ("Subject", s.subject), ("Class", s.class_name), ("Session", s.session),
        ("Duration", s.duration), ("Maximum marks", s.max_marks), ("Date", s.date),
    ]
    meta = [(k, v) for k, v in meta if v]
    if meta:
        out.append("| " + " | ".join(k for k, _ in meta) + " |")
        out.append("|" + "|".join(["---"] * len(meta)) + "|")
        out.append("| " + " | ".join(v for _, v in meta) + " |\n")

    counter = 0
    for block in _iter_blocks(doc):
        text = (block.text or "").strip()
        if not text:
            continue
        t = block.type
        if t == BlockType.TITLE:
            out.append(f"## {text}\n")
        elif t == BlockType.SUBTITLE:
            out.append(f"### {text}\n")
        elif t == BlockType.SECTION:
            out.append(f"## {text}\n")
        elif t == BlockType.INSTRUCTION:
            out.append(f"> {text}\n")
        elif t == BlockType.QUESTION:
            counter += 1
            if s.renumber_questions:
                body = _clean_number_prefix(text)
                text = f"{s.question_number_format.format(n=counter)} {body}".strip()
            out.append(f"{text} " + (f"**({block.marks})**" if block.marks else "") + "\n")
        elif t == BlockType.OPTIONS:
            out.append(f"    {text}\n")
        elif t == BlockType.SUBCOMPLETION:
            out.append(f"    - {text}\n")
        elif t == BlockType.NOTE:
            out.append(f"*{text}*\n")
        elif t == BlockType.TABLE:
            out.append("```\n" + text + "\n```\n")
        else:
            out.append(f"{text}\n")
    return "\n".join(out)


def build_text(doc: DocModel) -> str:
    lines: list[str] = []
    s = doc.settings
    if s.title:
        lines.append(s.title)
    if s.subtitle:
        lines.append(s.subtitle)
    lines.append("")
    for block in _iter_blocks(doc):
        text = (block.text or "").strip()
        if not text:
            continue
        if block.type == BlockType.QUESTION and block.marks:
            lines.append(f"{text} ({block.marks})")
        elif block.type == BlockType.OPTIONS:
            lines.append(f"    {text}")
        else:
            lines.append(text)
    return "\n".join(lines)


def build_html(doc: DocModel) -> str:
    s = doc.settings
    parts: list[str] = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{html.escape(s.title or 'Exam paper')}</title>",
        "<style>",
        "body{font-family:'Nirmala UI','Times New Roman',serif;max-width:820px;"
        "margin:2rem auto;line-height:1.6;padding:0 1rem}",
        "h1{text-align:center} .subtitle{text-align:center;font-weight:600}",
        ".section{font-weight:700;border-bottom:1px solid #ccc;margin-top:1.4rem}",
        ".instruction{border:1px solid #ddd;padding:.6rem .8rem;background:#fafafa}",
        ".q{margin-top:.8rem;padding-left:2.2rem;text-indent:-2.2rem}",
        ".marks{float:right;font-weight:600}",
        ".options{margin-left:2.2rem}",
        "table{border-collapse:collapse} td,th{border:1px solid #999;padding:.3rem .5rem}",
        "</style></head><body>",
    ]
    if s.title:
        parts.append(f"<h1>{html.escape(s.title)}</h1>")
    if s.subtitle:
        parts.append(f"<p class='subtitle'>{html.escape(s.subtitle)}</p>")
    meta = [(k, v) for k, v in (
        ("Subject", s.subject), ("Class", s.class_name), ("Session", s.session),
        ("Duration", s.duration), ("Max marks", s.max_marks), ("Date", s.date),
    ) if v]
    if meta:
        parts.append("<table><tr>" + "".join(
            f"<td><b>{html.escape(k)}:</b> {html.escape(v)}</td>" for k, v in meta
        ) + "</tr></table>")

    counter = 0
    for block in _iter_blocks(doc):
        text = html.escape((block.text or "").strip())
        if not text:
            continue
        t = block.type
        marks = (f"<span class='marks'>({html.escape(block.marks)})</span>"
                 if block.marks else "")
        if t == BlockType.TITLE:
            parts.append(f"<h1>{text}</h1>")
        elif t == BlockType.SUBTITLE:
            parts.append(f"<p class='subtitle'>{text}</p>")
        elif t == BlockType.SECTION:
            parts.append(f"<div class='section'>{text}</div>")
        elif t == BlockType.INSTRUCTION:
            parts.append(f"<div class='instruction'>{text}</div>")
        elif t == BlockType.QUESTION:
            counter += 1
            if s.renumber_questions:
                body = html.escape(_clean_number_prefix(block.text or "").strip())
                text = f"{html.escape(s.question_number_format.format(n=counter))} {body}"
            parts.append(f"<p class='q'>{marks}{text}</p>")
        elif t == BlockType.OPTIONS:
            parts.append(f"<p class='options'>{text}</p>")
        elif t == BlockType.SUBCOMPLETION:
            parts.append(f"<p class='options'>&nbsp;&nbsp;- {text}</p>")
        elif t == BlockType.NOTE:
            parts.append(f"<p><em>{text}</em></p>")
        elif t == BlockType.TABLE:
            parts.append(f"<pre>{text}</pre>")
        else:
            parts.append(f"<p>{text}</p>")
    parts.append("</body></html>")
    return "\n".join(parts)
