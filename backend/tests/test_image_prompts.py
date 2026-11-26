"""Tests for cached English image prompts and the images_enabled setting."""

from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models.lesson import Lesson, SituationImagePrompt
from app.models.user import User
from app.models.user_language import UserLanguage
from tests.conftest import make_study_plan

PROMPT = "A flat illustration of two friends talking at a cafe table, no text."
LLM = "app.services.image_prompts.llm_adapter.structured_output"


def _llm_result():
    from app.services.image_prompts import ImagePromptResult

    return AsyncMock(return_value=ImagePromptResult(prompt=PROMPT))


async def _lesson(db_session, user_id, title="Der Akkusativ"):
    plan = await make_study_plan(db_session, user_id=user_id, cefr_level="A1", is_active=True)
    lesson = Lesson(
        study_plan_id=plan.id,
        title=title,
        lesson_type="grammar",
        cefr_level="A1",
        week_number=1,
        day_number=1,
        content={},
    )
    db_session.add(lesson)
    await db_session.commit()
    await db_session.refresh(lesson)
    return plan, lesson


async def _other_user(db_session):
    user = User(
        username="other",
        email="other@example.com",
        display_name="Other",
        hashed_password=hash_password("testpass"),
        role="user",
        native_language="es",
        target_language="en-US",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(UserLanguage(user_id=user.id, target_language="en-US", is_active=True))
    await db_session.commit()
    return user, {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def test_lesson_image_prompt_is_english(client, test_user, db_session):
    user, headers = test_user
    _, lesson = await _lesson(db_session, user.id)

    with patch(LLM, new=_llm_result()) as llm:
        response = await client.get(f"/api/lessons/{lesson.id}/image-prompt", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"prompt": PROMPT}
    sent = llm.await_args.args[0][0]["content"]
    assert "English" in sent and "Der Akkusativ" in sent
    await db_session.refresh(lesson)
    assert lesson.image_prompt == PROMPT


async def test_situation_image_prompt_is_english(client, test_user, db_session):
    user, headers = test_user
    plan, _ = await _lesson(db_session, user.id)

    with patch(LLM, new=_llm_result()) as llm:
        response = await client.get(
            f"/api/situations/flat_viewing/image-prompt?study_plan_id={plan.id}", headers=headers
        )

    assert response.status_code == 200
    assert response.json() == {"prompt": PROMPT}
    assert "English" in llm.await_args.args[0][0]["content"]
    row = await db_session.scalar(
        select(SituationImagePrompt).where(SituationImagePrompt.situation_id == "flat_viewing")
    )
    assert row is not None and row.prompt == PROMPT


async def test_prompt_cached_on_revisit(client, test_user, db_session):
    user, headers = test_user
    plan, lesson = await _lesson(db_session, user.id)
    situation_url = f"/api/situations/flat_viewing/image-prompt?study_plan_id={plan.id}"
    lesson_url = f"/api/lessons/{lesson.id}/image-prompt"

    with patch(LLM, new=_llm_result()) as llm:
        for _ in range(2):
            assert (await client.get(lesson_url, headers=headers)).json() == {"prompt": PROMPT}
            assert (await client.get(situation_url, headers=headers)).json() == {"prompt": PROMPT}

    assert llm.await_count == 2


async def test_prompt_requires_owned_plan(client, test_user, db_session):
    user, _ = test_user
    _, lesson = await _lesson(db_session, user.id)
    _, other_headers = await _other_user(db_session)

    with patch(LLM, new=_llm_result()) as llm:
        response = await client.get(f"/api/lessons/{lesson.id}/image-prompt", headers=other_headers)

    assert response.status_code == 404
    llm.assert_not_awaited()


async def test_situation_prompt_requires_owned_plan(client, test_user, db_session):
    user, _ = test_user
    plan, _ = await _lesson(db_session, user.id)
    _, other_headers = await _other_user(db_session)

    with patch(LLM, new=_llm_result()) as llm:
        response = await client.get(
            f"/api/situations/flat_viewing/image-prompt?study_plan_id={plan.id}",
            headers=other_headers,
        )

    assert response.status_code == 404
    llm.assert_not_awaited()


async def test_settings_update_images_enabled(client, test_user):
    _, headers = test_user

    before = (await client.get("/api/auth/me", headers=headers)).json()
    assert before["images_enabled"] is False
    assert before["image_model_base_url"] == ""
    assert before["image_model_configured"] is False

    response = await client.patch("/api/auth/me", headers=headers, json={"images_enabled": True})
    assert response.status_code == 200
    assert response.json()["images_enabled"] is True
    assert (await client.get("/api/auth/me", headers=headers)).json()["images_enabled"] is True

    with patch("app.schemas.auth.settings.IMAGE_MODEL_BASE_URL", "https://models.example/img"):
        body = (await client.get("/api/auth/me", headers=headers)).json()
    assert body["image_model_base_url"] == "https://models.example/img"
    assert body["image_model_configured"] is True
