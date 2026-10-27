"""Image preparation tests."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from backend.ocr.imageprep import detect_orientation, estimate_skew, prepare


def make_image(size=(800, 1100), text_rows=20, rotate=0):
    img = Image.new("RGB", size, "white")
    from PIL import ImageDraw

    d = ImageDraw.Draw(img)
    for i in range(text_rows):
        d.text((60, 50 + i * 50), "1. The quick brown fox jumps over 13 lazy dogs.", fill="black")
    if rotate:
        img = img.rotate(rotate, expand=True, fillcolor="white")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class TestPrepare:
    def test_returns_jpeg_with_dimensions(self):
        out = prepare(make_image())
        assert out.mime == "image/jpeg"
        assert out.width > 0 and out.height > 0
        assert len(out.data) > 1000

    def test_small_images_are_upscaled(self):
        out = prepare(make_image(size=(200, 300)))
        assert max(out.width, out.height) >= 1200

    def test_large_images_are_downscaled(self):
        out = prepare(make_image(size=(5000, 6000)))
        assert max(out.width, out.height) <= 2008

    def test_garbage_input_does_not_raise(self):
        out = prepare(b"not an image at all")
        assert out.data == b"not an image at all"
        assert out.notes

    def test_output_dimensions_are_multiples_of_eight(self):
        out = prepare(make_image(size=(997, 1231)))
        assert out.width % 8 == 0
        assert out.height % 8 == 0


class TestOrientation:
    def test_upright_page_is_not_rotated(self):
        img = np.array(Image.open(io.BytesIO(make_image())).convert("RGB"))
        angle = detect_orientation(img)
        assert angle in (0, 180)

    def test_sideways_page_is_detected(self):
        img = np.array(Image.open(io.BytesIO(make_image(rotate=90))).convert("RGB"))
        angle = detect_orientation(img)
        assert angle in (90, 270)

    def test_prepare_rotates_a_sideways_page(self):
        out = prepare(make_image(rotate=90))
        # After rotation the page should be portrait again.
        assert out.height >= out.width


class TestSkew:
    def test_straight_page_has_small_skew(self):
        img = np.array(Image.open(io.BytesIO(make_image())).convert("L"))
        assert abs(estimate_skew(img)) < 2.0

    def test_tilted_page_is_detected(self):
        img = np.array(Image.open(io.BytesIO(make_image())).convert("L"))
        tilted = Image.fromarray(img).rotate(6, expand=True, fillcolor=255)
        assert abs(estimate_skew(np.array(tilted))) > 1.0
