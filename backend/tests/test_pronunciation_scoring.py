"""Acoustic pronunciation scoring: local provider, answer route and LLM fallback."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.config import settings
from app.schemas.lessons import PronunciationEvaluation
from app.services.pronunciation_service import (
    LocalPronunciationProvider,
    PronunciationUnavailable,
    load_phoneme_data,
    score_phonemes,
)

TARGET = "안녕하세요"
SPOKEN_OK = "annjʌŋhasejo"
SPOKEN_BAD = "mijoŋtʃuki"


def _service_response(phonemes: str = SPOKEN_OK) -> httpx.Response:
    return httpx.Response(
        200,
        json={"phonemes": phonemes},
        request=httpx.Request("POST", "http://pron/v1/phonemes"),
    )


def _mock_service(mock_post: AsyncMock):
    """Replace httpx inside the service module only, so the test client keeps working."""

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        post = staticmethod(mock_post)

    fake = SimpleNamespace(AsyncClient=FakeClient, HTTPError=httpx.HTTPError)
    return patch("app.services.pronunciation_service.httpx", fake)


async def _pronunciation_exercise(db_session, user_id: int, target_language: str):
    from app.models.lesson import Exercise, Lesson
    from tests.conftest import make_study_plan

    plan = await make_study_plan(
        db_session,
        user_id=user_id,
        target_language=target_language,
        cefr_level="A1",
        goals=["speaking"],
        duration_weeks=4,
        days_per_week=4,
        current_unit="",
        generated_plan={},
        is_active=True,
    )
    lesson = Lesson(
        study_plan_id=plan.id,
        title="Pronunciation",
        lesson_type="speaking",
        cefr_level="A1",
        week_number=1,
        day_number=1,
        content={},
    )
    db_session.add(lesson)
    await db_session.flush()
    exercise = Exercise(
        lesson_id=lesson.id,
        exercise_type="pronunciation",
        question="Listen and repeat:",
        options=None,
        correct_answer=TARGET if target_language == "ko-KR" else "Hello there",
    )
    db_session.add(exercise)
    await db_session.commit()
    return exercise


@pytest.fixture
def local_provider(monkeypatch):
    monkeypatch.setattr(settings, "PRONUNCIATION_PROVIDER", "local")
    monkeypatch.setattr(settings, "PRONUNCIATION_BASE_URL", "http://pron:8000")


def _llm_mock() -> AsyncMock:
    return AsyncMock(
        return_value=PronunciationEvaluation(score=0.6, feedback="LLM verdict.", is_correct=True)
    )


def test_matching_audio_scores_higher_than_mismatch():
    data = load_phoneme_data("ko-KR")
    match = score_phonemes(TARGET, SPOKEN_OK, data)
    mismatch = score_phonemes(TARGET, SPOKEN_BAD, data)

    assert match.score > mismatch.score
    assert match.score == 1.0
    assert [w.word for w in match.words] == [TARGET]
    assert mismatch.words[0].weak_phonemes


@pytest.mark.asyncio
async def test_local_provider_posts_audio():
    provider = LocalPronunciationProvider("http://pron:8000/")
    post = AsyncMock(return_value=_service_response())
    with _mock_service(post):
        result = await provider.score(
            b"audio-bytes",
            "a.webm",
            "audio/webm",
            target=TARGET,
            target_language="ko-KR",
        )

    assert post.await_args.args[0] == "http://pron:8000/v1/phonemes"
    assert post.await_args.kwargs["files"]["file"] == ("a.webm", b"audio-bytes", "audio/webm")
    assert post.await_args.kwargs["data"] == {"language": "ko-KR"}
    assert result.score == 1.0


@pytest.mark.asyncio
async def test_falls_back_to_llm_when_service_fails(client, test_user, db_session, local_provider):
    user, headers = test_user
    exercise = await _pronunciation_exercise(db_session, user.id, "ko-KR")
    llm = _llm_mock()

    with (
        _mock_service(AsyncMock(side_effect=httpx.ConnectError("down"))),
        patch("app.routers.lessons.evaluate_pronunciation", llm),
    ):
        response = await client.post(
            f"/api/lessons/exercises/{exercise.id}/answer",
            headers=headers,
            data={"answer": TARGET},
            files={"audio": ("a.webm", b"audio-bytes", "audio/webm")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["score"] == 0.6
    assert body["feedback"] == "LLM verdict."
    assert body["words"] is None
    llm.assert_awaited_once()


@pytest.mark.asyncio
async def test_language_without_data_uses_llm(client, test_user, db_session, local_provider):
    user, headers = test_user
    exercise = await _pronunciation_exercise(db_session, user.id, "en-US")
    llm = _llm_mock()
    post = AsyncMock(return_value=_service_response())

    assert load_phoneme_data("en-US") is None
    with (
        _mock_service(post),
        patch("app.routers.lessons.evaluate_pronunciation", llm),
    ):
        response = await client.post(
            f"/api/lessons/exercises/{exercise.id}/answer",
            headers=headers,
            data={"answer": "Hello there"},
            files={"audio": ("a.webm", b"audio-bytes", "audio/webm")},
        )

    assert response.status_code == 200
    assert response.json()["feedback"] == "LLM verdict."
    post.assert_not_awaited()
    llm.assert_awaited_once()
    with pytest.raises(PronunciationUnavailable):
        await LocalPronunciationProvider("http://pron:8000").score(
            b"x", "a.webm", "audio/webm", target="Hello there", target_language="en-US"
        )


@pytest.mark.asyncio
async def test_transcript_only_call_still_works(client, test_user, db_session, local_provider):
    user, headers = test_user
    exercise = await _pronunciation_exercise(db_session, user.id, "ko-KR")
    llm = _llm_mock()
    post = AsyncMock(return_value=_service_response())

    with (
        _mock_service(post),
        patch("app.routers.lessons.evaluate_pronunciation", llm),
    ):
        response = await client.post(
            f"/api/lessons/exercises/{exercise.id}/answer",
            headers=headers,
            json={"answer": TARGET},
        )

    assert response.status_code == 200
    assert response.json()["feedback"] == "LLM verdict."
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_audio_answer_returns_word_scores(client, test_user, db_session, local_provider):
    user, headers = test_user
    exercise = await _pronunciation_exercise(db_session, user.id, "ko-KR")

    with _mock_service(AsyncMock(return_value=_service_response())):
        response = await client.post(
            f"/api/lessons/exercises/{exercise.id}/answer",
            headers=headers,
            data={"answer": TARGET},
            files={"audio": ("a.webm", b"audio-bytes", "audio/webm")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["score"] == 1.0
    assert body["words"] == [{"word": TARGET, "score": 1.0, "weak_phonemes": []}]
