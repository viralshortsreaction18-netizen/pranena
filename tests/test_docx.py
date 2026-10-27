"""Word (.docx) export tests - especially the Devanagari complex-script slots."""

from __future__ import annotations

import re
import zipfile

from backend.export import build_docx, build_html, build_markdown, build_text
from backend.schemas import Block, BlockType, Document, Page


def make_doc(blocks=None, **settings):
    doc = Document(name="Test")
    doc.pages.append(Page(filename="p1.png", blocks=blocks or [], status="done"))
    for k, v in settings.items():
        setattr(doc.settings, k, v)
    return doc


class TestDocx:
    def test_produces_a_valid_zip_with_the_right_parts(self):
        data = build_docx(make_doc([Block(type=BlockType.QUESTION, text="1. Hello")]))
        z = zipfile.ZipFile(__import__("io").BytesIO(data))
        names = z.namelist()
        assert "word/document.xml" in names
        assert "[Content_Types].xml" in names
        assert z.testzip() is None

    def test_every_run_sets_the_complex_script_font(self):
        blocks = [
            Block(type=BlockType.TITLE, text="वार्षिक परीक्षा"),
            Block(type=BlockType.QUESTION, text="1. 100 से छोटी संख्या?"),
        ]
        data = build_docx(make_doc(blocks))
        z = zipfile.ZipFile(__import__("io").BytesIO(data))
        xml = z.read("word/document.xml").decode("utf-8")

        runs = re.findall(r"<w:rFonts[^/]*/>", xml)
        assert runs, "no runs found"
        for r in runs:
            assert 'w:cs="Nirmala UI"' in r, r

    def test_complex_script_size_and_bold_are_set(self):
        data = build_docx(make_doc([Block(type=BlockType.TITLE, text="शीर्षक")]))
        z = zipfile.ZipFile(__import__("io").BytesIO(data))
        xml = z.read("word/document.xml").decode("utf-8")
        assert "w:szCs" in xml
        assert "w:bCs" in xml
        assert 'w:bidi="hi-IN"' in xml

    def test_document_defaults_carry_the_complex_font(self):
        data = build_docx(make_doc([Block(type=BlockType.QUESTION, text="Q")]))
        z = zipfile.ZipFile(__import__("io").BytesIO(data))
        styles = z.read("word/styles.xml").decode("utf-8")
        assert 'w:cs="Nirmala UI"' in styles

    def test_devanagari_text_is_preserved(self):
        text = "निर्देश: सभी प्रश्न अनिवार्य हैं।"
        data = build_docx(make_doc([Block(type=BlockType.INSTRUCTION, text=text)]))
        z = zipfile.ZipFile(__import__("io").BytesIO(data))
        xml = z.read("word/document.xml").decode("utf-8")
        assert "निर्देश" in xml
        assert "अनिवार्य" in xml

    def test_marks_go_to_the_right_tab(self):
        data = build_docx(make_doc(
            [Block(type=BlockType.QUESTION, text="1. Solve", marks="5")]))
        z = zipfile.ZipFile(__import__("io").BytesIO(data))
        xml = z.read("word/document.xml").decode("utf-8")
        assert 'w:val="right"' in xml
        assert "(5)" in xml

    def test_renumbering(self):
        blocks = [
            Block(type=BlockType.QUESTION, text="7. Old number"),
            Block(type=BlockType.QUESTION, text="12. Another"),
        ]
        data = build_docx(make_doc(blocks, renumber_questions=True))
        z = zipfile.ZipFile(__import__("io").BytesIO(data))
        xml = z.read("word/document.xml").decode("utf-8")
        assert "1. Old number" in xml
        assert "2. Another" in xml
        assert "12." not in xml

    def test_page_size_and_margins(self):
        data = build_docx(make_doc([Block(type=BlockType.QUESTION, text="Q")],
                                    page_size="Letter", margin_in=1.0))
        z = zipfile.ZipFile(__import__("io").BytesIO(data))
        xml = z.read("word/document.xml").decode("utf-8")
        assert 'w:w="12240"' in xml   # 8.5in in twips
        assert 'w:left="1440"' in xml  # 1in margin

    def test_table_block(self):
        block = Block(type=BlockType.TABLE, text="| A | B |\n|---|---|\n| 1 | 2 |")
        data = build_docx(make_doc([block]))
        z = zipfile.ZipFile(__import__("io").BytesIO(data))
        xml = z.read("word/document.xml").decode("utf-8")
        assert "<w:tbl>" in xml
        # The table style is referenced by id and defined in styles.xml.
        assert "TableGrid" in xml or "Table Grid" in z.read("word/styles.xml").decode("utf-8")


class TestOtherFormats:
    def test_markdown(self):
        blocks = [
            Block(type=BlockType.TITLE, text="Paper"),
            Block(type=BlockType.QUESTION, text="1. Solve", marks="5"),
        ]
        md = build_markdown(make_doc(blocks, title="Paper"))
        assert "# Paper" in md
        assert "1. Solve" in md
        assert "**(5)**" in md

    def test_html(self):
        blocks = [Block(type=BlockType.QUESTION, text="1. Solve", marks="5")]
        html = build_html(make_doc(blocks, title="Paper"))
        assert html.startswith("<!doctype html>")
        assert "Paper" in html
        assert "(5)" in html

    def test_text(self):
        blocks = [Block(type=BlockType.QUESTION, text="1. Solve", marks="5")]
        txt = build_text(make_doc(blocks))
        assert "1. Solve (5)" in txt
