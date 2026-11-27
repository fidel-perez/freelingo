"""Tutor buttons: translate a line, flag a mistake, and a typed turn."""

from __future__ import annotations

from unittest.mock import AsyncMock

from sqlalchemy import select

from app.core.config import settings
from app.models.flashcard import Flashcard
from app.models.mistake import Mistake
from app.services import tutor_tools
from app.services.tutor_tools import MistakeCorrection
from tests.conftest import make_study_plan
from tests.test_conversation_pipeline_service import FakeWS, _make_pipeline


async def _plan(db_session, user_id, **kwargs):
    plan = await make_study_plan(db_session, user_id=user_id, cefr_level="A2", **kwargs)
    await db_session.commit()
    return plan


async def test_translate_asks_the_llm_for_the_native_language(
    client, db_session, test_user, monkeypatch
) -> None:
    user, headers = test_user
    plan = await _plan(db_session, user.id, target_language="fr-FR")
    chat = AsyncMock(return_value=" Hola, ¿qué tal? ")
    monkeypatch.setattr(tutor_tools.llm_adapter, "chat", chat)

    response = await client.post(
        "/api/tutor/translate",
        headers=headers,
        json={"text": "Salut, ça va ?", "study_plan_id": plan.id},
    )

    assert response.status_code == 200
    assert response.json() == {"translation": "Hola, ¿qué tal?"}
    system, user_message = chat.call_args.args[0]
    assert "French" in system["content"]
    assert user_message["content"] == "Salut, ça va ?"


async def test_translate_requires_an_owned_plan(
    client, db_session, test_user, admin_user, monkeypatch
) -> None:
    _, headers = test_user
    foreign = await _plan(db_session, admin_user[0].id, target_language="fr-FR")
    monkeypatch.setattr(tutor_tools.llm_adapter, "chat", AsyncMock(return_value="x"))

    denied = await client.post(
        "/api/tutor/translate", headers=headers, json={"text": "a", "study_plan_id": foreign.id}
    )
    anonymous = await client.post(
        "/api/tutor/translate", json={"text": "a", "study_plan_id": foreign.id}
    )

    assert denied.status_code == 404
    assert anonymous.status_code == 401


async def test_translate_uses_the_fast_model_when_one_is_set(monkeypatch) -> None:
    monkeypatch.setattr(settings, "FAST_LLM_BASE_URL", "http://fast.test/v1")
    monkeypatch.setattr(settings, "FAST_LLM_MODEL", "small")
    monkeypatch.setattr(tutor_tools, "_fast_llm", None)

    adapter = tutor_tools.tutor_llm()

    assert adapter is not tutor_tools.llm_adapter
    assert tutor_tools.tutor_llm() is adapter


async def test_flag_mistake_logs_a_flashcard(client, db_session, test_user, monkeypatch) -> None:
    user, headers = test_user
    plan = await _plan(db_session, user.id, target_language="fr-FR")
    fix = MistakeCorrection(corrected="Je suis allé", note="Auxiliaire être")
    monkeypatch.setattr(tutor_tools.llm_adapter, "structured_output", AsyncMock(return_value=fix))

    response = await client.post(
        "/api/tutor/flag-mistake",
        headers=headers,
        json={"text": "J'ai allé", "study_plan_id": plan.id},
    )

    assert response.json() == {"logged": True, "corrected": "Je suis allé"}
    mistake = (await db_session.execute(select(Mistake))).scalar_one()
    assert (mistake.original, mistake.corrected, mistake.source) == (
        "J'ai allé",
        "Je suis allé",
        "voice",
    )
    card = await db_session.get(Flashcard, mistake.flashcard_id)
    assert card is not None and card.source == "mistake"


async def test_flag_mistake_skips_a_line_that_is_already_right(
    client, db_session, test_user, monkeypatch
) -> None:
    user, headers = test_user
    plan = await _plan(db_session, user.id, target_language="fr-FR")
    fix = MistakeCorrection(corrected="Je suis allé")
    monkeypatch.setattr(tutor_tools.llm_adapter, "structured_output", AsyncMock(return_value=fix))

    response = await client.post(
        "/api/tutor/flag-mistake",
        headers=headers,
        json={"text": "Je suis allé", "study_plan_id": plan.id},
    )

    assert response.json() == {"logged": False, "corrected": ""}
    assert (await db_session.execute(select(Mistake))).first() is None


async def test_typed_turn_skips_stt() -> None:
    pipeline = _make_pipeline(native_language="es", target_language="fr-FR")
    pipeline.stt.transcribe = AsyncMock()
    pipeline.tts.synthesize = AsyncMock(return_value=b"audio")

    async def reply():
        yield "Très bien."

    pipeline.llm.chat = AsyncMock(return_value=reply())
    ws = FakeWS()
    try:
        await pipeline._process(b"", ws, typed_text="Bonjour")
    finally:
        await pipeline.cleanup()

    pipeline.stt.transcribe.assert_not_awaited()
    assert pipeline.llm.chat.call_args.args[0][-1] == {"role": "user", "content": "Bonjour"}


def test_strip_reasoning_removes_think_blocks_and_stray_tags() -> None:
    strip = tutor_tools.strip_reasoning
    assert strip("<think>pensando</think>Hola") == "Hola"
    assert strip("Hola</think>") == "Hola"
    assert strip("</think>\nHola") == "Hola"
    assert strip("<think>Hola") == "Hola"


async def test_translate_and_correct_strip_reasoning_tags(monkeypatch) -> None:
    monkeypatch.setattr(
        tutor_tools.llm_adapter, "chat", AsyncMock(return_value="Hola, ¿qué tal?</think>")
    )
    assert (
        await tutor_tools.translate_line("Salut", target_language="fr-FR", native_language="es-ES")
        == "Hola, ¿qué tal?"
    )
    structured = AsyncMock(
        return_value=MistakeCorrection(corrected="Je suis</think>", note="<think>x</think>Ok")
    )
    monkeypatch.setattr(tutor_tools.llm_adapter, "structured_output", structured)
    result = await tutor_tools.correct_line(
        "Je es", target_language="fr-FR", native_language="es-ES"
    )
    assert result == MistakeCorrection(corrected="Je suis", note="Ok")
