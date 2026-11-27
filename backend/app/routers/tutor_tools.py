from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_logger import get_logger
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.models.user import User
from app.services.llm_adapter import LLMError
from app.services.mistake_service import log_mistakes
from app.services.situations import get_situation_plan
from app.services.tutor_tools import MAX_LINE_CHARS, correct_line, translate_line

logger = get_logger(__name__)

router = APIRouter(prefix="/api/tutor", tags=["tutor"])


class LineRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_LINE_CHARS)
    study_plan_id: int = Field(ge=1, le=2_147_483_647)


class TranslateResponse(BaseModel):
    translation: str


class FlagMistakeResponse(BaseModel):
    logged: bool
    corrected: str = ""


@router.post("/translate", response_model=TranslateResponse)
@limiter.limit("30/minute")
async def translate(
    request: Request,
    data: LineRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TranslateResponse:
    plan = await get_situation_plan(db, current_user.id, data.study_plan_id, require_active=False)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan_unavailable")
    try:
        translation = await translate_line(
            data.text,
            target_language=plan.target_language,
            native_language=current_user.native_language or "en",
        )
    except LLMError:
        logger.warning("[tutor] translation failed for plan %s", plan.id)
        raise HTTPException(status_code=503, detail="translation_failed") from None
    return TranslateResponse(translation=translation)


@router.post("/flag-mistake", response_model=FlagMistakeResponse)
@limiter.limit("30/minute")
async def flag_mistake(
    request: Request,
    data: LineRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FlagMistakeResponse:
    """Log a learner line the learner flagged, so the mistake log makes it a flashcard."""
    plan = await get_situation_plan(db, current_user.id, data.study_plan_id, require_active=False)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan_unavailable")
    try:
        fix = await correct_line(
            data.text,
            target_language=plan.target_language,
            native_language=current_user.native_language or "en",
        )
    except LLMError:
        logger.warning("[tutor] correction failed for plan %s", plan.id)
        raise HTTPException(status_code=503, detail="correction_failed") from None
    logged = await log_mistakes(db, plan.id, [(data.text, fix.corrected, fix.note)], "voice")
    return FlagMistakeResponse(logged=bool(logged), corrected=fix.corrected if logged else "")
