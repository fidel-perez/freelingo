"""Picture talk: scenes, native voice, hint ladder, scoring and settings."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.data.picture_talk_levels import (
    ANSWERED_INSTRUCTION,
    LEVELS,
    NARRATE_GUIDANCE,
    NATIVE_LINE_MARKER,
    OPEN_GUIDANCE,
    RUNG_INSTRUCTIONS,
    RUNG_MODEL_ANSWER,
)
from app.models.chat_history import ChatHistory
from app.models.conversation import Conversation
from app.models.flashcard import Flashcard
from app.models.mistake import Mistake
from app.models.picture_talk import PictureTalkScene
from app.routers import picture_talk as picture_talk_router
from app.services import picture_talk
from app.services.conversation_pipeline import ConversationPipeline
from app.services.native_tts import native_line_enabled, resolve_native_voice
from app.services.picture_talk import PictureTalkState, SceneDraft
from app.services.prompts.tutor import build_conversation_system_prompt
from app.services.tts_service import KokoroTTSService
from tests.conftest import make_study_plan
from tests.test_conversation_pipeline_service import FakeWS, _make_chunk

MARKER = NATIVE_LINE_MARKER.strip()


def _stream(*parts: str):
    async def gen():
        for part in parts:
            yield _make_chunk(part)

    return gen()


def _chat(*replies: str) -> AsyncMock:
    """An LLM chat mock that answers each call with the next reply."""
    queue = list(replies)
    return AsyncMock(side_effect=lambda *a, **k: _stream(queue.pop(0) if queue else "Ok."))


def _pipeline(level: str = "A2", *, native_lines: bool = True, native: bool = True, **kwargs):
    state = PictureTalkState(
        scene_id=1,
        words=["chat"],
        image_prompt="A cat on a sofa",
        level=level,
        native_lines=native_lines,
    )
    native_tts = AsyncMock()
    native_tts.synthesize = AsyncMock(return_value=b"NATIVE-WAV")
    tts = AsyncMock()
    tts.synthesize = AsyncMock(return_value=b"STUDY-MP3")
    pipeline = ConversationPipeline(
        llm=AsyncMock(),
        tts=tts,
        stt=AsyncMock(),
        cefr_level=level,
        native_language="es",
        target_language="fr-FR",
        picture=state,
        native_tts=native_tts if native else None,
        native_voice="voice-x" if native else "",
        **kwargs,
    )
    return pipeline, state, native_tts


def _system(chat: AsyncMock, call: int = -1) -> str:
    return chat.call_args_list[call].args[0][0]["content"]


async def _finish(pipeline: ConversationPipeline) -> None:
    if pipeline.current_task is not None:
        await pipeline.current_task
    await asyncio.gather(*pipeline._pending_saves, return_exceptions=True)


def _patch_db(db_session):
    @asynccontextmanager
    async def context():
        yield db_session

    return patch("app.services.conversation_pipeline.db_session", context)


@pytest.fixture
async def plan(db_session, test_user):
    user, _ = test_user
    plan = await make_study_plan(
        db_session, user_id=user.id, target_language="fr-FR", cefr_level="B2"
    )
    await db_session.commit()
    return plan


def _card(plan, word: str, *, due: bool, source: str | None = None, definition: str = "def"):
    return Flashcard(
        user_id=plan.user_id,
        study_plan_id=plan.id,
        word=word,
        definition=definition,
        example_sentence="example",
        translation="translation",
        source=source,
        next_review=date.today() - timedelta(days=1) if due else date.today() + timedelta(days=9),
    )


def _draft_mock(monkeypatch) -> AsyncMock:
    mock = AsyncMock(return_value=SceneDraft(image_prompt="A cat on a sofa", words=["le chat"]))
    monkeypatch.setattr(picture_talk.llm_adapter, "structured_output", mock)
    return mock


# ── Scenes ─────────────────────────────────────────────────────────────────────


async def test_scene_requires_owned_plan(client, db_session, test_user, admin_user, monkeypatch):
    _draft_mock(monkeypatch)
    _, headers = test_user
    foreign = await make_study_plan(
        db_session, user_id=admin_user[0].id, target_language="fr-FR", cefr_level="A1"
    )
    await db_session.commit()

    denied = await client.post(
        "/api/picture-talk/scenes", headers=headers, json={"study_plan_id": foreign.id}
    )
    anonymous = await client.post("/api/picture-talk/scenes", json={"study_plan_id": foreign.id})

    assert denied.status_code == 404
    assert anonymous.status_code == 401
    assert (await db_session.execute(select(PictureTalkScene))).first() is None


async def test_scene_prefers_due_flashcard_words(client, db_session, test_user, plan, monkeypatch):
    mock = _draft_mock(monkeypatch)
    _, headers = test_user
    db_session.add_all(
        [
            _card(plan, "later-1", due=False),
            _card(plan, "later-2", due=False),
            _card(plan, "later-3", due=False),
            _card(plan, "due-word", due=True),
            _card(plan, "front-native", due=True, source="mistake", definition="due-mistake"),
        ]
    )
    await db_session.commit()

    response = await client.post(
        "/api/picture-talk/scenes", headers=headers, json={"study_plan_id": plan.id}
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "study_plan_id", "image_prompt", "words"}
    assert body["words"] == ["le chat"]
    prompt = mock.call_args.args[0][0]["content"]
    assert "due-word" in prompt and "due-mistake" in prompt
    assert "front-native" not in prompt
    assert sum(f"later-{n}" in prompt for n in (1, 2, 3)) == 3 - 1 + 0 or "later-1" in prompt
    assert prompt.index("due-mistake") < prompt.index("due-word")
    scene = await db_session.get(PictureTalkScene, body["id"])
    assert scene.study_plan_id == plan.id and scene.words == ["le chat"]


async def test_scene_uses_plan_level(client, test_user, plan, monkeypatch):
    mock = _draft_mock(monkeypatch)
    _, headers = test_user

    response = await client.post(
        "/api/picture-talk/scenes", headers=headers, json={"study_plan_id": plan.id}
    )

    assert response.status_code == 200
    assert "CEFR level B2" in mock.call_args.args[0][0]["content"]
    assert mock.call_args.args[1] is SceneDraft


async def test_next_scene_keeps_conversation(db_session, test_user, plan):
    user, _ = test_user
    first = PictureTalkScene(study_plan_id=plan.id, image_prompt="first", words=["alpha"])
    second = PictureTalkScene(study_plan_id=plan.id, image_prompt="second", words=["bravo"])
    other = await make_study_plan(
        db_session, user_id=user.id, target_language="de-DE", cefr_level="A1"
    )
    foreign = PictureTalkScene(study_plan_id=other.id, image_prompt="other", words=["x"])
    conversation = Conversation(
        user_id=user.id, title="t", source="voice", study_plan_id=plan.id, target_language="fr-FR"
    )
    db_session.add_all([first, second, foreign, conversation])
    await db_session.commit()
    pipeline, state, _ = _pipeline(
        user_id=user.id, study_plan_id=plan.id, conversation_id=conversation.id
    )
    state.scene_id = first.id
    pipeline.llm.chat = _chat("Un chat.", "Un autre chat.")
    ws = FakeWS()

    with _patch_db(db_session):
        await pipeline.handle_next_scene(ws, second.id)
        await _finish(pipeline)
        await pipeline.handle_next_scene(ws, foreign.id)
        await pipeline.handle_next_scene(ws, 999999)

    assert pipeline._conversation_id == conversation.id
    assert state.scene_id == second.id and state.words == ["bravo"]
    assert "bravo" in pipeline.system_prompt and "alpha" not in pipeline.system_prompt
    events = ws.json_messages()
    assert {"type": "scene", "scene_id": second.id} in events
    assert [e["code"] for e in events if e["type"] == "error"] == ["picture_unavailable"] * 2
    rows = (
        (
            await db_session.execute(
                select(ChatHistory).where(ChatHistory.conversation_id == conversation.id)
            )
        )
        .scalars()
        .all()
    )
    assert [row.content for row in rows] == ["Un chat."]


# ── Native voice ───────────────────────────────────────────────────────────────


async def test_native_line_uses_native_voice() -> None:
    pipeline, _, native_tts = _pipeline()
    pipeline.llm.chat = _chat(f"Le chat dort.\n{MARKER} El gato duerme.\n")
    ws = FakeWS()

    await pipeline._picture_tutor_turn(ws, "[start]")

    frames = [
        (kind, data)
        for kind, data in ws.sent
        if kind == "bytes" or data.get("type") == "audio_voice"
    ]
    assert frames == [
        ("json", {"type": "audio_voice", "voice": "study"}),
        ("bytes", b"STUDY-MP3"),
        ("json", {"type": "audio_voice", "voice": "native"}),
        ("bytes", b"NATIVE-WAV"),
    ]
    native_tts.synthesize.assert_awaited_once_with("El gato duerme.", "voice-x", "es")
    pipeline.tts.synthesize.assert_awaited_once_with("Le chat dort.", None, "fr-FR")
    transcript = [m for m in ws.json_messages() if m.get("role") == "assistant"][0]
    assert transcript["text"] == "Le chat dort.\nEl gato duerme."
    assert transcript["native_lines"] == ["El gato duerme."]


def test_native_voice_selected_by_env(monkeypatch) -> None:
    monkeypatch.setattr(settings, "NATIVE_TTS_PROVIDER", "local")
    monkeypatch.setattr(settings, "NATIVE_TTS_BASE_URL", "http://native-voice:1234")
    monkeypatch.setattr(settings, "NATIVE_TTS_VOICES", "es=voice-es, fr = voice-fr")

    service, voice = resolve_native_voice("es", "ko-KR")
    other, other_voice = resolve_native_voice("fr", "ko-KR")

    assert isinstance(service, KokoroTTSService)
    assert service.base_url == "http://native-voice:1234"
    assert (voice, other_voice) == ("voice-es", "voice-fr")
    assert other is not service


async def test_native_line_skipped_without_native_voice(monkeypatch) -> None:
    monkeypatch.setattr(settings, "NATIVE_TTS_BASE_URL", "http://native-voice:1234")
    monkeypatch.setattr(settings, "NATIVE_TTS_VOICES", "es=voice-es")
    monkeypatch.setattr(settings, "NATIVE_TTS_PROVIDER", "")
    assert resolve_native_voice("es", "ko-KR") is None  # provider empty
    monkeypatch.setattr(settings, "NATIVE_TTS_PROVIDER", "local")
    assert resolve_native_voice("de", "ko-KR") is None  # language missing from the map
    assert resolve_native_voice("es", "es-ES") is None  # native equals study language
    assert not native_line_enabled("es", "es-ES")
    assert resolve_native_voice("es", "ko-KR") is not None

    pipeline, _, native_tts = _pipeline(native=False)
    pipeline.llm.chat = _chat(f"Le chat dort.\n{MARKER} El gato duerme.\n")
    ws = FakeWS()
    await pipeline._picture_tutor_turn(ws, "[start]")

    native_tts.synthesize.assert_not_awaited()
    voices = [m["voice"] for m in ws.json_messages() if m["type"] == "audio_voice"]
    assert voices == ["study"]
    context = picture_talk.PICTURE_CONTEXT_PREFIX + "{}" + picture_talk.PICTURE_CONTEXT_SUFFIX
    prompt = build_conversation_system_prompt(
        student_name="Ana",
        cefr_level="A2",
        native_language="Spanish",
        target_language_name="French",
        user_context="",
        memory_context="",
        lesson_practice_context=context,
        picture_native_lines=False,
    )
    assert "Write no native-language line" in prompt


async def test_button_cancels_native_audio() -> None:
    pipeline, _, native_tts = _pipeline()
    started: list[str] = []
    cancelled: list[str] = []

    async def synth_native(text, voice, language):
        started.append(text)
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(text)
            raise

    native_tts.synthesize = synth_native
    release = asyncio.Event()
    calls = 0

    async def stream():
        yield _make_chunk(f"Le chat dort.\n{MARKER} El gato duerme.\n{MARKER} Y ronca.\n")
        await release.wait()

    def chat(*args, **kwargs):
        nonlocal calls
        calls += 1
        return stream() if calls == 1 else _stream("Ok.")

    pipeline.llm.chat = AsyncMock(side_effect=chat)
    ws = FakeWS()
    pipeline.current_task = asyncio.create_task(pipeline._picture_tutor_turn(ws, "[start]"))
    for _ in range(200):
        if len(started) == 2:
            break
        await asyncio.sleep(0.01)
    assert started == ["El gato duerme.", "Y ronca."]

    await pipeline.handle_button_event(ws, "took_too_long")

    assert sorted(cancelled) == ["El gato duerme.", "Y ronca."]
    assert any(m["type"] == "barge_in" for m in ws.json_messages())
    assert not [d for kind, d in ws.sent if kind == "bytes" and d == b"NATIVE-WAV"]
    await _finish(pipeline)


async def test_picture_talk_ignores_audio() -> None:
    pipeline, _, _ = _pipeline()
    pipeline.llm.chat = _chat("Oui.")

    await pipeline.handle_audio(b"audio", FakeWS())

    assert pipeline.current_task is None
    pipeline.llm.chat.assert_not_awaited()
    pipeline.stt.transcribe.assert_not_awaited()


async def test_picture_talk_uses_fast_llm(monkeypatch) -> None:
    monkeypatch.setattr(settings, "FAST_LLM_BASE_URL", "http://fast:8081/v1")
    monkeypatch.setattr(settings, "FAST_LLM_MODEL", "fast-model")
    adapter = picture_talk.scene_llm()
    assert str(adapter.client.base_url).startswith("http://fast:8081/v1")
    assert adapter.model == "fast-model"
    monkeypatch.setattr(settings, "FAST_LLM_BASE_URL", "")
    assert picture_talk.scene_llm() is picture_talk.llm_adapter


# ── Settings and prompt ────────────────────────────────────────────────────────


async def test_settings_update_native_speech_rate(client, test_user) -> None:
    _, headers = test_user

    default = await client.get("/api/auth/me", headers=headers)
    updated = await client.patch("/api/auth/me", headers=headers, json={"native_speech_rate": 2.2})
    reread = await client.get("/api/auth/me", headers=headers)

    assert default.json()["native_speech_rate"] == 1.8
    assert updated.status_code == 200 and updated.json()["native_speech_rate"] == 2.2
    assert reread.json()["native_speech_rate"] == 2.2
    for bad in (0.9, 2.6, "fast"):
        response = await client.patch(
            "/api/auth/me", headers=headers, json={"native_speech_rate": bad}
        )
        assert response.status_code == 422


def _prompt(level: str) -> str:
    context = picture_talk.PICTURE_CONTEXT_PREFIX + "{}" + picture_talk.PICTURE_CONTEXT_SUFFIX
    return build_conversation_system_prompt(
        student_name="Ana",
        cefr_level=level,
        native_language="Spanish",
        target_language_name="French",
        user_context="",
        memory_context="",
        lesson_practice_context=context,
        picture_native_lines=True,
    )


def test_lowest_level_answer_sits_in_question() -> None:
    lowest, higher = _prompt("A1"), _prompt("B2")

    assert NARRATE_GUIDANCE in lowest and "answer sits inside the question" in lowest
    assert NARRATE_GUIDANCE not in higher and OPEN_GUIDANCE in higher
    assert f'marker "{MARKER}"' in lowest


# ── Hint ladder ────────────────────────────────────────────────────────────────


async def test_took_too_long_button_steps_hint_ladder() -> None:
    pipeline, state, _ = _pipeline("A1")
    pipeline.llm.chat = _chat("Q1.", "Q2.", "Q3.")
    ws = FakeWS()

    for _ in range(3):
        await pipeline.handle_button_event(ws, "took_too_long")
        await _finish(pipeline)

    rungs = LEVELS["A1"].rungs
    for call, rung in enumerate(rungs[:3]):
        assert RUNG_INSTRUCTIONS[rung] in _system(pipeline.llm.chat, call)
    assert state.rung == 3 and state.open_question
    pipeline.stt.transcribe.assert_not_awaited()


async def test_answer_button_speaks_model_answer(db_session, test_user, plan) -> None:
    user, _ = test_user
    pipeline, state, _ = _pipeline("A1", user_id=user.id, study_plan_id=plan.id)
    state.open_question = True
    state.rung = 2
    model = f"Le chat dort.\n{MARKER} El gato duerme.\nEt le chien?\n{MARKER} Y el perro?\n"
    pipeline.llm.chat = _chat(model)
    ws = FakeWS()

    with _patch_db(db_session):
        await pipeline.handle_button_event(ws, "answered")
        await _finish(pipeline)

    system = _system(pipeline.llm.chat)
    assert ANSWERED_INSTRUCTION in system and "ask one new, easy question" in system
    assert "Hint ladder" not in system
    assert state.rung == 0 and not state.answered
    voices = [m["voice"] for m in ws.json_messages() if m["type"] == "audio_voice"]
    assert voices == ["study", "native", "study", "native"]
    assert (await db_session.execute(select(Mistake))).scalars().all() == []


async def test_ladder_ends_in_model_answer_with_native_line() -> None:
    pipeline, state, _ = _pipeline("A1")
    state.open_question = True
    model = f"Le chat dort.\n{MARKER} El gato duerme.\nEt le chien?\n{MARKER} Y el perro?\n"
    pipeline.llm.chat = _chat("Q1.", "Q2.", "Q3.", model, "Q1 again.")
    ws = FakeWS()

    for _ in range(4):
        await pipeline.handle_button_event(ws, "took_too_long")
        await _finish(pipeline)

    assert RUNG_INSTRUCTIONS[RUNG_MODEL_ANSWER] in _system(pipeline.llm.chat, 3)
    assert "its meaning on one marked native-language line" in _system(pipeline.llm.chat, 3)
    voices = [m["voice"] for m in ws.json_messages() if m["type"] == "audio_voice"]
    assert voices[-4:] == ["study", "native", "study", "native"]
    assert state.rung == 0  # the model answer ended the ladder
    await pipeline.handle_button_event(ws, "took_too_long")  # the next question starts again
    await _finish(pipeline)
    assert RUNG_INSTRUCTIONS[LEVELS["A1"].rungs[0]] in _system(pipeline.llm.chat, 4)


def test_ladder_shortens_with_level() -> None:
    order = ["A1", "A2", "B1", "B2", "C1", "C2"]
    lengths = [len(LEVELS[level].rungs) for level in order]

    assert lengths == sorted(lengths, reverse=True) and lengths[0] > lengths[-1]
    assert all(LEVELS[level].rungs[-1] == RUNG_MODEL_ANSWER for level in order)


async def test_unproduced_line_logged_as_mistake(db_session, test_user, plan) -> None:
    user, _ = test_user
    pipeline, state, _ = _pipeline("A1", user_id=user.id, study_plan_id=plan.id)
    state.open_question = True
    state.rung = len(LEVELS["A1"].rungs) - 1
    pipeline.llm.chat = _chat(f"Le chat dort.\n{MARKER} El gato duerme.\n")

    with _patch_db(db_session):
        await pipeline.handle_button_event(FakeWS(), "took_too_long")
        await _finish(pipeline)

    mistakes = (await db_session.execute(select(Mistake))).scalars().all()
    assert len(mistakes) == 1
    assert (mistakes[0].original, mistakes[0].corrected, mistakes[0].source) == (
        "El gato duerme.",
        "Le chat dort.",
        "voice",
    )
    card = await db_session.get(Flashcard, mistakes[0].flashcard_id)
    assert (card.word, card.definition, card.study_plan_id) == (
        "El gato duerme.",
        "Le chat dort.",
        plan.id,
    )


async def test_same_language_asks_for_no_native_line(db_session, test_user, plan) -> None:
    user, _ = test_user
    pipeline, state, _ = _pipeline(
        "A1", native_lines=False, native=False, user_id=user.id, study_plan_id=plan.id
    )
    state.open_question = True
    state.rung = len(LEVELS["A1"].rungs) - 1
    pipeline.llm.chat = _chat("Le chat dort.\n")

    with _patch_db(db_session):
        await pipeline.handle_button_event(FakeWS(), "took_too_long")
        await _finish(pipeline)

    assert "native-language" not in state.turn_context()
    state.answer()
    assert "native-language" not in state.turn_context()
    mistakes = (await db_session.execute(select(Mistake))).scalars().all()
    assert [(m.original, m.corrected, m.source) for m in mistakes] == [
        ("Le chat dort.", "Le chat dort.", "voice")
    ]


# ── Server image ───────────────────────────────────────────────────────────────

_IMAGE_CLIENT = "app.routers.picture_talk.httpx.AsyncClient"
_SERVER = "app.routers.picture_talk.settings.IMAGE_SERVER_BASE_URL"


def _image_post(mock: AsyncMock):
    """An httpx.AsyncClient stand-in whose post is `mock`; the test client stays real."""
    client = MagicMock()
    client.__aenter__.return_value = MagicMock(post=mock)
    return patch(_IMAGE_CLIENT, return_value=client)


async def _scene(db_session, plan, prompt="A cat"):
    scene = PictureTalkScene(study_plan_id=plan.id, image_prompt=prompt, words=["chat"])
    db_session.add(scene)
    await db_session.commit()
    return scene


async def test_server_image_refuses_foreign_and_unknown_scene(
    client, db_session, test_user, admin_user, plan
):
    user, headers = test_user
    user.images_enabled = True
    await db_session.commit()
    other = await make_study_plan(
        db_session, user_id=admin_user[0].id, target_language="fr-FR", cefr_level="A1"
    )
    foreign = await _scene(db_session, other, "secret")
    post = AsyncMock()
    with patch(_SERVER, "http://host:18850"), _image_post(post):
        for scene_id in (foreign.id, 999999):
            response = await client.post(
                "/api/picture-talk/image", headers=headers, json={"scene_id": scene_id}
            )
            assert response.status_code == 404
        arbitrary = await client.post(
            "/api/picture-talk/image", headers=headers, json={"prompt": "anything"}
        )
    assert arbitrary.status_code == 422
    post.assert_not_awaited()


def _png_response(status: int = 200) -> httpx.Response:
    request = httpx.Request("POST", "http://host/generate")
    return httpx.Response(status, content=b"\x89PNGdata", request=request)


async def test_server_image_proxies_png(client, db_session, test_user, plan):
    user, headers = test_user
    scene = await _scene(db_session, plan)
    user.images_enabled = True
    await db_session.commit()

    with (
        patch(_SERVER, "http://host:18850/"),
        _image_post(AsyncMock(return_value=_png_response())) as http_cls,
    ):
        response = await client.post(
            "/api/picture-talk/image", headers=headers, json={"scene_id": scene.id}
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"\x89PNGdata"
    post = http_cls.return_value.__aenter__.return_value.post
    assert post.call_args.args[0] == "http://host:18850/generate"
    assert post.call_args.kwargs["json"] == {
        "prompt": "A cat",
        "width": 512,
        "height": 512,
        "wait": False,
    }
    assert post.call_args.kwargs["timeout"] == 10.0


async def test_server_image_pending_is_202(client, db_session, test_user, plan):
    user, headers = test_user
    scene = await _scene(db_session, plan)
    user.images_enabled = True
    await db_session.commit()
    pending = httpx.Response(
        202, json={"status": "pending"}, request=httpx.Request("POST", "http://host/generate")
    )

    with patch(_SERVER, "http://host:18850"), _image_post(AsyncMock(return_value=pending)):
        response = await client.post(
            "/api/picture-talk/image", headers=headers, json={"scene_id": scene.id}
        )

    assert response.status_code == 202
    assert response.json() == {"status": "pending"}


async def test_new_scene_prefetches_image(client, db_session, test_user, plan, monkeypatch):
    _draft_mock(monkeypatch)
    user, headers = test_user
    user.images_enabled = True
    await db_session.commit()

    with (
        patch(_SERVER, "http://host:18850"),
        _image_post(AsyncMock(return_value=_png_response())) as http_cls,
    ):
        response = await client.post(
            "/api/picture-talk/scenes", headers=headers, json={"study_plan_id": plan.id}
        )
        await asyncio.gather(*picture_talk_router._prefetches)

    assert response.status_code == 200
    post = http_cls.return_value.__aenter__.return_value.post
    assert post.call_args.kwargs["json"]["prompt"] == "A cat on a sofa"
    assert post.call_args.kwargs["json"]["wait"] is False


async def test_new_scene_survives_prefetch_failure(
    client, db_session, test_user, plan, monkeypatch
):
    _draft_mock(monkeypatch)
    user, headers = test_user
    user.images_enabled = True
    await db_session.commit()

    with (
        patch(_SERVER, "http://host:18850"),
        _image_post(AsyncMock(side_effect=httpx.ConnectError("down"))),
    ):
        response = await client.post(
            "/api/picture-talk/scenes", headers=headers, json={"study_plan_id": plan.id}
        )
        await asyncio.gather(*picture_talk_router._prefetches)

    assert response.status_code == 200


async def test_scene_prompt_demands_study_language_words(plan):
    prompt = picture_talk.build_scene_prompt(plan, [])
    language = picture_talk.get_language_name(plan.target_language)
    assert f"Write every word in {language}" in prompt
    assert f"common everyday {language} words" in prompt


async def test_server_image_off_makes_no_call(client, db_session, test_user, plan):
    user, headers = test_user
    scene = await _scene(db_session, plan)
    body = {"scene_id": scene.id}
    user.images_enabled = True
    await db_session.commit()

    post = AsyncMock()
    with _image_post(post):
        with patch(_SERVER, ""):
            off = await client.post("/api/picture-talk/image", headers=headers, json=body)
        user.images_enabled = False
        await db_session.commit()
        with patch(_SERVER, "http://host:18850"):
            disabled = await client.post("/api/picture-talk/image", headers=headers, json=body)
        anonymous = await client.post("/api/picture-talk/image", json=body)

    assert (off.status_code, disabled.status_code, anonymous.status_code) == (404, 404, 401)
    post.assert_not_awaited()


async def test_server_image_failure_is_503(client, db_session, test_user, plan):
    user, headers = test_user
    scene = await _scene(db_session, plan)
    user.images_enabled = True
    await db_session.commit()

    failures = (
        AsyncMock(return_value=_png_response(500)),
        AsyncMock(side_effect=httpx.ConnectError("down")),
    )
    with patch(_SERVER, "http://host:18850"):
        for mock in failures:
            with _image_post(mock):
                response = await client.post(
                    "/api/picture-talk/image", headers=headers, json={"scene_id": scene.id}
                )
            assert response.status_code == 503
