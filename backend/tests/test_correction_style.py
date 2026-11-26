"""Tests for the per-user tutor correction style."""

import pytest

from app.services.prompts.tutor import build_conversation_system_prompt, build_tutor_system_prompt

RECAST_CHAT = "preferably through a natural reformulation. When explicit feedback is needed"
RECAST_VOICE = "preferably through a natural\n  reformulation. If explicit feedback is needed"
EXPLICIT = "name the error and give the corrected form in one short sentence"

CHAT_ARGS = {
    "student_name": "Alice",
    "cefr_level": "B1",
    "native_language": "Spanish",
    "target_language_name": "German",
    "total_xp": 0,
    "streak": 0,
    "lessons_today": 0,
    "skills": "None yet",
    "user_context": "",
    "memory_context": "",
}
VOICE_ARGS = {
    "student_name": "Alice",
    "cefr_level": "B1",
    "native_language": "Spanish",
    "target_language_name": "German",
    "user_context": "",
    "memory_context": "",
}


def test_explicit_style_prompt() -> None:
    prompt = build_tutor_system_prompt(**CHAT_ARGS, correction_style="explicit")

    assert EXPLICIT in prompt
    assert RECAST_CHAT not in prompt
    assert "detailed correction" in prompt
    assert "log_mistake" in prompt


def test_recast_style_prompt() -> None:
    prompt = build_tutor_system_prompt(**CHAT_ARGS, correction_style="recast")

    assert RECAST_CHAT in prompt
    assert EXPLICIT not in prompt
    assert "explicitly asks for detailed correction" in prompt
    assert "log_mistake" in prompt


def test_voice_prompt_uses_style() -> None:
    explicit = build_conversation_system_prompt(**VOICE_ARGS, correction_style="explicit")
    recast = build_conversation_system_prompt(**VOICE_ARGS, correction_style="recast")

    assert EXPLICIT in explicit
    assert RECAST_VOICE not in explicit
    assert RECAST_VOICE in recast
    assert EXPLICIT not in recast
    assert "log_mistake" in explicit
    assert "log_mistake" in recast


def test_default_style_keeps_prompt() -> None:
    assert build_tutor_system_prompt(**CHAT_ARGS) == build_tutor_system_prompt(
        **CHAT_ARGS, correction_style="recast"
    )
    assert build_conversation_system_prompt(**VOICE_ARGS) == build_conversation_system_prompt(
        **VOICE_ARGS, correction_style="recast"
    )


@pytest.mark.asyncio
async def test_settings_update_correction_style(client, test_user) -> None:
    _, headers = test_user

    response = await client.patch(
        "/api/auth/me", headers=headers, json={"correction_style": "explicit"}
    )
    assert response.status_code == 200
    assert response.json()["correction_style"] == "explicit"

    reread = await client.get("/api/auth/me", headers=headers)
    assert reread.json()["correction_style"] == "explicit"

    invalid = await client.patch("/api/auth/me", headers=headers, json={"correction_style": "loud"})
    assert invalid.status_code == 422
