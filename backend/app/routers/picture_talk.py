import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_logger import get_logger
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.models.conversation import Conversation
from app.models.user import User
from app.services.llm_adapter import LLMError
from app.services.picture_talk import create_scene
from app.services.situations import get_situation_plan

logger = get_logger(__name__)

router = APIRouter(prefix="/api/picture-talk", tags=["picture-talk"])


class SceneRequest(BaseModel):
    study_plan_id: int = Field(ge=1, le=2_147_483_647)
    conversation_id: int | None = Field(default=None, ge=1, le=2_147_483_647)


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)


class SceneResponse(BaseModel):
    id: int
    study_plan_id: int
    image_prompt: str
    words: list[str]


@router.post("/scenes", response_model=SceneResponse)
@limiter.limit("30/minute")
async def new_scene(
    request: Request,
    data: SceneRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SceneResponse:
    plan = await get_situation_plan(db, current_user.id, data.study_plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="picture_unavailable")
    if data.conversation_id is not None:
        conversation = await db.get(Conversation, data.conversation_id)
        if (
            conversation is None
            or conversation.user_id != current_user.id
            or conversation.study_plan_id != plan.id
        ):
            raise HTTPException(status_code=404, detail="picture_unavailable")
    try:
        scene = await create_scene(db, plan)
    except LLMError:
        logger.warning("[picture-talk] scene generation failed for plan %s", plan.id)
        raise HTTPException(status_code=503, detail="picture_generation_failed") from None
    return SceneResponse(
        id=scene.id,
        study_plan_id=scene.study_plan_id,
        image_prompt=scene.image_prompt,
        words=list(scene.words),
    )


@router.post("/image")
@limiter.limit("10/minute")
async def scene_image(
    request: Request,
    data: ImageRequest,
    current_user: User = Depends(get_current_user),
) -> Response:
    """Proxy the scene prompt to the host image service, for browsers without WebGPU."""
    base = settings.IMAGE_SERVER_BASE_URL.strip().rstrip("/")
    if not base or not current_user.images_enabled:
        raise HTTPException(status_code=404, detail="image_generation_disabled")
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{base}/generate",
                json={"prompt": data.prompt, "width": 512, "height": 512},
                timeout=60.0,
            )
            r.raise_for_status()
    except httpx.HTTPError:
        logger.warning("[picture-talk] image server failed")
        raise HTTPException(status_code=503, detail="image_generation_failed") from None
    return Response(content=r.content, media_type="image/png")
