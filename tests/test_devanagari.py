"""Devanagari post-processing tests."""

from __future__ import annotations

import pytest

from backend.postprocess import devanagari as dev


class TestNormalization:
    def test_nfc_composes_marks(self):
        # Decomposed ka+nukta stays decomposed (nukta letters are composition
        # exclusions in Unicode) and the mark keeps canonical order.
        assert dev.normalize_devanagari("\u0915\u093c") == "\u0915\u093c"

    def test_nukta_is_unified_to_decomposed(self):
        # U+0958..U+095F are the precomposed nukta letters.
        precomposed = "".join(chr(c) for c in range(0x0958, 0x0960))
        out = dev.normalize_devanagari(precomposed)
        assert all(chr(c) not in out for c in range(0x0958, 0x0960))
        assert out.count("\u093c") == 8  # eight nukta signs

    def test_precomposed_can_be_requested(self):
        decomposed = "\u0915\u093c"  # ka + nukta sign
        out = dev.normalize_devanagari(decomposed, prefer_precomposed_nukta=True)
        assert out == "\u0958"

    def test_duplicate_nukta_is_collapsed(self):
        assert dev.normalize_devanagari("\u0915\u093c\u093c") == "\u0915\u093c"

    def test_whitespace_is_tidied(self):
        assert dev.normalize_devanagari("  यह   एक   प्रश्न  ") == "यह एक प्रश्न"

    def test_space_before_danda_removed(self):
        assert dev.normalize_devanagari("समाप्त ।") == "समाप्त।"

    def test_danda_to_period(self):
        assert dev.normalize_devanagari("क। ख।", danda_to_period=True) == "क. ख."

    def test_devanagari_digits_convert(self):
        assert dev.normalize_devanagari("१२३", convert_digits=True) == "123"
        assert dev.normalize_devanagari("१२३") == "१२३"

    def test_empty_input(self):
        assert dev.normalize_devanagari("") == ""

    def test_english_text_is_untouched(self):
        assert dev.normalize_devanagari("Question 1: what is 2+2?") == "Question 1: what is 2+2?"


class TestDetection:
    def test_contains_devanagari(self):
        assert dev.contains_devanagari("नमस्ते")
        assert not dev.contains_devanagari("hello")

    def test_script_detection(self):
        assert dev.script_of("नमस्ते") == "devanagari"
        assert dev.script_of("hello") == "latin"
        assert dev.script_of("123 !@#") == "unknown"

    def test_lookalikes_detected(self):
        hits = dev.find_lookalikes("शब्द")
        assert hits and hits[0]["char"] == "श"
        assert "ष" in hits[0]["alternatives"]

    def test_lookalikes_context(self):
        hits = dev.find_lookalikes("कि क्ष क्ष")
        assert len(hits) >= 1

    def test_rare_characters(self):
        hits = dev.find_rare_characters("\u0915\u0904")  # ka + rare vowel sign
        assert hits and hits[0]["char"] == "\u0904"

    def test_suspicious_virama(self):
        # A virama after a full vowel is invalid Devanagari.
        hits = dev.find_suspicious_virama("अ्")
        assert len(hits) == 1
        assert dev.find_suspicious_virama("क्") == []


class TestLexicon:
    def test_known_words_pass(self):
        assert dev.unknown_words("गणित विज्ञान परीक्षा प्रश्न") == []

    def test_inflected_forms_pass(self):
        # "प्रश्नों" is in the lexicon, "प्रश्न" stem + "ों" suffix.
        assert dev.unknown_words("प्रश्नों") == []

    def test_gibberish_is_flagged(self):
        assert dev.unknown_words("क्वज़्यप्लम्ब") != []

    def test_english_words_pass(self):
        assert dev.unknown_words("calculate the value") == []

    def test_english_typo_flagged(self):
        assert dev.unknown_words("calculatd") != []

    def test_numbers_are_ignored(self):
        assert dev.unknown_words("१२३४५") == []


class TestLexiconFiles:
    """The lexicon ships as plain text so it can be extended without code."""

    def test_both_lexicons_load(self):
        assert len(dev.HINDI_LEXICON) > 500
        assert len(dev.SANSKRIT_LEXICON) > 500

    def test_lexicon_entries_are_clean_devanagari(self):
        # No latin letters, no stray control characters, no whitespace.
        for lexicon in (dev.HINDI_LEXICON, dev.SANSKRIT_LEXICON):
            for word in lexicon:
                assert word, "empty entry"
                assert " " not in word and "\t" not in word
                assert all(dev.is_devanagari_char(c) for c in word), word

    def test_common_school_words_are_present(self):
        for word in ("प्रश्न", "अंक", "समय", "विषय", "निर्देश", "विद्यार्थी",
                     "कक्षा", "गणित", "विज्ञान", "उत्तर", "ज्ञात"):
            assert word in dev.HINDI_LEXICON or word in dev.SANSKRIT_LEXICON, word

    def test_sanskrit_vocabulary_is_present(self):
        for word in ("धर्म", "कर्म", "ज्ञान", "योग", "सत्यम्", "विद्या"):
            assert word in dev.SANSKRIT_LEXICON, word


class TestDandaWordBoundary:
    def test_danda_does_not_attach_to_word(self):
        # "है।" must tokenise as "है" so it matches the lexicon.
        assert dev.words_of("है। यह") == ["है", "यह"]

    def test_sentence_ending_in_danda_is_known(self):
        assert dev.unknown_words("यह सही है।") == []


class TestConfusableDetection:
    """The look-alike detector must not fire on ordinary correct text."""

    def test_common_words_raise_no_confusion(self):
        text = "कक्षा 10 अंक 5 समय 45 मिनट"
        assert dev.find_confusable_words(text) == []

    def test_realistic_hindi_paper_raises_no_confusion(self):
        text = (
            "निर्देश: सभी प्रश्नों के उत्तर दीजिए। "
            "प्रश्न 1. 25 और 36 का योगफल ज्ञात कीजिए। "
            "प्रश्न 2. एक वृत्त की त्रिज्या 7 सेमी है। उसका क्षेत्रफल ज्ञात कीजिए। "
            "प्रश्न 3. दो संख्याओं का योग 30 तथा अंतर 4 है। संख्याएँ ज्ञात कीजिए।"
        )
        assert dev.find_confusable_words(text) == []
        assert dev.unknown_words(text) == []

    def test_known_word_with_lookalike_char_is_not_flagged(self):
        # "समय" contains स, which is confusable with श/ष - but it is correct.
        assert dev.find_confusable_words("समय") == []
        assert dev.find_confusable_words("सर्व") == []
        assert dev.find_confusable_words("शब्द") == []

    def test_genuine_misread_is_flagged_with_suggestion(self):
        hits = dev.find_confusable_words("शमय")
        assert len(hits) == 1
        assert hits[0]["char"] == "श"
        assert hits[0]["suggestion"] == "समय"
        assert hits[0]["word"] == "शमय"

    def test_confusable_word_is_also_reported_as_unknown(self):
        assert "शमय" in dev.unknown_words("शमय")

    def test_multiple_misreads_are_all_found(self):
        hits = dev.find_confusable_words("शमय और शरकार")
        suggestions = {h["suggestion"] for h in hits}
        assert suggestions == {"समय", "सरकार"}

    def test_raw_lookalike_scan_still_available(self):
        # The unfiltered scan is kept for tools that want every ambiguity.
        hits = dev.find_lookalikes("शब्द")
        assert hits and hits[0]["char"] == "श"


class TestNuktaPositioning:
    """Handwriting puts the nukta dot in the wrong place; fix it."""

    def test_nukta_after_vowel_sign_moves_onto_the_letter(self):
        # "बड़ी" written with the dot under the vowel sign.
        assert dev.normalize_nukta("बडी़") == "बड़ी"

    def test_nukta_already_on_the_letter_is_untouched(self):
        assert dev.normalize_nukta("क़ी") == "क़ी"
        assert dev.normalize_nukta("क़न") == "क़न"
        assert dev.normalize_nukta("क़ा") == "क़ा"

    def test_leading_nukta_moves_onto_the_letter(self):
        assert dev.normalize_nukta("़क") == "क़"

    def test_position_fix_also_applies_to_the_precomposed_style(self):
        # Same fix, but the letter ends up in its precomposed form (U+095C).
        assert dev.normalize_nukta("\u092c\u0921\u0940\u093c", prefer_precomposed=True) == "\u092c\u095c\u0940"


class TestSuggestionQuality:
    """A suggested correction must be a word we can actually write down."""

    def test_short_garbage_is_not_offered_as_a_suggestion(self):
        # "हें" is two characters long but it is not a word.
        assert not dev._is_concrete_word("हें")
        assert dev._is_concrete_word("समय")

    def test_no_bogus_suggestion_for_hindi_particles(self):
        # "हैं" must not be "corrected" to the non-word "हें".
        assert dev.find_confusable_words("हैं") == []

    def test_real_misread_still_suggests_a_real_word(self):
        hits = dev.find_confusable_words("शमय")
        assert [h["suggestion"] for h in hits] == ["समय"]
