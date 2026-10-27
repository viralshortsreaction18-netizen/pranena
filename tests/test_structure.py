"""Exam-paper structure detection tests."""

from __future__ import annotations

from backend.postprocess.structure import (
    detect_instruction,
    detect_marks,
    detect_question_number,
    detect_section,
    infer_structure,
    looks_like_options,
    split_options,
    strip_marks,
)
from backend.schemas import Block, BlockType


class TestQuestionNumbers:
    def test_dot_and_bracket_styles(self):
        assert detect_question_number("1. What is 2+2?") == "1"
        assert detect_question_number("2) What is 2+2?") == "2"
        assert detect_question_number("(3) What is 2+2?") == "3"
        assert detect_question_number("Q4 What is 2+2?") == "4"
        assert detect_question_number("Q. 5 Find x") == "5"

    def test_devanagari_numbers(self):
        assert detect_question_number("१. प्रश्न") == "१"

    def test_hindi_question_prefix(self):
        assert detect_question_number("प्रश्न 7 क्या है?") == "7"

    def test_no_number(self):
        assert detect_question_number("This is just text") is None


class TestMarks:
    def test_bracket_marks(self):
        assert detect_marks("Find the value. (5)") == "5"
        assert detect_marks("Find the value. [3]") == "3"

    def test_words_marks(self):
        assert detect_marks("Answer this 5 marks please") == "5"

    def test_hindi_marks(self):
        assert detect_marks("इसका उत्तर दो 5 अंक") == "5"

    def test_strip_marks(self):
        text, marks = strip_marks("Define the term photosynthesis. (4)")
        assert marks == "4"
        assert text == "Define the term photosynthesis."


class TestSections:
    def test_english_sections(self):
        assert detect_section("Section A") == "A"
        assert detect_section("SECTION - B") == "B"
        assert detect_section("Part II") == "II"

    def test_hindi_sections(self):
        assert detect_section("खण्ड अ") == "अ"
        assert detect_section("भाग - ब") == "ब"

    def test_letter_prefix(self):
        assert detect_section("A. Objective Questions") == "A"

    def test_not_a_section(self):
        assert detect_section("This question is hard") is None


class TestInstructions:
    def test_english(self):
        assert detect_instruction("Instructions: answer all questions")
        assert detect_instruction("Note: use a calculator")

    def test_hindi(self):
        assert detect_instruction("निर्देश: सभी प्रश्न अनिवार्य हैं")
        assert detect_instruction("सूचना - ध्यान दें")

    def test_not_an_instruction(self):
        assert not detect_instruction("What is the capital of France?")


class TestOptions:
    def test_detects_lettered_options(self):
        assert looks_like_options("(a) one (b) two (c) three")

    def test_detects_roman_options(self):
        assert looks_like_options("(i) one (ii) two (iii) three")

    def test_ignores_prose(self):
        assert not looks_like_options("This is a normal sentence about a and b")

    def test_split(self):
        parts = split_options("(a) 96 (b) 97 (c) 98")
        assert [p[0] for p in parts] == ["a", "b", "c"]
        assert parts[0][1] == "96"


class TestInferStructure:
    def test_question_gets_number_and_marks(self):
        blocks = [
            Block(type=BlockType.QUESTION, text="1. Define photosynthesis. (4)"),
        ]
        infer_structure(blocks)
        b = blocks[0]
        assert b.question_number == "1"
        assert b.marks == "4"
        assert b.text == "1. Define photosynthesis."

    def test_unknown_block_becomes_question(self):
        blocks = [Block(type=BlockType.UNKNOWN, text="7. What is 5 + 5?")]
        infer_structure(blocks)
        assert blocks[0].type == BlockType.QUESTION
        assert blocks[0].question_number == "7"

    def test_options_are_typed(self):
        blocks = [Block(type=BlockType.UNKNOWN, text="(a) 1 (b) 2 (c) 3 (d) 4")]
        infer_structure(blocks)
        assert blocks[0].type == BlockType.OPTIONS

    def test_first_block_becomes_title(self):
        blocks = [Block(type=BlockType.UNKNOWN, text="Annual Examination 2026")]
        infer_structure(blocks)
        assert blocks[0].type == BlockType.TITLE

    def test_instruction_is_detected(self):
        blocks = [Block(type=BlockType.UNKNOWN, text="निर्देश: सभी प्रश्न अनिवार्य हैं।")]
        infer_structure(blocks)
        assert blocks[0].type == BlockType.INSTRUCTION

    def test_subcompletion_after_question(self):
        blocks = [
            Block(type=BlockType.QUESTION, text="1. Explain."),
            Block(type=BlockType.UNKNOWN, text="(a) first part"),
        ]
        infer_structure(blocks)
        assert blocks[1].type == BlockType.SUBCOMPLETION
