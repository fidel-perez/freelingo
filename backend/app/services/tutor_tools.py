"""Small tutor helpers behind the conversation buttons: translate a line, flag a mistake."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.language_helpers import get_language_name, get_native_language_name
from app.services.llm_adapter import LLMAdapter, llm_adapter

MAX_LINE_CHARS = 1000

_fast_llm: LLMAdapter | None = None


def tutor_llm() -> LLMAdapter:
    """The fast text model when FAST_LLM_BASE_URL is set, else the default adapter."""
    global _fast_llm
    if not settings.FAST_LLM_BASE_URL:
        return llm_adapter
    if _fast_llm is None:
        _fast_llm = LLMAdapter(base_url=settings.FAST_LLM_BASE_URL, model=settings.FAST_LLM_MODEL)
    return _fast_llm


class MistakeCorrection(BaseModel):
    corrected: str = Field(max_length=MAX_LINE_CHARS)
    note: str = Field(default="", max_length=300)


async def translate_line(text: str, *, target_language: str, native_language: str) -> str:
    """Translate one tutor line from the study language into the learner's native language."""
    prompt = (
        f"Translate the {get_language_name(target_language)} text the user sends into "
        f"{get_native_language_name(native_language)}. Answer with the translation only."
    )
    reply = await tutor_llm().chat(
        [{"role": "system", "content": prompt}, {"role": "user", "content": text}]
    )
    return str(reply).strip()


async def correct_line(
    text: str, *, target_language: str, native_language: str
) -> MistakeCorrection:
    """Correct a line the learner flagged as a mistake."""
    prompt = (
        f"The learner wrote this {get_language_name(target_language)} sentence and says it "
        "holds a mistake. Return JSON with `corrected` (the sentence, corrected; the same "
        "sentence if it is already right) and `note` (one short explanation in "
        f"{get_native_language_name(native_language)})."
    )
    result = await tutor_llm().structured_output(
        [{"role": "system", "content": prompt}, {"role": "user", "content": text}],
        MistakeCorrection,
    )
    assert isinstance(result, MistakeCorrection)
    return result
