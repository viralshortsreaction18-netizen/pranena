"""Runtime configuration and the OCR provider registry.

Provider credentials are read from the environment *or* supplied per-request by
the user from the Settings panel in the UI.  Environment variables win only when
the user has not set a key in the UI, so a shared deployment can still work.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .schemas import EngineSpec

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PRANENA_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"

for _d in (DATA_DIR, UPLOAD_DIR, EXPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    kind: str  # anthropic | gemini | openai | ollama | tesseract | demo
    default_model: str
    default_base_url: str | None = None
    env_key: str | None = None
    key_url: str = ""
    local: bool = False
    notes: str = ""
    # Models that are known-good for handwriting transcription.
    recommended_models: tuple[str, ...] = ()


PROVIDERS: dict[str, Provider] = {
    "anthropic": Provider(
        id="anthropic",
        label="Claude (Anthropic)",
        kind="anthropic",
        default_model="claude-sonnet-5-5",
        default_base_url="https://api.anthropic.com",
        env_key="ANTHROPIC_API_KEY",
        key_url="https://console.anthropic.com/settings/keys",
        notes="Best-in-class for messy handwriting; native JSON-schema output.",
        recommended_models=(
            "claude-opus-5-5",
            "claude-sonnet-5-5",
            "claude-haiku-4-5-20251001",
        ),
    ),
    "gemini": Provider(
        id="gemini",
        label="Gemini (Google AI Studio)",
        kind="gemini",
        default_model="gemini-3.8-flash",
        default_base_url="https://generativelanguage.googleapis.com",
        env_key="GEMINI_API_KEY",
        key_url="https://aistudio.google.com/apikey",
        notes="Free tier available (no card needed). Strong on Devanagari.",
        recommended_models=(
            "gemini-3.8-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
        ),
    ),
    "openai": Provider(
        id="openai",
        label="OpenAI",
        kind="openai",
        default_model="gpt-5.1",
        default_base_url="https://api.openai.com",
        env_key="OPENAI_API_KEY",
        key_url="https://platform.openai.com/api-keys",
        notes="High accuracy on clean handwriting.",
        recommended_models=("gpt-5.1", "gpt-5", "gpt-5-mini"),
    ),
    "mistral": Provider(
        id="mistral",
        label="Mistral OCR",
        kind="openai",
        default_model="mistral-ocr-latest",
        default_base_url="https://api.mistral.ai",
        env_key="MISTRAL_API_KEY",
        key_url="https://console.mistral.ai/api-keys",
        notes="Mistral OCR 3 leads on handwriting & tables (88.9% internal bench).",
    ),
    "groq": Provider(
        id="groq",
        label="Groq (OpenAI-compatible)",
        kind="openai",
        default_model="meta-llama/llama-4-scout-17b-16e-instruct",
        default_base_url="https://api.groq.com/openai",
        env_key="GROQ_API_KEY",
        key_url="https://console.groq.com/keys",
    ),
    "openrouter": Provider(
        id="openrouter",
        label="OpenRouter (any model)",
        kind="openai",
        default_model="google/gemini-2.5-flash",
        default_base_url="https://openrouter.ai/api",
        env_key="OPENROUTER_API_KEY",
        key_url="https://openrouter.ai/keys",
    ),
    "ollama": Provider(
        id="ollama",
        label="Ollama (local, offline)",
        kind="ollama",
        default_model="qwen3-vl:8b",
        default_base_url="http://127.0.0.1:11434",
        local=True,
        notes="Fully offline. Qwen3-VL-8B scored chrF++ 75.2 on real Devanagari scans.",
    ),
    "tesseract": Provider(
        id="tesseract",
        label="Tesseract (local, offline)",
        kind="tesseract",
        default_model="eng+hin+san",
        local=True,
        notes="Printed text only — weak on handwriting, but free and instant.",
    ),
    "demo": Provider(
        id="demo",
        label="Demo (no key, synthetic sample)",
        kind="demo",
        default_model="demo-1",
        local=True,
        notes="Lets you explore the whole app without any API key.",
    ),
}


def env_key_for(provider_id: str) -> str | None:
    p = PROVIDERS.get(provider_id)
    return p.env_key if p else None


def default_engine_specs() -> list[EngineSpec]:
    """Build the engine list from environment variables.

    Any provider with a key present in the environment is enabled by default so
    the app works out of the box on a configured machine.
    """
    specs: list[EngineSpec] = []
    for pid, p in PROVIDERS.items():
        key = os.environ.get(p.env_key) if p.env_key else None
        if p.id == "demo":
            specs.append(
                EngineSpec(
                    id="demo",
                    label=p.label,
                    provider="demo",
                    model=p.default_model,
                    local=True,
                    enabled=bool(key) or p.id == "demo",
                )
            )
            continue
        if key:
            specs.append(
                EngineSpec(
                    id=pid,
                    label=p.label,
                    provider=pid,
                    model=p.default_model,
                    api_key=key,
                    base_url=p.default_base_url,
                    enabled=True,
                )
            )
    if not any(s.id == "demo" for s in specs):
        demo = PROVIDERS["demo"]
        specs.append(
            EngineSpec(
                id="demo",
                label=demo.label,
                provider="demo",
                model=demo.default_model,
                local=True,
                enabled=True,
            )
        )
    return specs


@dataclass
class AppState:
    """Process-wide mutable state (engine registry)."""

    engines: dict[str, EngineSpec] = field(default_factory=dict)
    documents: dict[str, "Document"] = field(default_factory=dict)  # type: ignore[name-defined]

    def engine(self, engine_id: str) -> EngineSpec | None:
        return self.engines.get(engine_id)


state = AppState()


def load_engines() -> None:
    for spec in default_engine_specs():
        state.engines[spec.id] = spec


load_engines()
