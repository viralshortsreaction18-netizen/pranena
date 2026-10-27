"""Quality-flag regression tests.

These cover the end-to-end behaviour of the review flags that the editor
relies on.  The important property is *precision*: a correctly transcribed
Hindi/Sanskrit exam paper must not light up the review queue, otherwise the
human-verification step that guarantees accuracy becomes useless.
"""

from __future__ import annotations

from backend.ocr.pipeline import _apply_quality_flags
from backend.schemas import Block, Flag


def _flags(text: str) -> list[Flag]:
    block = Block(text=text)
    _apply_quality_flags([block])
    return block.flags


class TestHindiFalsePositives:
    def test_clean_hindi_block_has_no_flags(self):
        text = (
            "निर्देश: सभी प्रश्नों के उत्तर दीजिए। "
            "प्रश्न 1. 25 और 36 का योगफल ज्ञात कीजिए।"
        )
        assert _flags(text) == []

    def test_clean_hindi_exam_block_has_no_flags(self):
        text = (
            "कक्षा - 10        अंक - 50        समय - 3 घंटे\n"
            "विषय: गणित\n"
            "प्रश्न 1. एक वृत्त की त्रिज्या 7 सेमी है। उसका क्षेत्रफल ज्ञात कीजिए। "
            "प्रश्न 2. दो संख्याओं का योग 30 तथा अंतर 4 है। संख्याएँ ज्ञात कीजिए। "
            "प्रश्न 3. सरल रेखा का समीकरण 3x + 2y = 12 है।"
        )
        assert _flags(text) == []

    def test_clean_sanskrit_block_has_no_flags(self):
        text = (
            "सर्वे भवन्तु सुखिनः सर्वे सन्तु निरामयाः। "
            "धर्मोऽर्थः कर्महेतुः सम्यक् समाधि।"
        )
        assert _flags(text) == []

    def test_english_block_has_no_flags(self):
        text = "Question 1: Calculate the value of x when y = 4."
        assert _flags(text) == []


class TestTruePositives:
    def test_real_misread_is_flagged(self):
        flags = _flags("प्रश्न 1. शमय की गणना कीजिए।")
        assert Flag.DEVANAGARI_CONFUSION in flags

    def test_unknown_word_is_flagged(self):
        flags = _flags("प्रश्न 1. क्वज़्यप्लम्ब कीजिए।")
        assert Flag.UNKNOWN_WORD in flags

    def test_rare_codepoint_is_flagged(self):
        # U+0951 is a Vedic accent sign that never appears in school text.
        flags = _flags("प्रश्न 1. क\u0951श कीजिए।")
        assert Flag.DEVANAGARI_CONFUSION in flags

    def test_invalid_virama_is_flagged(self):
        flags = _flags("प्रश्न 1. अ् कीजिए।")
        assert Flag.DEVANAGARI_CONFUSION in flags

    def test_truncation_is_flagged(self):
        flags = _flags("प्रश्न 1. ज्ञात कीजिए...")
        assert Flag.SUSPECT_TRUNCATION in flags
