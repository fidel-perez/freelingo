"""Voice latency: TTS starts per finished sentence while the LLM streams."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI

from app import main as main_module
from app.core.config import settings
from app.services import stt_service
from app.services.conversation_pipeline import (
    STREAM_TERMINALS,
    ConversationPipeline,
    SentenceSplitter,
    sentence_terminals,
)
from app.services.stt_service import OpenAICompatibleSTTService
from tests.test_conversation_pipeline_service import FakeWS, _make_chunk, _make_pipeline


def _pipeline() -> ConversationPipeline:
    pipeline = _make_pipeline()
    pipeline.stt.transcribe = AsyncMock(return_value="hello")
    return pipeline


async def test_tts_starts_before_llm_stream_ends() -> None:
    pipeline = _pipeline()
    events: list[str] = []
    tts_started = asyncio.Event()

    async def synth(text, voice, lang):
        events.append(f"tts:{text}")
        tts_started.set()
        return b"audio"

    pipeline.tts.synthesize = synth

    async def stream():
        yield _make_chunk("First sentence. ")
        await asyncio.wait_for(tts_started.wait(), timeout=2)
        events.append("llm:end")
        yield _make_chunk("Second sentence.")

    pipeline.llm.chat = AsyncMock(return_value=stream())

    await pipeline._process(b"audio", FakeWS())

    assert events == ["tts:First sentence.", "llm:end", "tts:Second sentence."]


async def test_barge_in_cancels_queued_tts() -> None:
    pipeline = _pipeline()
    started: list[str] = []
    cancelled: list[str] = []
    stream_closed = asyncio.Event()

    async def synth(text, voice, lang):
        started.append(text)
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(text)
            raise

    pipeline.tts.synthesize = synth

    async def stream():
        try:
            yield _make_chunk("One. Two. Three. ")
            await asyncio.sleep(30)
        finally:
            stream_closed.set()

    pipeline.llm.chat = AsyncMock(return_value=stream())
    ws = FakeWS()
    task = asyncio.create_task(pipeline._process(b"audio", ws))
    pipeline.current_task = task
    for _ in range(100):
        if len(started) == 3:
            break
        await asyncio.sleep(0.01)
    assert started == ["One.", "Two.", "Three."]

    await pipeline.cancel_current()

    assert sorted(cancelled) == ["One.", "Three.", "Two."]
    assert stream_closed.is_set()
    assert [kind for kind, _ in ws.sent].count("bytes") == 0


async def test_audio_keeps_sentence_order() -> None:
    pipeline = _pipeline()

    async def synth(text, voice, lang):
        # The first sentence finishes last, so unordered sending would swap them.
        await asyncio.sleep({"Slow one.": 0.15, "Quick two.": 0.0, "Quick three.": 0.0}[text])
        return text.encode()

    pipeline.tts.synthesize = synth

    async def stream():
        yield _make_chunk("Slow one. ")
        yield _make_chunk("Quick two. ")
        yield _make_chunk("Quick three.")

    pipeline.llm.chat = AsyncMock(return_value=stream())
    ws = FakeWS()

    await pipeline._process(b"audio", ws)

    assert [data for kind, data in ws.sent if kind == "bytes"] == [
        b"Slow one.",
        b"Quick two.",
        b"Quick three.",
    ]
    assert ws.types().count("transcript") == 2  # user text, then one assistant transcript


def test_sentence_split_without_full_stop() -> None:
    ja = sentence_terminals("ja-JP")
    assert SentenceSplitter(STREAM_TERMINALS + ja).feed("おはよう。元気ですか？はい！") == [
        "おはよう。",
        "元気ですか？",
    ]
    zh = SentenceSplitter(STREAM_TERMINALS + sentence_terminals("zh-CN"))
    assert [*zh.feed("你好吗？我很好！再见"), *zh.flush()] == ["你好吗？", "我很好！", "再见"]
    assert sentence_terminals("en-GB") == ""

    streamed = SentenceSplitter(STREAM_TERMINALS)
    assert streamed.feed("Really") == []
    assert streamed.feed("?") == []  # may still grow into "?!"
    assert streamed.feed(" Yes! Costs 3") == ["Really?", "Yes!"]
    assert streamed.feed(".5 pounds. ") == ["Costs 3.5 pounds."]
    assert streamed.flush() == []
    assert ConversationPipeline._split_tts_sentences("它好。再见。", ja) == ["它好。", "再见。"]


@pytest.fixture
def startup_stubs(monkeypatch):
    monkeypatch.setattr(main_module, "_run_migrations", Mock())
    monkeypatch.setattr(main_module, "os", SimpleNamespace(makedirs=Mock()))
    monkeypatch.setattr(settings, "SECRET_KEY", "test-startup-secret-at-least-32-characters")
    monkeypatch.setattr(settings, "REVENUECAT_ENABLED", False)
    monkeypatch.setattr(settings, "TTS_PROVIDER", "local")


async def test_new_stt_provider_selected_by_env(startup_stubs, monkeypatch) -> None:
    monkeypatch.setattr(settings, "STT_PROVIDER", "openai-compatible")
    monkeypatch.setattr(settings, "STT_BASE_URL", "http://speaches:18810/")
    application = FastAPI()

    async with main_module.lifespan(application):
        service = application.state.stt_service

    assert isinstance(service, OpenAICompatibleSTTService)
    assert service.base_url == "http://speaches:18810"
    assert service.model == "deepdml/faster-whisper-large-v3-turbo-ct2"

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": " 안녕하세요 "})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        stt_service.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )

    text = await service.transcribe(b"wav", "a.wav", "audio/wav", language="ko")

    assert text == "안녕하세요"
    assert str(seen[0].url) == "http://speaches:18810/v1/audio/transcriptions"
    body = seen[0].content
    assert b'name="model"' in body
    assert b"deepdml/faster-whisper-large-v3-turbo-ct2" in body
    assert b'name="language"' in body
