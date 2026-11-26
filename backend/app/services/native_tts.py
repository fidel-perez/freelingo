"""Second TTS instance that speaks picture talk's native-language line."""

from __future__ import annotations

from app.core.config import settings
from app.services.gemini_tts_service import GeminiTTSService
from app.services.language_helpers import get_iso639
from app.services.tts_service import KokoroTTSService, OpenAITTSService


def parse_native_voices(raw: str) -> dict[str, str]:
    """Read `es=<voice-id>,fr=<voice-id>` into a language to voice map."""
    voices: dict[str, str] = {}
    for part in raw.replace(";", ",").split(","):
        language, sep, voice = part.partition("=")
        language, voice = language.strip().lower(), voice.strip()
        if sep and language and voice:
            voices[language] = voice
    return voices


def native_line_enabled(native_language: str, target_language: str) -> bool:
    """A learner whose native language is the study language gets no native line."""
    return native_language.split("-")[0].lower() != get_iso639(target_language)


def build_native_tts() -> object | None:
    """Build the provider class the settings name, or None when the provider is empty."""
    provider = settings.NATIVE_TTS_PROVIDER
    if provider == "local":
        if not settings.NATIVE_TTS_BASE_URL:
            return None
        return KokoroTTSService(settings.NATIVE_TTS_BASE_URL, "")
    if provider == "openai" and settings.OPENAI_API_KEY:
        return OpenAITTSService(
            api_key=settings.OPENAI_API_KEY,
            model=settings.OPENAI_TTS_MODEL,
            voice=settings.OPENAI_TTS_VOICE,
            female_voice=settings.OPENAI_TTS_FEMALE_VOICE,
            male_voice=settings.OPENAI_TTS_MALE_VOICE,
            speed=settings.OPENAI_TTS_SPEED,
        )
    if provider == "gemini" and settings.GEMINI_API_KEY:
        return GeminiTTSService(
            api_key=settings.GEMINI_API_KEY,
            model=settings.GEMINI_TTS_MODEL,
            voice=settings.GEMINI_TTS_VOICE,
            female_voice=settings.GEMINI_TTS_FEMALE_VOICE,
            male_voice=settings.GEMINI_TTS_MALE_VOICE,
            timeout=settings.GEMINI_TTS_TIMEOUT_SECONDS,
        )
    return None


def resolve_native_voice(native_language: str, target_language: str) -> tuple[object, str] | None:
    """Return the native TTS service and its voice, or None when the native line is not spoken."""
    if not native_line_enabled(native_language, target_language):
        return None
    voice = parse_native_voices(settings.NATIVE_TTS_VOICES).get(native_language.lower())
    if not voice:
        voice = parse_native_voices(settings.NATIVE_TTS_VOICES).get(
            native_language.split("-")[0].lower()
        )
    if not voice:
        return None
    service = build_native_tts()
    return (service, voice) if service is not None else None
