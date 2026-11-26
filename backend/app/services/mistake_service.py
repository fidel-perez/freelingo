from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_logger import get_logger
from app.models.flashcard import Flashcard
from app.models.mistake import Mistake
from app.models.study_plan import StudyPlan
from app.services.flashcard_sm2 import sm2_update
from app.services.llm_adapter import LLMTool, LLMToolCall, LLMToolResult

logger = get_logger(__name__)

LOG_MISTAKE_TOOL_NAME = "log_mistake"
MISTAKE_SOURCES = ("chat", "voice", "exercise")
MAX_MISTAKE_CHARS = 200
MAX_NOTE_CHARS = 300
MISTAKE_FLASHCARD_SOURCE = "mistake"
# Quality 1 restarts the SM-2 schedule: the learner repeated a mistake.
REPEAT_MISTAKE_QUALITY = 1


def build_log_mistake_tool(native_language_name: str) -> LLMTool:
    return LLMTool(
        name=LOG_MISTAKE_TOOL_NAME,
        description=(
            "Log one mistake you just corrected in the student's target-language text. "
            f"Write the note in the student's native language: {native_language_name}."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "original": {
                    "type": "string",
                    "maxLength": MAX_MISTAKE_CHARS,
                    "description": "The student's text with the mistake.",
                },
                "corrected": {
                    "type": "string",
                    "maxLength": MAX_MISTAKE_CHARS,
                    "description": "The corrected text.",
                },
                "note": {
                    "type": "string",
                    "maxLength": MAX_NOTE_CHARS,
                    "description": f"A short explanation in {native_language_name}.",
                },
            },
            "required": ["original", "corrected"],
            "additionalProperties": False,
        },
    )


def _key(text: str) -> str:
    return " ".join(text.split()).casefold()[:300]


def _clean(value: object, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _apply_card(card: Flashcard, mistake: Mistake) -> None:
    card.word = mistake.original[:100]
    card.definition = mistake.corrected
    card.example_sentence = mistake.note or mistake.corrected
    card.translation = mistake.corrected[:255]


async def log_mistakes(
    db: AsyncSession,
    study_plan_id: int,
    entries: Iterable[tuple[str, str, str]],
    source: str,
    *,
    commit: bool = True,
) -> int:
    """Log (original, corrected, note) entries in a plan and return how many were stored.

    A new mistake creates one SM-2 flashcard in the same plan. A repeat updates the
    existing row and restarts its card's schedule.
    """
    if source not in MISTAKE_SOURCES:
        raise ValueError(f"Unknown mistake source: {source}")
    plan = (
        await db.execute(select(StudyPlan).where(StudyPlan.id == study_plan_id).with_for_update())
    ).scalar_one_or_none()
    if plan is None:
        return 0

    stored = 0
    for raw_original, raw_corrected, raw_note in entries:
        original = _clean(raw_original, MAX_MISTAKE_CHARS)
        corrected = _clean(raw_corrected, MAX_MISTAKE_CHARS)
        note = _clean(raw_note, MAX_NOTE_CHARS)
        if not original or not corrected or _key(original) == _key(corrected):
            continue
        mistake = (
            await db.execute(
                select(Mistake).where(
                    Mistake.study_plan_id == plan.id, Mistake.original_key == _key(original)
                )
            )
        ).scalar_one_or_none()
        if mistake is None:
            mistake = Mistake(
                study_plan_id=plan.id,
                original=original,
                original_key=_key(original),
                corrected=corrected,
                note=note,
                source=source,
            )
            db.add(mistake)
        else:
            mistake.corrected = corrected
            mistake.note = note or mistake.note
            mistake.occurrences += 1
        card = await db.get(Flashcard, mistake.flashcard_id) if mistake.flashcard_id else None
        if card is None:
            card = Flashcard(
                user_id=plan.user_id,
                study_plan_id=plan.id,
                source=MISTAKE_FLASHCARD_SOURCE,
                word="",
                definition="",
                example_sentence="",
                translation="",
            )
            db.add(card)
            _apply_card(card, mistake)
            await db.flush()
            mistake.flashcard_id = card.id
        else:
            _apply_card(card, mistake)
            sm2_update(card, REPEAT_MISTAKE_QUALITY)
        stored += 1
    if commit:
        await db.commit()
    else:
        await db.flush()
    return stored


async def execute_log_mistake(
    db: AsyncSession,
    call: LLMToolCall,
    source: str,
    *,
    study_plan_id: int | None,
) -> LLMToolResult:
    if call.name != LOG_MISTAKE_TOOL_NAME:
        return LLMToolResult(
            call=call, content={"logged": False, "error": "unknown_tool"}, is_error=True
        )
    args = call.arguments
    entry = (
        _clean(args.get("original"), MAX_MISTAKE_CHARS),
        _clean(args.get("corrected"), MAX_MISTAKE_CHARS),
        _clean(args.get("note"), MAX_NOTE_CHARS),
    )
    if study_plan_id is None or not entry[0] or not entry[1]:
        return LLMToolResult(
            call=call, content={"logged": False, "error": "invalid_content"}, is_error=True
        )
    try:
        logged = await log_mistakes(db, study_plan_id, [entry], source)
    except Exception:
        await db.rollback()
        logger.exception("Failed to log mistake for plan %s", study_plan_id)
        return LLMToolResult(
            call=call, content={"logged": False, "error": "persistence_failed"}, is_error=True
        )
    return LLMToolResult(call=call, content={"logged": bool(logged)})
