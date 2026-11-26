from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.data.situations import SITUATIONS, SITUATIONS_BY_ID
from app.models.user import User
from app.schemas.situations import SituationsResponse, SituationSummary
from app.services.image_prompts import get_situation_image_prompt
from app.services.situations import get_situation_plan

router = APIRouter(prefix="/api/situations", tags=["situations"])


@router.get("", response_model=SituationsResponse)
@limiter.limit("60/minute")
async def list_situations(
    request: Request,
    study_plan_id: int = Query(ge=1, le=2_147_483_647),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SituationsResponse:
    plan = await get_situation_plan(db, current_user.id, study_plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="situation_unavailable")
    return SituationsResponse(
        study_plan_id=plan.id,
        target_language=plan.target_language,
        cefr_level=plan.cefr_level,
        situations=[SituationSummary(id=item.id, category=item.category) for item in SITUATIONS],
    )


@router.get("/{situation_id}/image-prompt")
@limiter.limit("30/minute")
async def situation_image_prompt(
    request: Request,
    situation_id: str,
    study_plan_id: int = Query(ge=1, le=2_147_483_647),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    plan = await get_situation_plan(db, current_user.id, study_plan_id)
    situation = SITUATIONS_BY_ID.get(situation_id)
    if plan is None or situation is None:
        raise HTTPException(status_code=404, detail="situation_unavailable")
    return {"prompt": await get_situation_image_prompt(db, situation)}
