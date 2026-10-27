"""Multi-engine consensus tests."""

from __future__ import annotations

from backend.ocr.base import EngineResult, RawBlock
from backend.ocr.consensus import build_consensus, page_agreement, to_blocks
from backend.schemas import Flag


def result(engine_id, blocks, confidence=0.9):
    return EngineResult(
        engine_id=engine_id,
        model="test",
        blocks=[
            RawBlock(type="question", text=t, bbox=b, confidence=confidence)
            for t, b in blocks
        ],
    )


class TestAlignment:
    def test_identical_readings_merge(self):
        a = result("a", [("Question one", [0.1, 0.1, 0.9, 0.15])])
        b = result("b", [("Question one", [0.1, 0.1, 0.9, 0.15])])
        consensus = build_consensus([a, b])
        assert len(consensus) == 1
        assert consensus[0].text == "Question one"
        assert consensus[0].agreement == 1.0
        assert consensus[0].flags == []

    def test_different_readings_are_flagged(self):
        a = result("a", [("Question one", [0.1, 0.1, 0.9, 0.15])])
        b = result("b", [("Questiom one", [0.1, 0.1, 0.9, 0.15])])
        consensus = build_consensus([a, b])
        assert len(consensus) == 1
        assert Flag.ENGINE_DISAGREEMENT in consensus[0].flags
        assert consensus[0].agreement < 1.0

    def test_consensus_picks_the_majority_reading(self):
        a = result("a", [("The value is 97", [0.1, 0.1, 0.9, 0.15])])
        b = result("b", [("The value is 97", [0.1, 0.1, 0.9, 0.15])])
        c = result("c", [("The value is 9T", [0.1, 0.1, 0.9, 0.15])])
        consensus = build_consensus([a, b, c])
        assert len(consensus) == 1
        # Two of three engines read "97" -> the consensus keeps it.
        assert "97" in consensus[0].text
        assert consensus[0].agreement > 0.9

    def test_unmatched_block_is_kept(self):
        a = result("a", [("Line one", [0.1, 0.1, 0.9, 0.15])])
        b = result("b", [("Line one", [0.1, 0.1, 0.9, 0.15]),
                          ("Line two", [0.1, 0.2, 0.9, 0.25])])
        consensus = build_consensus([a, b])
        assert len(consensus) == 2
        texts = sorted(c.text for c in consensus)
        assert texts == ["Line one", "Line two"]

    def test_single_engine_is_not_flagged(self):
        a = result("a", [("Only engine", [0.1, 0.1, 0.9, 0.15])], confidence=0.95)
        consensus = build_consensus([a])
        assert consensus[0].flags == []

    def test_single_engine_with_multiple_engines_flags_the_extra_line(self):
        a = result("a", [("Line one", [0.1, 0.1, 0.9, 0.15])])
        b = result("b", [("Line one", [0.1, 0.1, 0.9, 0.15]),
                          ("Line two", [0.1, 0.2, 0.9, 0.25])])
        consensus = build_consensus([a, b])
        extra = [c for c in consensus if c.text == "Line two"][0]
        assert Flag.ENGINE_DISAGREEMENT in extra.flags

    def test_reading_order_is_by_position(self):
        a = result("a", [
            ("Second line", [0.1, 0.5, 0.9, 0.55]),
            ("First line", [0.1, 0.1, 0.9, 0.15]),
        ])
        consensus = build_consensus([a])
        assert consensus[0].text == "First line"


class TestToBlocks:
    def test_conversion(self):
        a = result("a", [("Q text", [0.1, 0.1, 0.9, 0.15])])
        blocks = to_blocks(build_consensus([a]))
        assert len(blocks) == 1
        assert blocks[0].text == "Q text"
        assert blocks[0].engine_texts == {"a": "Q text"}
        assert blocks[0].review_status.value == "pending"

    def test_page_agreement(self):
        a = result("a", [("Same", [0.1, 0.1, 0.9, 0.15])])
        b = result("b", [("Same", [0.1, 0.1, 0.9, 0.15])])
        assert page_agreement(build_consensus([a, b]), 2) == 1.0
        assert page_agreement([], 2) == 0.0
