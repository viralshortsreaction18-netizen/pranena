"""Engine registry and factory."""

from __future__ import annotations

from ..schemas import EngineSpec
from .base import BaseEngine, EngineError, EngineResult, RawBlock

__all__ = [
    "BaseEngine",
    "EngineError",
    "EngineResult",
    "RawBlock",
    "build_engine",
]


def build_engine(spec: EngineSpec) -> BaseEngine:
    """Instantiate the engine implementation for a provider spec."""
    provider = spec.provider

    if provider == "anthropic":
        from .engines.anthropic_engine import AnthropicEngine

        return AnthropicEngine(spec)
    if provider == "gemini":
        from .engines.gemini_engine import GeminiEngine

        return GeminiEngine(spec)
    if provider in ("openai", "mistral", "groq", "openrouter", "together",
                    "fireworks", "siliconflow", "custom"):
        from .engines.openai_engine import OpenAICompatibleEngine

        return OpenAICompatibleEngine(spec)
    if provider == "ollama":
        from .engines.ollama_engine import OllamaEngine

        return OllamaEngine(spec)
    if provider == "tesseract":
        from .engines.tesseract_engine import TesseractEngine

        return TesseractEngine(spec)
    if provider == "demo":
        from .engines.demo_engine import DemoEngine

        return DemoEngine(spec)

    raise EngineError(f"unknown provider: {provider}")
