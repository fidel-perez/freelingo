"""Short English image prompts for lessons and situations, cached after the first LLM call."""

import asyncio
import logging

from fastapi import HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.situations import Situation
from app.models.lesson import Lesson, SituationImagePrompt
from app.services.llm_adapter import LLMError, llm_adapter

logger = logging.getLogger(__name__)

IMAGE_PROMPT_TIMEOUT_SECONDS = 20.0

_INSTRUCTIONS = (
    "Write one English sentence, at most 40 words, that describes an image illustrating the "
    "topic below. Use a flat illustration style. The image must contain no text, letters, "
    "numbers or signs. Reply in English whatever the language of the topic."
)


class ImagePromptResult(BaseModel):
    prompt: str = Field(min_length=1, max_length=600)


def _lesson_topic(lesson: Lesson) -> str:
    content = lesson.content if isinstance(lesson.content, dict) else {}
    explanation = content.get("explanation")
    summary = explanation.get("text") if isinstance(explanation, dict) else None
    topic = f"Lesson title: {lesson.title}\nLesson type: {lesson.lesson_type}"
    if isinstance(summary, str) and summary.strip():
        topic += f"\nSummary: {summary.strip()[:400]}"
    return topic


async def _generate(topic: str) -> str:
    deadline = asyncio.get_running_loop().time() + IMAGE_PROMPT_TIMEOUT_SECONDS
    try:
        result = await llm_adapter.structured_output(
            [{"role": "user", "content": f"{_INSTRUCTIONS}\n\n{topic}"}],
            ImagePromptResult,
            deadline=deadline,
        )
    except LLMError as exc:
        logger.warning("[image-prompt] generation failed (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="image_prompt_unavailable"
        ) from exc
    return " ".join(result.prompt.split())


async def get_lesson_image_prompt(db: AsyncSession, lesson: Lesson) -> str:
    if lesson.image_prompt:
        return lesson.image_prompt
    lesson_id, topic = lesson.id, _lesson_topic(lesson)
    await db.commit()
    prompt = await _generate(topic)
    lesson = await db.get(Lesson, lesson_id, populate_existing=True)
    if lesson is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lesson not found")
    if not lesson.image_prompt:
        lesson.image_prompt = prompt
        await db.commit()
    return lesson.image_prompt


async def get_situation_image_prompt(db: AsyncSession, situation: Situation) -> str:
    query = select(SituationImagePrompt.prompt).where(
        SituationImagePrompt.situation_id == situation.id
    )
    cached = await db.scalar(query)
    if cached:
        return cached
    await db.commit()
    topic = f"Situation: {situation.title}\nScenario: {situation.scenario}"
    prompt = await _generate(topic)
    db.add(SituationImagePrompt(situation_id=situation.id, prompt=prompt))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return await db.scalar(query) or prompt
    return prompt
