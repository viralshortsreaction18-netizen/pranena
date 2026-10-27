"""Build a Microsoft Word (.docx) document from a Pranena document.

Devanagari in Word is deceptively hard.  Word resolves a run's font through
four slots and a Devanagari character is a *complex script*, so it reads
``w:rFonts/@w:cs`` and ``w:szCs`` / ``w:bCs`` / ``w:iCs`` - not the ordinary
``w:ascii`` / ``w:sz`` / ``w:b`` / ``w:i`` that most libraries write.

If you only set the Latin slot, Word silently renders Hindi and Sanskrit in a
fallback face (or as boxes).  Every run this module creates therefore sets the
complex-script slot explicitly, and the document defaults do too.
"""

from __future__ import annotations

import io
import re
from typing import Any, Iterable

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from ..schemas import Block, BlockType, DocSettings, Document as DocModel

#: Fonts that are known to ship with Devanagari support on Windows and Office
#: for Mac.  Nirmala UI is the modern default; Mangal is the legacy fallback.
DEVANAGARI_FONTS = ("Nirmala UI", "Mangal", "Kokila", "Noto Sans Devanagari")


def _set_font(
    run,
    *,
    latin: str,
    devanagari: str,
    size_pt: float | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
    language: str = "en-IN",
    devanagari_language: str = "hi-IN",
) -> None:
    """Apply a font to a run, covering BOTH the Latin and complex-script slots."""
    rPr = run._element.get_or_add_rPr()

    # -- fonts ------------------------------------------------------------
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        if rFonts.get(qn(attr)) is not None:
            del rFonts.attrib[qn(attr)]
    rFonts.set(qn("w:ascii"), latin)
    rFonts.set(qn("w:hAnsi"), latin)
    rFonts.set(qn("w:eastAsia"), latin)
    rFonts.set(qn("w:cs"), devanagari)

    # -- size -------------------------------------------------------------
    if size_pt is not None:
        half = str(int(round(size_pt * 2)))
        for tag in ("w:sz", "w:szCs"):
            el = rPr.find(qn(tag))
            if el is None:
                el = OxmlElement(tag)
                rPr.append(el)
            el.set(qn("w:val"), half)

    # -- bold / italic (both slots) --------------------------------------
    if bold is not None:
        for tag in ("w:b", "w:bCs"):
            el = rPr.find(qn(tag))
            if el is None:
                el = OxmlElement(tag)
                rPr.append(el)
            el.set(qn("w:val"), "true" if bold else "false")
    if italic is not None:
        for tag in ("w:i", "w:iCs"):
            el = rPr.find(qn(tag))
            if el is None:
                el = OxmlElement(tag)
                rPr.append(el)
            el.set(qn("w:val"), "true" if italic else "false")

    # -- language ----------------------------------------------------------
    lang = rPr.find(qn("w:lang"))
    if lang is None:
        lang = OxmlElement("w:lang")
        rPr.append(lang)
    lang.set(qn("w:val"), language)
    lang.set(qn("w:bidi"), devanagari_language)


def _set_document_defaults(doc, latin: str, devanagari: str, size_pt: float) -> None:
    """Set fonts on ``styles.xml`` docDefaults so nothing falls back."""
    styles = doc.styles.element
    docDefaults = styles.find(qn("w:docDefaults"))
    if docDefaults is None:
        docDefaults = OxmlElement("w:docDefaults")
        styles.insert(0, docDefaults)

    rPrDefault = docDefaults.find(qn("w:rPrDefault"))
    if rPrDefault is None:
        rPrDefault = OxmlElement("w:rPrDefault")
        docDefaults.append(rPrDefault)
    rPr = rPrDefault.find(qn("w:rPr"))
    if rPr is None:
        rPr = OxmlElement("w:rPr")
        rPrDefault.append(rPr)

    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:ascii"), latin)
    rFonts.set(qn("w:hAnsi"), latin)
    rFonts.set(qn("w:eastAsia"), latin)
    rFonts.set(qn("w:cs"), devanagari)

    half = str(int(round(size_pt * 2)))
    for tag in ("w:sz", "w:szCs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = OxmlElement(tag)
            rPr.append(el)
        el.set(qn("w:val"), half)

    lang = rPr.find(qn("w:lang"))
    if lang is None:
        lang = OxmlElement("w:lang")
        rPr.append(lang)
    lang.set(qn("w:val"), "en-IN")
    lang.set(qn("w:bidi"), "hi-IN")


def _set_style_font(style, latin: str, devanagari: str, size_pt: float | None = None,
                     bold: bool | None = None, color: str | None = None) -> None:
    rPr = style.element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:ascii"), latin)
    rFonts.set(qn("w:hAnsi"), latin)
    rFonts.set(qn("w:eastAsia"), latin)
    rFonts.set(qn("w:cs"), devanagari)
    if size_pt is not None:
        half = str(int(round(size_pt * 2)))
        for tag in ("w:sz", "w:szCs"):
            el = rPr.find(qn(tag))
            if el is None:
                el = OxmlElement(tag)
                rPr.append(el)
            el.set(qn("w:val"), half)
    if bold is not None:
        for tag in ("w:b", "w:bCs"):
            el = rPr.find(qn(tag))
            if el is None:
                el = OxmlElement(tag)
                rPr.append(el)
            el.set(qn("w:val"), "true" if bold else "false")
    if color:
        el = rPr.find(qn("w:color"))
        if el is None:
            el = OxmlElement("w:color")
            rPr.append(el)
        el.set(qn("w:val"), color)


def _add_page_field(paragraph, instr: str = "PAGE") -> None:
    """Insert a Word field such as PAGE or NUMPAGES."""
    run = paragraph.add_run()
    fld = OxmlElement("w:fldChar")
    fld.set(qn("w:fldCharType"), "begin")
    instr_el = OxmlElement("w:instrText")
    instr_el.set(qn("xml:space"), "preserve")
    instr_el.text = f" {instr} "
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._element.append(fld)
    run._element.append(instr_el)
    run._element.append(end)


def _add_bottom_border(paragraph, size: int = 6, color: str = "9AA0A6") -> None:
    pPr = paragraph._p.get_or_add_pPr()
    borders = pPr.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        pPr.append(borders)
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "2")
    bottom.set(qn("w:color"), color)
    borders.append(bottom)


def _add_top_border(paragraph, size: int = 6, color: str = "9AA0A6") -> None:
    pPr = paragraph._p.get_or_add_pPr()
    borders = pPr.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        pPr.append(borders)
    top = OxmlElement("w:top")
    top.set(qn("w:val"), "single")
    top.set(qn("w:sz"), str(size))
    top.set(qn("w:space"), "2")
    top.set(qn("w:color"), color)
    borders.append(top)


def _set_right_tab(paragraph, position_twips: int) -> None:
    pPr = paragraph._p.get_or_add_pPr()
    tabs = pPr.find(qn("w:tabs"))
    if tabs is None:
        tabs = OxmlElement("w:tabs")
        pPr.append(tabs)
    tab = OxmlElement("w:tab")
    tab.set(qn("w:val"), "right")
    tab.set(qn("w:pos"), str(int(position_twips)))
    tabs.append(tab)


def _set_hanging_indent(paragraph, left_in: float, hanging_in: float) -> None:
    pf = paragraph.paragraph_format
    pf.left_indent = Inches(left_in)
    pf.first_line_indent = Inches(-hanging_in)


def _parse_table(text: str) -> list[list[str]]:
    """Parse a Markdown-ish pipe table into rows of cells."""
    rows: list[list[str]] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r"[\s:|-]+", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append(cells)
    return rows


def _clean_number_prefix(text: str) -> str:
    """Remove a leading question number so we can renumber cleanly."""
    return re.sub(
        r"^\s*(?:\d{1,3}|[०-९]{1,3})\s*[.)]\s*"
        r"|^\s*[\(\[]\s*(?:\d{1,3}|[०-९]{1,3})\s*[\)\]]\s*"
        r"|^\s*(?:q(?:uestion)?\.?)\s*(?:\d{1,3}|[०-९]{1,3})\s*[.):-]?\s*"
        r"|^\s*(?:प्रश्न|प्र\.)\s*[-–—.]?\s*(?:\d{1,3}|[०-९]{1,3})\s*[.):-]?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )


class DocxBuilder:
    """Renders a :class:`Document` model into a .docx byte string."""

    def __init__(self, doc: DocModel):
        self.model = doc
        self.settings = doc.settings
        self.latin = self.settings.font_latin or "Times New Roman"
        self.deva = self.settings.font_devanagari or "Nirmala UI"
        if self.deva not in DEVANAGARI_FONTS:
            # Keep the user's font but make sure the complex-script slot is set.
            pass
        self.size = self.settings.font_size_pt or 12.0

    # -- public -------------------------------------------------------------

    def build(self) -> bytes:
        doc = Document()
        _set_document_defaults(doc, self.latin, self.deva, self.size)

        section = doc.sections[0]
        if self.settings.page_size == "Letter":
            section.page_width = Inches(8.5)
            section.page_height = Inches(11)
        else:
            section.page_width = Inches(8.27)
            section.page_height = Inches(11.69)
        m = self.settings.margin_in
        section.top_margin = Inches(m)
        section.bottom_margin = Inches(m)
        section.left_margin = Inches(m)
        section.right_margin = Inches(m)

        # Width available for the right-aligned marks tab, in twips.
        content_width_in = (
            section.page_width.inches - section.left_margin.inches - section.right_margin.inches
        )
        self.right_tab = int(content_width_in * 1440)

        normal = doc.styles["Normal"]
        _set_style_font(normal, self.latin, self.deva, self.size)
        normal.paragraph_format.space_after = Pt(4)
        normal.paragraph_format.line_spacing = self.settings.line_spacing

        self._build_header_footer(doc, section)
        self._render_body(doc)

        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    # -- header / footer ----------------------------------------------------

    def _build_header_footer(self, doc, section) -> None:
        s = self.settings
        header_text = s.institution or s.title or ""
        if header_text:
            p = section.header.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = p.add_run(header_text)
            _set_font(run, latin=self.latin, devanagari=self.deva, size_pt=9.5,
                      language="en-IN")
            _add_bottom_border(p)

        footer = section.footer.paragraphs[0]
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        left = s.footer_text or ""
        if left:
            r = footer.add_run(left + "     ")
            _set_font(r, latin=self.latin, devanagari=self.deva, size_pt=9)
        r = footer.add_run("Page ")
        _set_font(r, latin=self.latin, devanagari=self.deva, size_pt=9)
        _add_page_field(footer, "PAGE")
        r = footer.add_run(" of ")
        _set_font(r, latin=self.latin, devanagari=self.deva, size_pt=9)
        _add_page_field(footer, "NUMPAGES")

    # -- body ---------------------------------------------------------------

    def _add_run(self, paragraph, text: str, **kwargs):
        run = paragraph.add_run(text)
        _set_font(run, latin=self.latin, devanagari=self.deva, **kwargs)
        return run

    def _render_body(self, doc) -> None:
        s = self.settings

        if s.title:
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            self._add_run(p, s.title, size_pt=s.font_size_pt + 4, bold=True)
            p.paragraph_format.space_after = Pt(2)

        if s.subtitle:
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            self._add_run(p, s.subtitle, size_pt=s.font_size_pt + 1, bold=True)
            p.paragraph_format.space_after = Pt(8)

        info = self._info_pairs()
        if info:
            self._render_info_table(doc, info)

        if s.institution:
            pass  # already in the header

        counter = 0
        for page in self.model.pages:
            for block in page.blocks:
                counter = self._render_block(doc, block, counter)

    def _info_pairs(self) -> list[tuple[str, str]]:
        s = self.settings
        pairs: list[tuple[str, str]] = []
        if s.subject:
            pairs.append(("Subject / विषय", s.subject))
        if s.class_name:
            pairs.append(("Class / कक्षा", s.class_name))
        if s.session:
            pairs.append(("Session / सत्र", s.session))
        if s.duration:
            pairs.append(("Duration / समय", s.duration))
        if s.max_marks:
            pairs.append(("Maximum Marks / अधिकतम अंक", s.max_marks))
        if s.date:
            pairs.append(("Date / दिनांक", s.date))
        return pairs

    def _render_info_table(self, doc, pairs: list[tuple[str, str]]) -> None:
        cols = 3 if len(pairs) >= 3 else len(pairs)
        rows = (len(pairs) + cols - 1) // cols
        table = doc.add_table(rows=rows, cols=cols)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = True
        idx = 0
        for r in range(rows):
            for c in range(cols):
                cell = table.cell(r, c)
                cell.text = ""
                p = cell.paragraphs[0]
                p.paragraph_format.space_after = Pt(1)
                p.paragraph_format.line_spacing = 1.0
                if idx < len(pairs):
                    label, value = pairs[idx]
                    self._add_run(p, f"{label}: ", size_pt=self.size - 1.5, bold=True)
                    self._add_run(p, value, size_pt=self.size - 1.5)
                    idx += 1
        # A little breathing room after the table.
        spacer = doc.add_paragraph()
        spacer.paragraph_format.space_after = Pt(2)
        spacer.paragraph_format.line_spacing = 0.4

    # -- block rendering ----------------------------------------------------

    def _render_block(self, doc, block: Block, counter: int) -> int:
        s = self.settings
        btype = block.type
        text = (block.text or "").strip()
        if not text:
            return counter

        if btype == BlockType.TITLE:
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            self._add_run(p, text, size_pt=s.font_size_pt + 3, bold=True)
            p.paragraph_format.space_before = Pt(6)
            p.paragraph_format.space_after = Pt(4)

        elif btype == BlockType.SUBTITLE:
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            self._add_run(p, text, size_pt=s.font_size_pt, bold=True)
            p.paragraph_format.space_after = Pt(3)

        elif btype == BlockType.SECTION:
            p = doc.add_paragraph()
            self._add_run(p, text, size_pt=s.font_size_pt, bold=True)
            p.paragraph_format.space_before = Pt(10)
            p.paragraph_format.space_after = Pt(4)
            _add_bottom_border(p)

        elif btype == BlockType.INSTRUCTION:
            p = doc.add_paragraph()
            self._add_run(p, text, size_pt=s.font_size_pt - 1)
            p.paragraph_format.left_indent = Inches(0.15)
            p.paragraph_format.right_indent = Inches(0.15)
            p.paragraph_format.space_before = Pt(4)
            p.paragraph_format.space_after = Pt(6)

        elif btype == BlockType.QUESTION:
            counter += 1
            body = text
            if s.renumber_questions:
                body = _clean_number_prefix(body)
                prefix = s.question_number_format.format(n=counter)
                body = f"{prefix} {body}".strip()
            p = doc.add_paragraph()
            self._add_run(p, body, size_pt=s.font_size_pt)
            p.paragraph_format.space_before = Pt(6)
            p.paragraph_format.space_after = Pt(2)
            _set_hanging_indent(p, 0.35, 0.35)
            self._append_marks(p, block)

        elif btype == BlockType.OPTIONS:
            if not s.include_options:
                return counter
            p = doc.add_paragraph()
            self._add_run(p, text, size_pt=s.font_size_pt)
            p.paragraph_format.left_indent = Inches(0.55)
            p.paragraph_format.space_after = Pt(2)

        elif btype == BlockType.SUBCOMPLETION:
            p = doc.add_paragraph()
            self._add_run(p, text, size_pt=s.font_size_pt)
            p.paragraph_format.left_indent = Inches(0.55)
            p.paragraph_format.space_after = Pt(2)

        elif btype == BlockType.NOTE:
            p = doc.add_paragraph()
            self._add_run(p, text, size_pt=s.font_size_pt - 1, italic=True)
            p.paragraph_format.space_after = Pt(3)

        elif btype == BlockType.DIAGRAM_CAPTION:
            p = doc.add_paragraph()
            self._add_run(p, text, size_pt=s.font_size_pt - 1, italic=True)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

        elif btype == BlockType.MARKS:
            p = doc.add_paragraph()
            self._add_run(p, text, size_pt=s.font_size_pt - 1)
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

        elif btype == BlockType.TABLE:
            self._render_table(doc, text)

        elif btype in (BlockType.HEADER, BlockType.FOOTER):
            p = doc.add_paragraph()
            self._add_run(p, text, size_pt=s.font_size_pt - 2, italic=True)
            if btype == BlockType.HEADER:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            else:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER

        else:  # UNKNOWN
            p = doc.add_paragraph()
            self._add_run(p, text, size_pt=s.font_size_pt)
            p.paragraph_format.space_after = Pt(3)

        return counter

    def _append_marks(self, paragraph, block: Block) -> None:
        s = self.settings
        if not s.show_marks_column or not block.marks:
            return
        marks = block.marks.strip()
        if s.marks_position == "bracket":
            self._add_run(paragraph, f"  ({marks})", size_pt=s.font_size_pt)
            return
        if s.marks_position == "inline":
            self._add_run(paragraph, f"  [{marks}]", size_pt=s.font_size_pt)
            return
        self._add_run(paragraph, "\t", size_pt=s.font_size_pt)
        _set_right_tab(paragraph, self.right_tab)
        self._add_run(paragraph, f"({marks})", size_pt=s.font_size_pt)

    def _render_table(self, doc, text: str) -> None:
        rows = _parse_table(text)
        if not rows:
            return
        cols = max(len(r) for r in rows)
        table = doc.add_table(rows=len(rows), cols=cols)
        table.style = "Table Grid"
        for ri, row in enumerate(rows):
            for ci in range(cols):
                cell = table.cell(ri, ci)
                cell.text = ""
                p = cell.paragraphs[0]
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.line_spacing = 1.0
                value = row[ci] if ci < len(row) else ""
                self._add_run(p, value, size_pt=self.size - 1,
                              bold=(ri == 0 and len(rows) > 1))
        doc.add_paragraph().paragraph_format.space_after = Pt(2)


def build_docx(doc: DocModel) -> bytes:
    return DocxBuilder(doc).build()
