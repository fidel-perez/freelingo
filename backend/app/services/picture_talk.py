"""Picture talk: scene generation, scene loading and the hint ladder."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from html import escape

from pydantic import BaseModel, Field
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_logger import get_logger
from app.core.config import settings
from app.data.picture_talk_levels import (
    ANSWERED_INSTRUCTION,
    NATIVE_LINE_MARKER,
    PICTURE_CONTEXT_PREFIX,
    PICTURE_CONTEXT_SUFFIX,
    RUNG_INSTRUCTIONS,
    level_data,
)
from app.models.flashcard import Flashcard
from app.models.picture_talk import PictureTalkScene
from app.models.study_plan import StudyPlan
from app.services.language_helpers import get_language_name
from app.services.llm_adapter import LLMAdapter, llm_adapter
from app.services.mistake_service import MISTAKE_FLASHCARD_SOURCE
from app.services.situations import get_situation_plan

logger = get_logger(__name__)

SCENE_WORD_LIMIT = 5
NATIVE_MARKER_CORE = NATIVE_LINE_MARKER.strip()


class SceneDraft(BaseModel):
    image_prompt: str = Field(min_length=1, max_length=600)
    words: list[str] = Field(default_factory=list, max_length=12)


def scene_llm() -> LLMAdapter:
    """The fast text model of picture talk, or the configured LLM when no fast one is set."""
    if not settings.FAST_LLM_BASE_URL:
        return llm_adapter
    return LLMAdapter(base_url=settings.FAST_LLM_BASE_URL, model=settings.FAST_LLM_MODEL)


# ── Scenes ─────────────────────────────────────────────────────────────────────


async def study_side_words(db: AsyncSession, plan: StudyPlan) -> list[str]:
    """Study-language side of up to five flashcards, due ones and mistake cards first."""
    rows = (
        await db.execute(
            select(Flashcard)
            .where(Flashcard.study_plan_id == plan.id, Flashcard.is_suspended.is_(False))
            .order_by(
                case((Flashcard.next_review <= date.today(), 0), else_=1),
                case((Flashcard.source == MISTAKE_FLASHCARD_SOURCE, 0), else_=1),
                Flashcard.next_review,
                Flashcard.id,
            )
            .limit(SCENE_WORD_LIMIT)
        )
    ).scalars()
    words = []
    for card in rows:
        # A mistake card keeps the learner's text on its front and the corrected study text behind.
        text = card.definition if card.source == MISTAKE_FLASHCARD_SOURCE else card.word
        if text and text.strip():
            words.append(" ".join(text.split())[:120])
    return words


def build_scene_prompt(plan: StudyPlan, words: list[str]) -> str:
    study_language = get_language_name(plan.target_language)
    listed = (
        "\n".join(f"- {word}" for word in words) or "- (none yet, choose common everyday words)"
    )
    return (
        f"Design one picture for a {study_language} speaking exercise at CEFR level "
        f"{plan.cefr_level}. Return a JSON object with two fields.\n"
        "image_prompt: an ENGLISH prompt for an image model that describes one clear, friendly "
        "everyday scene. Name no text, letters or logos in it.\n"
        f"words: the {study_language} words or short phrases a learner at this level can use to "
        "describe the scene, taken from the list below when they fit the scene, at most five.\n"
        f"Candidate {study_language} words:\n{listed}"
    )


async def create_scene(db: AsyncSession, plan: StudyPlan) -> PictureTalkScene:
    words = await study_side_words(db, plan)
    draft = await scene_llm().structured_output(
        [{"role": "user", "content": build_scene_prompt(plan, words)}], SceneDraft
    )
    assert isinstance(draft, SceneDraft)
    scene_words = [w.strip() for w in draft.words if w.strip()][:SCENE_WORD_LIMIT]
    scene = PictureTalkScene(
        study_plan_id=plan.id, image_prompt=draft.image_prompt.strip(), words=scene_words
    )
    db.add(scene)
    await db.commit()
    await db.refresh(scene)
    return scene


@dataclass(frozen=True)
class PictureScene:
    scene: PictureTalkScene
    plan: StudyPlan
    context: str


def scene_context(scene: PictureTalkScene, plan: StudyPlan) -> str:
    reference = {
        "image_prompt": scene.image_prompt,
        "words": scene.words,
        "target_language": plan.target_language,
        "cefr_level": plan.cefr_level,
    }
    return (
        PICTURE_CONTEXT_PREFIX
        + escape(json.dumps(reference, ensure_ascii=False))
        + PICTURE_CONTEXT_SUFFIX
    )


async def load_picture_scene(
    db: AsyncSession, user_id: int, scene_id: object, *, study_plan_id: int | None = None
) -> PictureScene | None:
    """Load a scene for the owner of its plan; a plan id pins the scene to that plan."""
    if type(scene_id) is not int or not 1 <= scene_id <= 2_147_483_647:
        return None
    scene = await db.get(PictureTalkScene, scene_id)
    if scene is None or (study_plan_id is not None and scene.study_plan_id != study_plan_id):
        return None
    plan = await get_situation_plan(db, user_id, scene.study_plan_id)
    if plan is None:
        return None
    return PictureScene(scene=scene, plan=plan, context=scene_context(scene, plan))


# ── Native line text ───────────────────────────────────────────────────────────


def is_native_line(line: str) -> bool:
    return line.lstrip().startswith(NATIVE_MARKER_CORE)


def native_line_text(line: str) -> str:
    return line.lstrip()[len(NATIVE_MARKER_CORE) :].strip()


def split_picture_text(text: str) -> tuple[list[str], list[str]]:
    """Return the study lines and the native lines of a tutor reply."""
    study: list[str] = []
    native: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if is_native_line(line):
            native.append(native_line_text(line))
        else:
            study.append(line.strip())
    return study, native


def display_text(text: str) -> str:
    """The reply with the native marker removed, one line per original line."""
    lines = [
        native_line_text(line) if is_native_line(line) else line.strip()
        for line in text.splitlines()
        if line.strip()
    ]
    return "\n".join(lines)


# ── Hint ladder ────────────────────────────────────────────────────────────────


@dataclass
class PictureTalkState:
    scene_id: int
    words: list[str]
    image_prompt: str
    level: str
    native_lines: bool = True
    rung: int = 0
    open_question: bool = False
    active_rung: str | None = None
    answered: bool = False

    def load_scene(self, scene_id: int, words: list[str], image_prompt: str) -> None:
        self.scene_id = scene_id
        self.words = words
        self.image_prompt = image_prompt
        self.reset_ladder()
        self.open_question = False

    def reset_ladder(self) -> None:
        self.rung = 0
        self.active_rung = None
        self.answered = False

    def step(self) -> str:
        """Move one rung up on a "took too long" press; a ladder past its end starts over."""
        rungs = level_data(self.level).rungs
        if self.rung >= len(rungs):
            self.rung = 0
        self.rung += 1
        self.answered = False
        self.active_rung = rungs[self.rung - 1]
        return self.active_rung

    def answer(self) -> None:
        """An "I answered" press ends the turn with no rung."""
        self.reset_ladder()
        self.answered = True

    def turn_context(self) -> str:
        parts: list[str] = []
        if self.active_rung:
            parts.append(
                f"Hint ladder, rung {self.rung} ({self.active_rung}): "
                + RUNG_INSTRUCTIONS[self.active_rung]
            )
        elif self.answered:
            parts.append(ANSWERED_INSTRUCTION)
        if not parts:
            return ""
        return "\n<picture_turn>\n" + "\n".join(f"- {p}" for p in parts) + "\n</picture_turn>\n"
