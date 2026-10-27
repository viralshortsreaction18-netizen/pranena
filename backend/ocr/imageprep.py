"""Image preparation for handwriting OCR.

Handwritten exam papers are usually phone photos: wrong orientation, uneven
lighting, a tilted page and faint pencil strokes.  Vision models are sensitive
to all of these, so we fix the cheap problems *before* paying for inference.

The pipeline is deliberately conservative.  A vision model reads a tilted,
low-contrast page far better than it reads a heavily thresholded, jagged
binary image, so the default never destroys ink pixels.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps

#: Long-edge target.  Vision models get ~ (w*h)/750 tokens of image input and
#: the API silently downscales anything bigger, so we normalise ourselves and
#: get a predictable cost and quality.
TARGET_LONG_EDGE = 2000
MIN_LONG_EDGE = 1200


@dataclass
class PrepResult:
    data: bytes
    mime: str
    width: int
    height: int
    rotations_applied: int = 0
    deskew_angle: float = 0.0
    contrast_enhanced: bool = False
    notes: list[str] | None = None


def _to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _profile_score(binary: np.ndarray, angle: float) -> float:
    """Sharpness of the horizontal projection profile at a given rotation.

    When the text lines are horizontal, the row-ink profile alternates sharply
    between "ink rows" and "blank rows", which maximises its variance.
    """
    h, w = binary.shape
    center = (w / 2.0, h / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        binary, matrix, (w, h), flags=cv2.INTER_NEAREST, borderValue=0
    )
    profile = (rotated > 0).sum(axis=1).astype(np.float64)
    if profile.size < 3 or profile.sum() == 0:
        return 0.0
    return float(profile.var())


def estimate_skew(gray: np.ndarray) -> float:
    """Estimate the small-angle tilt of the page in degrees.

    The returned value is the angle that must be *added* to the image to level
    it.  We search for the rotation that maximises the variance of the
    horizontal ink profile, which is the classic and most reliable deskew
    method for text pages - far more robust than looking for the longest
    straight line (which happily locks onto a margin or a table rule).
    """
    h, w = gray.shape
    if h < 60 or w < 60:
        return 0.0

    scale = min(1.0, 700.0 / max(h, w))
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    _, binary = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if binary.sum() == 0:
        return 0.0

    # Coarse pass, then refine around the best coarse angle.
    coarse = np.arange(-12.0, 12.01, 1.0)
    scores = [_profile_score(binary, float(a)) for a in coarse]
    best = float(coarse[int(np.argmax(scores))])
    best_score = max(scores)

    fine = np.arange(best - 1.0, best + 1.01, 0.2)
    fine_scores = [_profile_score(binary, float(a)) for a in fine]
    best = float(fine[int(np.argmax(fine_scores))])

    # A flat profile means there is no reliable signal (blank page).
    if best_score <= 0 or max(fine_scores) <= 0:
        return 0.0
    if abs(best) < 0.2 or abs(best) > 12.0:
        return 0.0
    return best


def rotate_image(img: np.ndarray, angle: float) -> np.ndarray:
    if abs(angle) < 0.05:
        return img
    h, w = img.shape[:2]
    center = (w / 2, h / 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_w = int(h * sin + w * cos)
    new_h = int(h * cos + w * sin)
    matrix[0, 2] += new_w / 2 - center[0]
    matrix[1, 2] += new_h / 2 - center[1]
    border = (255, 255, 255) if img.ndim == 3 else 255
    return cv2.warpAffine(
        img, matrix, (new_w, new_h), flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT, borderValue=border,
    )


def rotate_exact(img: np.ndarray, degrees_cw: int) -> np.ndarray:
    """Rotate by an exact multiple of 90 degrees, clockwise."""
    degrees_cw %= 360
    if degrees_cw == 0:
        return img
    if degrees_cw == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if degrees_cw == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)


def _component_count(binary: np.ndarray) -> int:
    """Number of connected components of ink."""
    n, _ = cv2.connectedComponents(binary, connectivity=8)
    return max(0, n - 1)


def _horizontal_text_score(binary: np.ndarray) -> float:
    """How horizontal is the text in this image?  1.0 = perfectly horizontal.

    Dilating with a long horizontal kernel merges the words of every line
    into one blob, so the number of connected components collapses towards
    the number of *lines*.  Dilating vertically does the opposite and keeps
    roughly one component per character.  The ratio of the two counts is a
    direct, scale-free measure of the text direction.
    """
    h, w = binary.shape
    span = int(np.clip(w / 30.0, 9, 45))
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (span, 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, span))

    h_count = _component_count(cv2.dilate(binary, h_kernel))
    v_count = _component_count(cv2.dilate(binary, v_kernel))
    if h_count == 0 or v_count == 0:
        return 0.0
    return v_count / float(h_count)


def detect_orientation(img: np.ndarray) -> int:
    """Return the clockwise rotation in degrees needed to make the page upright.

    Text lines are horizontal, so the orientation whose ink merges into
    horizontal lines is the upright one.  The remaining 180-degree ambiguity
    is broken by the fact that exam pages are top-heavy (heading at the top,
    signature space at the bottom).
    """
    gray = _to_gray(img)
    h, w = gray.shape
    if h < 60 or w < 60:
        return 0

    scale = min(1.0, 700.0 / max(h, w))
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    _, binary = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if binary.sum() == 0:
        return 0

    def top_heavy(b: np.ndarray) -> float:
        rows = (b > 0).sum(axis=1).astype(np.float64)
        total = rows.sum()
        if total <= 0:
            return 0.0
        n = len(rows)
        # Weight the top of the page more than the bottom.
        weights = np.linspace(1.6, 0.4, n)
        return float((rows * weights).sum() / total)

    scores: dict[int, float] = {}
    heaviness: dict[int, float] = {}
    for angle in (0, 90, 180, 270):
        rot = rotate_exact(binary, angle)
        scores[angle] = _horizontal_text_score(rot)
        heaviness[angle] = top_heavy(rot)

    # Two classes: "text runs horizontally" (0 / 180) and "text runs
    # vertically" (90 / 270).  Decide the class first, then the direction.
    flat = max(scores[0], scores[180])
    side = max(scores[90], scores[270])
    if flat <= 0 and side <= 0:
        return 0

    if flat >= side * 1.6 and flat > 0:
        candidates = [0, 180]
    elif side >= flat * 1.6 and side > 0:
        candidates = [90, 270]
    else:
        # Too ambiguous to act on - leave the page exactly as it was.
        return 0

    # Inside the class, the top-heavy heuristic decides which way is up:
    # exam papers carry their heading at the top and the answer space below.
    best = max(candidates, key=lambda a: heaviness[a])
    if heaviness[candidates[1]] - heaviness[best] > 0.02:
        best = candidates[1]
    return best


def enhance_contrast(img: np.ndarray) -> tuple[np.ndarray, bool]:
    """CLAHE contrast enhancement on the lightness channel.

    Returns (image, changed).  Faint pencil on white paper is the single most
    common failure mode of phone photos of exam papers.
    """
    if img.ndim == 2:
        lab = cv2.cvtColor(img, cv2.COLOR_GRAY2LAB)
    else:
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_channel, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_eq = clahe.apply(l_channel)
    merged = cv2.merge((l_eq, a, b))
    out = cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)
    if img.ndim == 2:
        out = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    return out, True


def fit_size(img: np.ndarray) -> np.ndarray:
    """Resize so the long edge lands in a sensible band for vision models."""
    h, w = img.shape[:2]
    long_edge = max(h, w)
    if long_edge > TARGET_LONG_EDGE:
        scale = TARGET_LONG_EDGE / long_edge
    elif long_edge < MIN_LONG_EDGE:
        scale = MIN_LONG_EDGE / long_edge
    else:
        return img
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(img, (new_w, new_h), interpolation=interp)


def prepare(
    raw: bytes,
    *,
    auto_rotate: bool = True,
    deskew: bool = True,
    enhance: bool = True,
    fmt: str = "jpeg",
    quality: int = 92,
) -> PrepResult:
    """Full preparation pipeline.  Never raises on a bad image."""
    notes: list[str] = []
    try:
        pil = Image.open(io.BytesIO(raw))
        pil = ImageOps.exif_transpose(pil)
        pil = pil.convert("RGB")
    except Exception as exc:  # pragma: no cover - defensive
        notes.append(f"could not decode image: {exc}")
        return PrepResult(data=raw, mime="image/png", width=0, height=0, notes=notes)

    img = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

    rotations = 0
    if auto_rotate:
        angle = detect_orientation(img)
        if angle:
            img = rotate_exact(img, angle)
            rotations = angle

    skew = 0.0
    if deskew:
        gray = _to_gray(img)
        skew = estimate_skew(gray)
        if skew:
            img = rotate_image(img, skew)

    enhanced = False
    if enhance:
        img, enhanced = enhance_contrast(img)

    img = fit_size(img)

    # Pad to a multiple of 8 - some vision backends prefer it, and it keeps
    # the coordinates we report aligned with what the model actually saw.
    h, w = img.shape[:2]
    pad_h = (8 - h % 8) % 8
    pad_w = (8 - w % 8) % 8
    if pad_h or pad_w:
        img = cv2.copyMakeBorder(img, 0, pad_h, 0, pad_w,
                                 cv2.BORDER_CONSTANT, value=(255, 255, 255))

    ok, buf = cv2.imencode(f".{fmt}", img,
                           [int(cv2.IMWRITE_JPEG_QUALITY), quality]
                           if fmt == "jpeg" else [])
    if not ok:
        return PrepResult(data=raw, mime="image/png", width=w, height=h, notes=notes)

    h, w = img.shape[:2]
    return PrepResult(
        data=buf.tobytes(),
        mime=f"image/{fmt}",
        width=w,
        height=h,
        rotations_applied=rotations,
        deskew_angle=round(skew, 2),
        contrast_enhanced=enhanced,
        notes=notes or None,
    )
