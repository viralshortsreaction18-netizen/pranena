"""Text post-processing: Devanagari normalisation, structure inference, QA flags."""

from . import devanagari, structure, verify  # noqa: F401

__all__ = ["devanagari", "structure", "verify"]
