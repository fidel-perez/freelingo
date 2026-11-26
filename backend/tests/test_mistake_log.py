"""Mistake log: chat, voice and exercise capture, flashcards and plan isolation."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select

from app.models.flashcard import Flashcard
from app.models.lesson import Exercise, Lesson
from app.models.mistake import Mistake
from app.schemas.lessons import FreeWriteCorrection, FreeWriteEvaluation
from app.services.conversation_pipeline import ConversationPipeline
from app.services.llm_adapter import LLMToolCall, LLMToolResultEvent
from app.services.mistake_service import log_mistakes
from tests.conftest import make_study_plan


@asynccontextmanager
async def _same_session(session):
    yield session


def _call(original="I goed home", corrected="I went home", note="Irregular past"):
    arguments = {"original": original, "corrected": corrected, "note": note}
    return LLMToolCall(id="call_1", name="log_mistake", arguments=arguments, raw_arguments="{}")


async def _plan(db_session, user_id, **kwargs):
    plan = await make_study_plan(
        db_session,
        user_id=user_id,
        cefr_level="A2",
        goals=["grammar"],
        duration_weeks=4,
        days_per_week=4,
        current_unit="",
        generated_plan={},
        **kwargs,
    )
    await db_session.commit()
    return plan


class _Stream:
    def __init__(self, results):
        self.tool_results = results

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for result in self.tool_results:
            yield LLMToolResultEvent(result=result)
        yield "Good try."


async def test_chat_correction_logs_mistake(client, test_user, db_session):
    user, headers = test_user
    plan = await _plan(db_session, user.id, is_active=True)

    async def fake_chat(_messages, **kwargs):
        assert {t.name for t in kwargs["tools"]} == {"save_user_memory", "log_mistake"}
        return _Stream([await kwargs["tool_executor"](_call())])

    with (
        patch("app.routers.chat.db_session", side_effect=lambda: _same_session(db_session)),
        patch("app.routers.chat.llm_adapter.chat", new_callable=AsyncMock, side_effect=fake_chat),
    ):
        response = await client.post("/api/chat", headers=headers, json={"message": "I goed home"})
    assert response.status_code == 200
    assert "memory_updated" not in response.text
    mistake = (await db_session.execute(select(Mistake))).scalar_one()
    assert (mistake.study_plan_id, mistake.source) == (plan.id, "chat")
    assert (mistake.original, mistake.corrected) == ("I goed home", "I went home")


async def test_voice_correction_logs_mistake(db_session, test_user):
    user, _ = test_user
    plan = await _plan(db_session, user.id, is_active=True)
    pipeline = ConversationPipeline(
        llm=AsyncMock(),
        tts=AsyncMock(),
        stt=AsyncMock(),
        cefr_level="B1",
        max_duration=1800,
        inactivity_timeout=180,
        user_id=user.id,
        study_plan_id=plan.id,
    )
    pipeline.stt.transcribe = AsyncMock(return_value="I goed home")
    pipeline.tts.synthesize = AsyncMock(return_value=b"audio")

    async def fake_chat(_messages, **kwargs):
        assert "log_mistake" in {t.name for t in kwargs["tools"]}
        return _Stream([await kwargs["tool_executor"](_call())])

    pipeline.llm.chat = AsyncMock(side_effect=fake_chat)
    ws = MagicMock(send_json=AsyncMock(), send_bytes=AsyncMock())
    with patch(
        "app.services.conversation_pipeline.db_session",
        side_effect=lambda: _same_session(db_session),
    ):
        await pipeline._process(b"audio", ws)
    mistake = (await db_session.execute(select(Mistake))).scalar_one()
    assert (mistake.study_plan_id, mistake.source) == (plan.id, "voice")


async def test_exercise_corrections_log_mistakes(client, test_user, db_session):
    user, headers = test_user
    plan = await _plan(db_session, user.id, is_active=True)
    lesson = Lesson(
        study_plan_id=plan.id,
        title="Lesson",
        lesson_type="grammar",
        cefr_level="A2",
        week_number=1,
        day_number=1,
        content={},
    )
    db_session.add(lesson)
    await db_session.flush()
    exercise = Exercise(
        lesson_id=lesson.id,
        exercise_type="free_write",
        question="Describe your day.",
        options=["grammar"],
        correct_answer="Sample.",
    )
    db_session.add(exercise)
    await db_session.commit()
    evaluation = FreeWriteEvaluation(
        score=0.5,
        feedback="Ok.",
        corrections=[
            FreeWriteCorrection(original="I goed", corrected="I went", explanation="Past"),
            FreeWriteCorrection(original="two cat", corrected="two cats"),
        ],
    )
    llm = AsyncMock(return_value=evaluation)
    with patch("app.routers.lessons.evaluate_free_write", llm):
        response = await client.post(
            f"/api/lessons/exercises/{exercise.id}/answer",
            headers=headers,
            json={"answer": "I goed home with two cat."},
        )
    assert response.status_code == 200
    mistakes = (await db_session.execute(select(Mistake).order_by(Mistake.id))).scalars().all()
    assert [(m.original, m.source, m.study_plan_id) for m in mistakes] == [
        ("I goed", "exercise", plan.id),
        ("two cat", "exercise", plan.id),
    ]
    assert llm.await_count == 1


async def test_mistake_creates_flashcard(db_session, test_user):
    plan = await _plan(db_session, test_user[0].id)
    assert await log_mistakes(db_session, plan.id, [("I goed", "I went", "Past")], "chat") == 1
    mistake = (await db_session.execute(select(Mistake))).scalar_one()
    card = await db_session.get(Flashcard, mistake.flashcard_id)
    assert (card.study_plan_id, card.source, card.repetitions) == (plan.id, "mistake", 0)
    assert card.word == "I goed"
    assert card.definition == "I went"
    assert "Past" in card.example_sentence


async def test_repeat_mistake_updates_card(db_session, test_user):
    plan = await _plan(db_session, test_user[0].id)
    await log_mistakes(db_session, plan.id, [("I goed", "I went", "Past")], "chat")
    card = (await db_session.execute(select(Flashcard))).scalar_one()
    card.repetitions, card.interval = 3, 15
    await db_session.commit()

    await log_mistakes(db_session, plan.id, [(" i  GOED ", "I went", "Irregular")], "voice")

    mistake = (await db_session.execute(select(Mistake))).scalar_one()
    cards = (await db_session.execute(select(Flashcard))).scalars().all()
    assert len(cards) == 1
    await db_session.refresh(cards[0])
    assert mistake.occurrences == 2
    assert mistake.note == "Irregular"
    assert (cards[0].repetitions, cards[0].interval) == (0, 1)


async def test_mistakes_isolated_per_plan(db_session, test_user):
    user = test_user[0]
    first = await _plan(db_session, user.id, is_active=True)
    second = await _plan(db_session, user.id, target_language="de-DE")
    await log_mistakes(db_session, first.id, [("I goed", "I went", "")], "chat")
    await log_mistakes(db_session, second.id, [("I goed", "Ich ging", "")], "chat")

    rows = (await db_session.execute(select(Mistake).order_by(Mistake.study_plan_id))).scalars()
    assert [(m.study_plan_id, m.occurrences) for m in rows] == [(first.id, 1), (second.id, 1)]
    cards = (await db_session.execute(select(Flashcard))).scalars().all()
    assert {c.study_plan_id for c in cards} == {first.id, second.id}
