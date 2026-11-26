from __future__ import annotations

import asyncio
import importlib
import json
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core.app_logger import get_logger
from app.data.picture_talk_levels import (
    NATIVE_LINE_MARKER,
    NEXT_SCENE_TRIGGER,
    NO_ANSWER_TRIGGER,
    RUNG_MODEL_ANSWER,
)
from app.models.user import User
from app.services.language_helpers import (
    get_iso639,
    get_language_name,
    get_native_language_name,
)
from app.services.learning_analytics import record_lingu_practice
from app.services.llm_adapter import (
    LLMError,
    LLMStream,
    LLMStreamReset,
    LLMTimeoutError,
    LLMToolResultEvent,
    LLMUnavailableError,
)
from app.services.memory_service import (
    build_memory_context,
    build_save_user_memory_tool,
    execute_save_user_memory,
    get_user_memories,
)
from app.services.mistake_service import (
    LOG_MISTAKE_TOOL_NAME,
    build_log_mistake_tool,
    execute_log_mistake,
    log_mistakes,
)
from app.services.picture_talk import (
    PictureTalkState,
    classify_reply,
    display_text,
    load_picture_scene,
    native_line_text,
    score_repeat,
    split_picture_text,
    weak_words_of_answer,
)
from app.services.prompts.common import get_language_prompt_overlay
from app.services.prompts.tutor import CONVERSATION_START_PROMPT, build_conversation_system_prompt
from app.services.quota_service import record_session_seconds
from app.utils.db import db_session

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = get_logger(__name__)

TTS_MAX_RETRIES = 1
TTS_RETRY_DELAY_SECONDS = 0.2

CONVERSATION_HISTORY_MESSAGE_LIMIT = 40

BUTTON_EVENT_CONTEXT = {
    "answered": (
        "Practice event: the learner said their answer aloud and pressed the button "
        "'I answered'. You did not hear it. Do not ask them to repeat it. React briefly, "
        "encourage them and move the practice on."
    ),
    "took_too_long": (
        "Practice event: the learner pressed the button 'I took too long' because they "
        "could not answer in time. Do not judge. Say the model answer simply and invite "
        "them to try the next one."
    ),
}

WARNING_ADVANCE_SECONDS = 60  # How many seconds before timeout to send the warning


def _build_conversation_system_prompt(
    *,
    student_name: str,
    cefr_level: str,
    native_language: str,
    target_language_name: str,
    user_context: str,
    memory_context: str,
    language_prompt_overlay: str = "",
    memory_tools_enabled: bool = True,
    lesson_practice_context: str = "",
    correction_style: str = "recast",
    picture_native_lines: bool = False,
) -> str:
    return build_conversation_system_prompt(
        student_name=student_name,
        cefr_level=cefr_level,
        native_language=native_language,
        target_language_name=target_language_name,
        user_context=user_context,
        memory_context=memory_context,
        language_prompt_overlay=language_prompt_overlay,
        memory_tools_enabled=memory_tools_enabled,
        lesson_practice_context=lesson_practice_context,
        correction_style=correction_style,
        picture_native_lines=picture_native_lines,
    )


TTS_MAX_PARALLEL = 3
STREAM_TERMINALS = ".!?"
NATIVE_MARKER_CORE = NATIVE_LINE_MARKER.strip()
_CLOSERS = "\"'”’)）」』】"


def sentence_terminals(target_language: str) -> str:
    """Return the extra sentence-final marks of a study language, or an empty string."""
    try:
        module = importlib.import_module(
            f"app.data.{get_iso639(target_language)}.sentence_terminals"
        )
    except ImportError:
        return ""
    return str(module.SENTENCE_TERMINALS)


class SentenceSplitter:
    """Cut streamed text into sentences as soon as each one is finished."""

    def __init__(self, terminals: str = ".") -> None:
        self._terminals = set(terminals)
        self._buffer = ""

    def feed(self, text: str) -> list[str]:
        self._buffer += text
        return self._drain(final=False)

    def flush(self) -> list[str]:
        return self._drain(final=True)

    def reset(self) -> None:
        self._buffer = ""

    def _drain(self, *, final: bool) -> list[str]:
        buf = self._buffer
        chunks: list[str] = []
        start = 0
        idx = 0
        while idx < len(buf):
            if buf[idx] not in self._terminals:
                idx += 1
                continue
            # A mark at the end of the buffer may still become "..." or "3.5".
            if idx + 1 >= len(buf) and not final:
                break
            if buf[idx] == ".":
                prev = buf[idx - 1] if idx > 0 else ""
                nxt = buf[idx + 1] if idx + 1 < len(buf) else ""
                if prev == "." or nxt == "." or (prev.isdecimal() and nxt.isdecimal()):
                    idx += 1
                    continue
            end = idx + 1
            while end < len(buf) and (buf[end] in self._terminals or buf[end] in _CLOSERS):
                end += 1
            if end >= len(buf) and not final:
                break
            chunks.append(buf[start:end])
            start = idx = end
        if final:
            chunks.append(buf[start:])
            start = len(buf)
        self._buffer = buf[start:]
        return [c.strip() for c in chunks if any(ch.isalnum() for ch in c)]


class _SpeechStreamer:
    """Synthesize each finished sentence while the LLM streams; send audio in order."""

    def __init__(self, pipeline: ConversationPipeline, ws: WebSocket, turn_id: int) -> None:
        self._pipeline = pipeline
        self._ws = ws
        self._turn_id = turn_id
        self._splitter = SentenceSplitter(
            STREAM_TERMINALS + sentence_terminals(pipeline._target_language)
        )
        self._queue: asyncio.Queue[tuple[int, asyncio.Task, bool] | None] = asyncio.Queue()
        self._picture = pipeline._picture is not None
        self._line = ""
        self._mode: str | None = None
        self._fed = 0
        self.native_lines: list[str] = []
        self._pending: list[asyncio.Task] = []
        self._generation = 0
        self._slots = asyncio.Semaphore(TTS_MAX_PARALLEL)
        self._sender = asyncio.create_task(self._send_in_order())
        self._final_text: str | None = None
        self.transcript_sent = False
        self.aborted = False
        self.chunks_sent = 0
        self.audio_bytes = 0
        self.first_enqueue_t0: float | None = None

    def push(self, token: str) -> None:
        if not self._picture:
            for sentence in self._splitter.feed(token):
                self._enqueue(sentence)
            return
        while token:
            head, newline, token = token.partition("\n")
            self._line += head
            self._advance_line()
            if newline:
                self._end_line()

    def _advance_line(self) -> None:
        """Decide whether the current line is a native line, then stream study sentences."""
        if self._mode is None:
            stripped = self._line.lstrip()
            if stripped.startswith(NATIVE_MARKER_CORE):
                self._mode = "native"
            elif NATIVE_MARKER_CORE.startswith(stripped):
                return
            else:
                self._mode = "study"
        if self._mode == "study":
            fresh, self._fed = self._line[self._fed :], len(self._line)
            for sentence in self._splitter.feed(fresh):
                self._enqueue(sentence)

    def _end_line(self) -> None:
        line, mode = self._line, self._mode
        self._line, self._mode, self._fed = "", None, 0
        if mode == "native":
            text = native_line_text(line)
            if any(ch.isalnum() for ch in text):
                self.native_lines.append(text)
                if self._pipeline._native_voice_ready:
                    self._enqueue(text, native=True)
        elif mode == "study":
            for sentence in self._splitter.flush():
                self._enqueue(sentence)
        elif line.strip():
            for sentence in [*self._splitter.feed(line), *self._splitter.flush()]:
                self._enqueue(sentence)

    def reset(self) -> None:
        """Drop the text and the unsent audio of a stream the LLM restarted."""
        self._splitter.reset()
        self._line, self._mode, self._fed = "", None, 0
        self.native_lines.clear()
        self._generation += 1
        for task in self._pending:
            task.cancel()
        self._pending.clear()

    def _enqueue(self, sentence: str, native: bool = False) -> None:
        if self.aborted:
            return
        if self.first_enqueue_t0 is None:
            self.first_enqueue_t0 = time.perf_counter()
        task = asyncio.create_task(self._synthesize(sentence, native))
        self._pending.append(task)
        self._queue.put_nowait((self._generation, task, native))

    async def _synthesize(self, sentence: str, native: bool = False) -> bytes:
        async with self._slots:
            if native:
                return await self._pipeline._synthesize_chunk(sentence, native=True)
            return await self._pipeline._synthesize_chunk(sentence)

    async def _send_in_order(self) -> None:
        while (item := await self._queue.get()) is not None:
            generation, task, native = item
            await asyncio.wait({task})
            if self.aborted or generation != self._generation or task.cancelled():
                continue
            exc = task.exception()
            if exc is not None:
                logger.warning(
                    "[pipeline] TTS sentence failed after retry: turn_id=%s error=%s",
                    self._turn_id,
                    self._pipeline._fmt_exc(exc),
                )
                continue
            audio = task.result()
            if not await self._pipeline._safe_send_audio(
                self._ws, audio, "native" if native else "study"
            ):
                logger.warning(
                    "[pipeline] Audio send failed and remaining chunks are being aborted: "
                    "turn_id=%s",
                    self._turn_id,
                )
                self.aborted = True
                for pending in self._pending:
                    pending.cancel()
                continue
            self.chunks_sent += 1
            self.audio_bytes += len(audio)
            await self._send_transcript_if_ready()

    async def _send_transcript_if_ready(self) -> None:
        if self.transcript_sent or self._final_text is None or self.aborted:
            return
        self.transcript_sent = True
        payload: dict = {
            "type": "transcript",
            "role": "assistant",
            "text": self._final_text,
            "final": True,
            "turn_id": self._turn_id,
        }
        if self._picture:
            payload["text"] = display_text(self._final_text)
            payload["native_lines"] = self.native_lines
        await self._pipeline._send_json(self._ws, payload)

    async def finish(self, final_text: str) -> None:
        """Flush the last sentence, wait for all audio, then publish the transcript."""
        if self._picture:
            self._end_line()
        for sentence in self._splitter.flush():
            self._enqueue(sentence)
        self._final_text = final_text
        if self.chunks_sent:
            await self._send_transcript_if_ready()
        self._queue.put_nowait(None)
        await self._sender
        if self.chunks_sent == 0 and not self.aborted:
            logger.warning(
                "[pipeline] No TTS audio produced; publishing text-only transcript: turn_id=%s",
                self._turn_id,
            )
        await self._send_transcript_if_ready()

    async def cancel(self) -> None:
        """Barge-in or failure: stop the sender and every queued TTS fragment."""
        tasks = [self._sender, *self._pending]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class ConversationPipeline:
    """
    Orchestrates STT → LLM → TTS streaming with barge-in and session timeout.
    """

    def __init__(
        self,
        llm: object,
        tts: object,
        stt: object,
        cefr_level: str = "B1",
        native_language: str = "es",
        target_language: str = "en-GB",
        student_name: str = "Student",
        max_duration: int = 1800,
        inactivity_timeout: int = 180,
        initial_context: list[dict] | None = None,
        user_id: int | None = None,
        conversation_id: int | None = None,
        bio: str | None = None,
        learning_goals: str | None = None,
        memories: list | None = None,
        voice: str = "",
        study_plan_id: int | None = None,
        lesson_practice_context: str = "",
        user_agent: str = "",
        correction_style: str = "recast",
        picture: PictureTalkState | None = None,
        native_tts: object | None = None,
        native_voice: str = "",
    ) -> None:
        self.llm = llm
        self.tts = tts
        self.stt = stt
        self._voice = voice
        self._picture = picture
        self._native_tts = native_tts
        self._native_voice = native_voice
        self._native_voice_ready = bool(
            picture is not None and picture.native_lines and native_tts is not None and native_voice
        )
        self._native_language = native_language
        self._stt_language = get_iso639(target_language)
        self._target_language = target_language
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._study_plan_id = study_plan_id
        self._user_agent = user_agent
        self._transcript_lock = asyncio.Lock()
        # Build user context section
        _ctx_parts: list[str] = []
        if learning_goals:
            try:
                import json as _json  # noqa: PLC0415

                goals = _json.loads(learning_goals)
                if isinstance(goals, list) and goals:
                    _ctx_parts.append(f"Learning goals: {', '.join(goals)}")
            except ValueError, TypeError:
                pass
        if bio and bio.strip():
            _ctx_parts.append(f"About the student: {bio.strip()}")
        user_context = (
            ("\nStudent context:\n" + "\n".join(f"- {p}" for p in _ctx_parts) + "\n")
            if _ctx_parts
            else ""
        )
        self._memory_context = build_memory_context(memories or [])
        target_language_name = get_language_name(target_language)
        language_prompt_overlay = get_language_prompt_overlay(target_language)
        self._prompt_args = {
            "student_name": student_name,
            "cefr_level": cefr_level,
            "native_language": get_native_language_name(native_language),
            "target_language_name": target_language_name,
            "user_context": user_context,
            "language_prompt_overlay": language_prompt_overlay,
            "lesson_practice_context": lesson_practice_context,
            "correction_style": correction_style,
            "picture_native_lines": bool(picture is not None and picture.native_lines),
        }
        self.system_prompt = _build_conversation_system_prompt(
            **self._prompt_args,
            memory_context=self._memory_context,
        )
        self.max_duration = max_duration
        self.inactivity_timeout = inactivity_timeout
        self._redis: object | None = None  # injected after construction
        self._recorded = False
        self._freemium_voice = False
        self._memory_tools_available = True

        self.current_task: asyncio.Task | None = None
        # Pre-populate history from optional chat context
        if initial_context:
            self.history: list[dict] = [
                {"role": m["role"], "content": m["content"]}
                for m in initial_context
                if isinstance(m, dict)
                and m.get("role") in ("user", "assistant")
                and isinstance(m.get("content"), str)
                and m["content"].strip()
            ][-10:]
        else:
            self.history = []
        self._session_start = time.monotonic()
        self._last_activity = time.monotonic()
        self._timer_tasks: list[asyncio.Task] = []
        self._pending_saves: list[asyncio.Task] = []
        self._inactivity_warning_sent = False
        self._send_lock = asyncio.Lock()
        self._turn_id = 0
        self._client_close_reason: str | None = None

    @staticmethod
    def _fmt_exc(exc: Exception) -> str:
        return f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _task_state(task: asyncio.Task | None) -> str:
        if task is None:
            return "none"
        if task.cancelled():
            return "cancelled"
        if task.done():
            if task.exception() is not None:
                return "done:error"
            return "done"
        return "running"

    async def _send_json(self, ws: WebSocket, data: dict) -> None:
        async with self._send_lock:
            await ws.send_json(data)

    def _next_turn_id(self) -> int:
        self._turn_id += 1
        return self._turn_id

    async def _send_status(self, ws: WebSocket, turn_id: int, value: str) -> None:
        await self._send_json(ws, {"type": "status", "value": value, "turn_id": turn_id})

    async def _send_memory_updated(self, ws: WebSocket, turn_id: int) -> None:
        send_task = asyncio.create_task(
            self._send_json(ws, {"type": "memory_updated", "turn_id": turn_id})
        )
        try:
            await asyncio.shield(send_task)
        except asyncio.CancelledError:
            try:
                await send_task
            except Exception as exc:
                logger.debug(
                    "[pipeline] Memory update notification failed during cancellation: %s",
                    self._fmt_exc(exc),
                )
            raise

    @staticmethod
    def _extract_speech_text(raw_text: str) -> str:
        return ConversationPipeline._clean_sentence(raw_text).strip()

    @staticmethod
    def _stream_text(chunk: object) -> str:
        if isinstance(chunk, str):
            return chunk
        choices = getattr(chunk, "choices", None)
        if not choices:
            return ""
        return getattr(getattr(choices[0], "delta", None), "content", None) or ""

    async def _refresh_memory_prompt(self) -> None:
        self.system_prompt = _build_conversation_system_prompt(
            **self._prompt_args,
            memory_context=self._memory_context,
            memory_tools_enabled=self._memory_tools_available,
        )
        if self._user_id is None:
            return
        try:
            async with db_session() as db:
                memories = await get_user_memories(db, self._user_id)
                user = await db.get(User, self._user_id)
                if user is not None:
                    self._prompt_args["native_language"] = get_native_language_name(
                        user.native_language
                    )
                    self._prompt_args["correction_style"] = user.correction_style
            self._memory_context = build_memory_context(memories)
            self.system_prompt = _build_conversation_system_prompt(
                **self._prompt_args,
                memory_context=self._memory_context,
                memory_tools_enabled=self._memory_tools_available,
            )
        except Exception:
            logger.exception("[pipeline] Failed to refresh global memory context")

    async def _safe_send_bytes(self, ws: WebSocket, data: bytes) -> bool:
        try:
            async with self._send_lock:
                await ws.send_bytes(data)
            return True
        except RuntimeError as exc:
            logger.debug("[pipeline] Socket closed while sending audio: %s", exc)
            return False
        except Exception as exc:
            logger.debug("[pipeline] Audio send failed: %s", self._fmt_exc(exc))
            return False

    async def _close_ws(self, ws: WebSocket, code: int = 1000) -> None:
        logger.debug("[pipeline] Closing websocket with code=%s", code)
        async with self._send_lock:
            await ws.close(code=code)

    async def _safe_send_audio(self, ws: WebSocket, data: bytes, voice: str) -> bool:
        """Send one audio frame; picture talk tells the client first which voice speaks it."""
        if self._picture is None:
            return await self._safe_send_bytes(ws, data)
        try:
            async with self._send_lock:
                await ws.send_json({"type": "audio_voice", "voice": voice})
                await ws.send_bytes(data)
            return True
        except Exception as exc:
            logger.debug("[pipeline] Audio send failed: %s", self._fmt_exc(exc))
            return False

    async def _synthesize_chunk(self, text: str, *, native: bool = False) -> bytes:
        attempts = TTS_MAX_RETRIES + 1
        trace_id = uuid.uuid4().hex[:10]
        logger.info(
            "[pipeline] TTS request scheduling — trace=%s len=%d attempts=%d",
            trace_id,
            len(text),
            attempts,
        )
        for attempt in range(1, attempts + 1):
            try:
                logger.debug(
                    "[pipeline] TTS request start: len=%d chars, attempt=%d/%d",
                    len(text),
                    attempt,
                    attempts,
                )
                if native:
                    audio = await self._native_tts.synthesize(  # type: ignore[union-attr]
                        text, self._native_voice, self._native_language
                    )
                else:
                    audio = await self.tts.synthesize(
                        text, self._voice or None, self._target_language
                    )
                if not audio:
                    raise RuntimeError("TTS returned empty audio payload")
                return audio
            except TimeoutError:
                if attempt >= attempts:
                    logger.warning(
                        "[pipeline] TTS request timed out (%d/%d attempts)",
                        attempt,
                        attempts,
                    )
                    raise
                logger.warning(
                    "[pipeline] TTS request timed out (%d/%d); retrying",
                    attempt,
                    attempts,
                )
                await asyncio.sleep(TTS_RETRY_DELAY_SECONDS * attempt)
            except asyncio.CancelledError:
                logger.warning(
                    "[pipeline] TTS request cancelled: trace=%s len=%d attempt=%d",
                    trace_id,
                    len(text),
                    attempt,
                )
                raise
            except GeneratorExit:
                logger.warning(
                    "[pipeline] TTS request interrupted by GeneratorExit: trace=%s len=%d attempt=%d",
                    trace_id,
                    len(text),
                    attempt,
                )
                raise
            except Exception as exc:
                if attempt >= attempts:
                    raise
                logger.warning(
                    "[pipeline] TTS request failed (%d/%d): %s",
                    attempt,
                    attempts,
                    exc,
                )
                await asyncio.sleep(TTS_RETRY_DELAY_SECONDS * attempt)

    @staticmethod
    def _split_tts_sentences(text: str, extra_terminals: str = "") -> list[str]:
        """Split finished text on sentence-final marks, keeping decimals and ellipses whole."""
        splitter = SentenceSplitter("." + extra_terminals)
        return [*splitter.feed(text), *splitter.flush()]

    async def _synthesize_and_send_response(
        self,
        ws: WebSocket,
        *,
        text: str,
        turn_id: int,
        transcript_payload: dict,
    ) -> tuple[int, int, bool, bool]:
        """Synthesize ordered sentence chunks and send the transcript after first audio."""
        chunks_sent = 0
        audio_bytes_sent = 0
        transcript_sent = False
        send_aborted = False

        for idx, sentence in enumerate(
            self._split_tts_sentences(text, sentence_terminals(self._target_language)), start=1
        ):
            try:
                audio = await self._synthesize_chunk(sentence)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "[pipeline] TTS sentence failed after retry: turn_id=%s sentence=%s error=%s",
                    turn_id,
                    idx,
                    self._fmt_exc(exc),
                )
                continue

            send_ok = await self._safe_send_bytes(ws, audio)
            if not send_ok:
                logger.warning(
                    "[pipeline] Audio send failed and remaining chunks are being aborted: turn_id=%s",
                    turn_id,
                )
                send_aborted = True
                break

            chunks_sent += 1
            audio_bytes_sent += len(audio)

            if not transcript_sent:
                await self._send_json(ws, transcript_payload)
                transcript_sent = True

        if chunks_sent == 0 and not transcript_sent and not send_aborted:
            logger.warning(
                "[pipeline] No TTS audio produced; publishing text-only transcript: turn_id=%s",
                turn_id,
            )
            await self._send_json(ws, transcript_payload)
            transcript_sent = True

        return chunks_sent, audio_bytes_sent, transcript_sent, send_aborted

    @staticmethod
    def _clean_sentence(raw_sentence: str) -> str:
        """Normalize visible assistant text before synthesis."""
        return raw_sentence.strip()

    def _picture_system_prompt(self, extra: str = "") -> str:
        return (
            _build_conversation_system_prompt(
                **self._prompt_args,
                memory_context=self._memory_context,
                memory_tools_enabled=False,
            )
            + extra
        )

    async def _scene_event(self, ws: WebSocket) -> None:
        picture = self._picture
        if picture is not None:
            await self._send_json(
                ws,
                {
                    "type": "scene",
                    "scene_id": picture.scene_id,
                    "silence_wait_s": picture.silence_wait_s,
                },
            )

    async def _picture_tutor_turn(self, ws: WebSocket, trigger: str) -> None:
        """A tutor turn with no learner audio: the opening, a new scene or a hint rung."""
        picture = self._picture
        if picture is None:
            return
        turn_id = self._next_turn_id()
        messages = [
            {
                "role": "system",
                "content": self._picture_system_prompt(picture.turn_context(weak_words=[])),
            },
            *self.history[-CONVERSATION_HISTORY_MESSAGE_LIMIT:],
            {"role": "user", "content": trigger},
        ]
        streamer: _SpeechStreamer | None = None
        try:
            await self._send_status(ws, turn_id, "thinking")
            llm_stream = await self.llm.chat(messages, stream=True)
            streamer = _SpeechStreamer(self, ws, turn_id)
            full_response = ""
            async for chunk in llm_stream:
                if isinstance(chunk, LLMToolResultEvent):
                    continue
                if isinstance(chunk, LLMStreamReset):
                    full_response = ""
                    streamer.reset()
                    continue
                token = self._stream_text(chunk)
                full_response += token
                streamer.push(token)
            clean = self._extract_speech_text(full_response)
            if not clean:
                return
            await streamer.finish(clean)
            if streamer.aborted or not streamer.transcript_sent:
                return
            self.history.append({"role": "assistant", "content": clean})
            self._pending_saves.append(
                asyncio.create_task(self._save_message("assistant", display_text(clean)))
            )
            self._picture_after_turn(clean)
            await self._send_status(ws, turn_id, "listening")
            await self._send_json(ws, {"type": "turn_complete", "turn_id": turn_id})
        except asyncio.CancelledError:
            logger.warning("[pipeline] Picture turn cancelled for turn_id=%s", turn_id)
            raise
        except Exception as exc:
            logger.error("[pipeline] Picture turn failed: %s", exc)
        finally:
            if streamer is not None:
                await streamer.cancel()
            self._reset_inactivity()

    async def _picture_prepare(
        self, ws: WebSocket, turn_id: int, audio_bytes: bytes, user_text: str
    ) -> str:
        """Step the ladder or score the learner's audio; return the tutor's turn context."""
        picture = self._picture
        if picture is None:
            return ""
        weak_words: list[str] = []
        repeat: tuple[str, float] | None = None
        if picture.model_answer:
            answer = picture.model_answer
            picture.model_answer = None
            picture.model_meaning = None
            score = await score_repeat(
                audio_bytes,
                user_text,
                model_answer=answer,
                target_language=self._target_language,
                native_language=self._native_language,
                cefr_level=picture.level,
            )
            repeat = (answer, score)
            picture.reset_ladder()
            await self._send_json(
                ws,
                {
                    "type": "repeat_score",
                    "score": score,
                    "model_answer": answer,
                    "turn_id": turn_id,
                },
            )
        else:
            kind = "answer"
            if picture.open_question:
                kind = await classify_reply(
                    self.llm,
                    text=user_text,
                    native_language=self._native_language,
                    target_language=self._target_language,
                )
            if kind == "answer":
                picture.reset_ladder()
                weak_words = await weak_words_of_answer(
                    audio_bytes, user_text, target_language=self._target_language
                )
            else:
                picture.step()
        return picture.turn_context(weak_words=weak_words, repeat=repeat)

    def _picture_after_turn(self, reply: str) -> None:
        """Mark the question open and keep the model answer a ladder turn just gave."""
        picture = self._picture
        if picture is None:
            return
        picture.open_question = True
        if picture.active_rung == RUNG_MODEL_ANSWER:
            study, native = split_picture_text(reply)
            if study:
                picture.model_answer = study[0]
                picture.model_meaning = native[0] if native else None
                if picture.model_meaning:
                    self._pending_saves.append(
                        asyncio.create_task(self._log_model_answer(study[0], native[0]))
                    )
        picture.active_rung = None
        picture.move_on = False

    async def _log_model_answer(self, model_answer: str, meaning: str) -> None:
        """One mistake per model answer: the native meaning on the front, the answer behind."""
        if self._study_plan_id is None:
            return
        try:
            async with db_session() as db:
                await log_mistakes(db, self._study_plan_id, [(meaning, model_answer, "")], "voice")
        except Exception:
            logger.exception("[pipeline] Failed to log the model answer as a mistake")

    async def handle_no_answer(self, ws: WebSocket) -> None:
        picture = self._picture
        if picture is None or not picture.open_question:
            return
        if self.current_task and not self.current_task.done():
            return
        self._reset_inactivity()
        picture.step()
        self.current_task = asyncio.create_task(self._picture_tutor_turn(ws, NO_ANSWER_TRIGGER))

    async def handle_next_scene(self, ws: WebSocket, scene_id: object) -> None:
        picture = self._picture
        loaded = None
        if picture is not None and self._user_id is not None and self._study_plan_id is not None:
            async with db_session() as db:
                loaded = await load_picture_scene(
                    db, self._user_id, scene_id, study_plan_id=self._study_plan_id
                )
        if picture is None or loaded is None:
            await self._send_json(ws, {"type": "error", "code": "picture_unavailable"})
            return
        await self.cancel_current()
        picture.load_scene(loaded.scene.id, list(loaded.scene.words), loaded.scene.image_prompt)
        self._prompt_args["lesson_practice_context"] = loaded.context
        self.system_prompt = _build_conversation_system_prompt(
            **self._prompt_args,
            memory_context=self._memory_context,
            memory_tools_enabled=self._memory_tools_available,
        )
        self._reset_inactivity()
        await self._scene_event(ws)
        self.current_task = asyncio.create_task(self._picture_tutor_turn(ws, NEXT_SCENE_TRIGGER))

    async def handle_button_event(self, ws: WebSocket, event: object) -> None:
        """A learner button press starts a tutor turn with no audio, STT or VAD."""
        if not isinstance(event, str) or event not in BUTTON_EVENT_CONTEXT:
            await self._send_json(ws, {"type": "error", "code": "button_event_unknown"})
            return
        self._reset_inactivity()
        if self.current_task and not self.current_task.done():
            await self.cancel_current()
            await self._send_json(ws, {"type": "barge_in"})
        self.current_task = asyncio.create_task(self._button_turn(ws, event))

    async def _button_turn(self, ws: WebSocket, event: str) -> None:
        """Answer a learner event: stream the LLM and speak each finished sentence."""
        turn_id = self._next_turn_id()
        messages = [
            {
                "role": "system",
                "content": self.system_prompt + "\n\n" + BUTTON_EVENT_CONTEXT[event],
            },
            *self.history[-CONVERSATION_HISTORY_MESSAGE_LIMIT:],
            {"role": "user", "content": f"[{event}]"},
        ]
        streamer: _SpeechStreamer | None = None
        try:
            await self._send_status(ws, turn_id, "thinking")
            llm_stream = await self.llm.chat(messages, stream=True)
            streamer = _SpeechStreamer(self, ws, turn_id)
            full_response = ""
            async for chunk in llm_stream:
                if isinstance(chunk, LLMToolResultEvent):
                    continue
                if isinstance(chunk, LLMStreamReset):
                    full_response = ""
                    streamer.reset()
                    continue
                token = self._stream_text(chunk)
                full_response += token
                streamer.push(token)
            clean = self._extract_speech_text(full_response)
            if not clean:
                await self._send_status(ws, turn_id, "listening")
                return
            await streamer.finish(clean)
            if streamer.aborted or not streamer.transcript_sent:
                return
            self.history.append({"role": "assistant", "content": clean})
            self._pending_saves.append(asyncio.create_task(self._save_message("assistant", clean)))
            await self._send_status(ws, turn_id, "listening")
            await self._send_json(ws, {"type": "turn_complete", "turn_id": turn_id})
        except asyncio.CancelledError:
            logger.warning("[pipeline] Button turn cancelled for turn_id=%s", turn_id)
            raise
        except Exception as exc:
            logger.error("[pipeline] Button turn failed: %s", exc)
            await self._send_json(
                ws,
                {"type": "error", "code": "llm_failed", "message": str(exc), "turn_id": turn_id},
            )
        finally:
            if streamer is not None:
                await streamer.cancel()
            self._reset_inactivity()

    async def _greet(self, ws: WebSocket) -> None:
        """Generate and stream an opening greeting from the assistant."""
        if self._picture is not None:
            await self._picture_tutor_turn(ws, CONVERSATION_START_PROMPT)
            self._reset_inactivity()
            return
        turn_id = self._next_turn_id()
        trigger = {
            "role": "user",
            "content": CONVERSATION_START_PROMPT,
        }
        greeting_prompt = _build_conversation_system_prompt(
            **self._prompt_args,
            memory_context=self._memory_context,
            memory_tools_enabled=False,
        )
        messages = (
            [{"role": "system", "content": greeting_prompt}]
            + self.history[-CONVERSATION_HISTORY_MESSAGE_LIMIT:]
            + [trigger]
        )

        try:
            full_response = ""
            llm_stream = await self.llm.chat(messages, stream=True)
            async for chunk in llm_stream:
                token = self._stream_text(chunk)
                full_response += token
            clean_full_response = self._extract_speech_text(full_response)
            if not clean_full_response:
                return
            await self._send_status(ws, turn_id, "thinking")

            (
                chunks_sent,
                _audio_bytes,
                transcript_sent,
                send_aborted,
            ) = await self._synthesize_and_send_response(
                ws,
                text=clean_full_response,
                turn_id=turn_id,
                transcript_payload={
                    "type": "transcript",
                    "role": "assistant",
                    "text": clean_full_response,
                    "final": True,
                    "turn_id": turn_id,
                },
            )
            if send_aborted or not transcript_sent:
                return

            self.history.append({"role": "assistant", "content": clean_full_response})
            self._pending_saves.append(
                asyncio.create_task(self._save_message("assistant", clean_full_response))
            )
            await self._send_status(ws, turn_id, "listening")
            await self._send_json(ws, {"type": "turn_complete", "turn_id": turn_id})
        except asyncio.CancelledError:
            logger.warning(
                "[pipeline] Greeting cancelled for turn_id=%s",
                turn_id,
            )
            raise
        except Exception as exc:
            logger.error("[pipeline] Greeting failed: %s", exc)
        finally:
            self._reset_inactivity()

    async def run(self, ws: WebSocket) -> None:
        """Main loop: starts timeout watchers then handles incoming messages."""
        logger.info("[pipeline] Conversation loop starting")
        self._timer_tasks = [
            asyncio.create_task(self._max_duration_watcher(ws)),
            asyncio.create_task(self._inactivity_watcher(ws)),
        ]
        await self._scene_event(ws)
        self.current_task = asyncio.create_task(self._greet(ws))
        try:
            while True:
                try:
                    data = await ws.receive()
                except RuntimeError:
                    # Client disconnected — ws.receive() raises RuntimeError after disconnect
                    logger.info("[pipeline] ws.receive raised RuntimeError — disconnect")
                    break
                except Exception as exc:
                    logger.error("[pipeline] ws.receive failed: %s", self._fmt_exc(exc))
                    break
                if data.get("type") == "websocket.disconnect":
                    logger.info("[pipeline] websocket.disconnect event received")
                    break
                if "bytes" in data:
                    await self.handle_audio(data["bytes"], ws)
                elif "text" in data:
                    msg = json.loads(data["text"])
                    if msg.get("type") == "client_event":
                        if msg.get("event") == "session_close_request":
                            self._client_close_reason = msg.get("reason", "manual")
                            logger.info(
                                "[pipeline] Client requested session close: reason=%s",
                                self._client_close_reason,
                            )
                            break
                        logger.debug("[pipeline] Received unknown client_event: %s", msg)
                        continue
                    if msg.get("type") == "no_answer":
                        await self.handle_no_answer(ws)
                        continue
                    if msg.get("type") == "button_event":
                        await self.handle_button_event(ws, msg.get("event"))
                        continue
                    if msg.get("type") == "next_scene":
                        await self.handle_next_scene(ws, msg.get("scene_id"))
                        continue
                    if msg.get("type") == "interrupt":
                        await self.cancel_current()
                        await self._send_json(ws, {"type": "interrupted"})
        finally:
            logger.info(
                "[pipeline] Conversation loop ending: current_task=%s",
                self._task_state(self.current_task),
            )
            logger.info(
                "[pipeline] Session close reason=%s",
                self._client_close_reason or "peer_disconnect/unknown",
            )
            await self.cancel_current()
            for t in self._timer_tasks:
                t.cancel()

    def _reset_inactivity(self) -> None:
        self._last_activity = time.monotonic()
        self._inactivity_warning_sent = False

    async def handle_audio(self, audio_bytes: bytes, ws: WebSocket) -> None:
        self._reset_inactivity()
        logger.debug(
            "[pipeline] Audio chunk received (%d bytes); current_task=%s",
            len(audio_bytes),
            self._task_state(self.current_task),
        )
        # Barge-in: cancel ongoing response if a new audio chunk arrives
        if self.current_task and not self.current_task.done():
            logger.info(
                "[pipeline] Barge-in requested: canceling active turn before STT",
            )
            await self.cancel_current()
            logger.info("[pipeline] Barge-in: previous turn cancelled")
            await self._send_json(ws, {"type": "barge_in"})
        self.current_task = asyncio.create_task(self._process(audio_bytes, ws))

    async def _process(self, audio_bytes: bytes, ws: WebSocket) -> None:
        # Turns are serialized by handle_audio. Only this turn owns additions
        # beyond the snapshot; a cancelled greeting never owns initial context.
        history_start = len(self.history)
        try:
            await self._process_turn(audio_bytes, ws)
        finally:
            if len(self.history) > history_start and self.history[-1]["role"] == "user":
                self.history.pop()
            self._reset_inactivity()

    async def _process_turn(self, audio_bytes: bytes, ws: WebSocket) -> None:
        turn_t0 = time.perf_counter()
        turn_id = self._next_turn_id()
        stt_ms: float | None = None
        llm_ms: float | None = None
        tts_send_ms: float | None = None
        tts_bytes: int = 0
        tts_chunks_sent = 0
        assistant_transcript_sent = False
        send_aborted = False

        # 1. STT
        try:
            await self._send_status(ws, turn_id, "transcribing")
            stt_t0 = time.perf_counter()
            user_text = await self.stt.transcribe(
                audio_bytes,
                "audio.wav",
                "audio/wav",
                language=self._stt_language,
            )
            stt_ms = (time.perf_counter() - stt_t0) * 1000
            logger.info("[pipeline] STT result: %r", user_text)
        except Exception as exc:
            logger.error("[pipeline] STT failed: %s", exc)
            logger.info(
                "pipeline_turn_metrics",
                stage="stt_failed",
                stt_ms=round(stt_ms, 1) if stt_ms is not None else None,
                llm_ms=None,
                tts_send_ms=None,
                tts_chunks_sent=0,
                tts_audio_bytes=0,
                turn_total_ms=round((time.perf_counter() - turn_t0) * 1000, 1),
            )
            await self._send_json(
                ws,
                {
                    "type": "error",
                    "code": "stt_failed",
                    "message": str(exc),
                    "turn_id": turn_id,
                },
            )
            return

        user_text = user_text.strip()
        if not user_text:
            logger.info("[pipeline] Empty STT result — ignoring audio chunk")
            await self._send_status(ws, turn_id, "listening")
            return

        await self._send_json(
            ws,
            {
                "type": "transcript",
                "role": "user",
                "text": user_text,
                "final": True,
                "turn_id": turn_id,
            },
        )

        # 2. Streaming LLM
        self.history.append({"role": "user", "content": user_text})
        # NOTE: user message is intentionally saved *after* a successful turn
        # (alongside the assistant reply) so no orphan rows are written on
        # LLM failures or barge-in cancellations.
        await self._refresh_memory_prompt()
        turn_context = await self._picture_prepare(ws, turn_id, audio_bytes, user_text)
        system_prompt = self.system_prompt + turn_context
        messages = [{"role": "system", "content": system_prompt}] + self.history[
            -CONVERSATION_HISTORY_MESSAGE_LIMIT:
        ]

        full_response = ""
        clean_full_response = ""
        memory_updated = False
        llm_stream = None
        streamer: _SpeechStreamer | None = None
        try:
            await self._send_status(ws, turn_id, "thinking")

            llm_t0 = time.perf_counter()

            async def execute_memory_tool(call):
                if self._user_id is None:
                    raise RuntimeError("Memory tool requires an authenticated user")
                async with db_session() as db_mem:
                    if call.name == LOG_MISTAKE_TOOL_NAME:
                        return await execute_log_mistake(
                            db_mem, call, "voice", study_plan_id=self._study_plan_id
                        )
                    return await execute_save_user_memory(
                        db_mem,
                        self._user_id,
                        call,
                        "voice",
                        study_plan_id=self._study_plan_id,
                    )

            memory_kwargs = {}
            if self._memory_tools_available:
                fallback_messages = [
                    {
                        "role": "system",
                        "content": _build_conversation_system_prompt(
                            **self._prompt_args,
                            memory_context=self._memory_context,
                            memory_tools_enabled=False,
                        )
                        + turn_context,
                    }
                ] + self.history[-CONVERSATION_HISTORY_MESSAGE_LIMIT:]
                memory_kwargs = {
                    "tools": [
                        build_save_user_memory_tool(str(self._prompt_args["native_language"])),
                        build_log_mistake_tool(str(self._prompt_args["native_language"])),
                    ],
                    "tool_executor": execute_memory_tool,
                    "fallback_messages": fallback_messages,
                }
            llm_stream = await self.llm.chat(messages, stream=True, **memory_kwargs)
            streamer = _SpeechStreamer(self, ws, turn_id)
            try:
                async for chunk in llm_stream:
                    if isinstance(chunk, LLMToolResultEvent):
                        memory_updated = memory_updated or chunk.result.content.get("saved") is True
                        continue
                    if isinstance(chunk, LLMStreamReset):
                        full_response = ""
                        streamer.reset()
                        continue
                    token = self._stream_text(chunk)
                    full_response += token
                    streamer.push(token)
            finally:
                memory_updated = memory_updated or any(
                    result.content.get("saved") is True
                    for result in getattr(llm_stream, "tool_results", [])
                )
                if memory_updated:
                    await self._send_memory_updated(ws, turn_id)
            llm_ms = (time.perf_counter() - llm_t0) * 1000

            clean_full_response = self._extract_speech_text(full_response)
            if clean_full_response:
                await streamer.finish(clean_full_response)
                tts_chunks_sent = streamer.chunks_sent
                tts_bytes = streamer.audio_bytes
                assistant_transcript_sent = streamer.transcript_sent
                send_aborted = streamer.aborted
                if streamer.first_enqueue_t0 is not None:
                    tts_send_ms = (time.perf_counter() - streamer.first_enqueue_t0) * 1000
                if send_aborted or not assistant_transcript_sent:
                    logger.warning(
                        "[pipeline] No assistant audio was sent and turn is being aborted: turn_id=%s",
                        turn_id,
                    )
                    await self._send_json(
                        ws,
                        {
                            "type": "error",
                            "code": "tts_failed",
                            "message": "Failed to synthesize assistant audio.",
                            "turn_id": turn_id,
                        },
                    )
                    return
            else:
                logger.warning(
                    "[pipeline] Skipping TTS synthesis because assistant text is empty after cleaning"
                )
                await self._send_status(ws, turn_id, "listening")
                await self._send_json(ws, {"type": "turn_complete", "turn_id": turn_id})
                return

            completed_at = datetime.now(UTC).replace(tzinfo=None)

        except asyncio.CancelledError:
            logger.warning(
                "[pipeline] Turn cancelled before completion: turn_id=%s",
                turn_id,
            )
            raise
        except (LLMTimeoutError, LLMUnavailableError, LLMError) as exc:
            logger.error("[pipeline] LLM failed: %s", exc)
            logger.info(
                "pipeline_turn_metrics",
                stage="llm_failed",
                stt_ms=round(stt_ms, 1) if stt_ms is not None else None,
                llm_ms=round(llm_ms, 1) if llm_ms is not None else None,
                tts_send_ms=round(tts_send_ms, 1) if tts_send_ms is not None else None,
                tts_chunks_sent=tts_chunks_sent,
                tts_audio_bytes=tts_bytes,
                turn_total_ms=round((time.perf_counter() - turn_t0) * 1000, 1),
            )
            await self._send_json(
                ws,
                {
                    "type": "error",
                    "code": "llm_failed",
                    "message": str(exc),
                    "turn_id": turn_id,
                },
            )
            return
        except Exception as exc:
            logger.error("[pipeline] TTS failed: %s", exc)
            logger.info(
                "pipeline_turn_metrics",
                stage="tts_failed",
                stt_ms=round(stt_ms, 1) if stt_ms is not None else None,
                llm_ms=round(llm_ms, 1) if llm_ms is not None else None,
                tts_send_ms=round(tts_send_ms, 1) if tts_send_ms is not None else None,
                tts_chunks_sent=tts_chunks_sent,
                tts_audio_bytes=tts_bytes,
                turn_total_ms=round((time.perf_counter() - turn_t0) * 1000, 1),
            )
            await self._send_json(
                ws,
                {
                    "type": "error",
                    "code": "tts_failed",
                    "message": str(exc),
                    "turn_id": turn_id,
                },
            )
            return
        finally:
            if streamer is not None:
                await streamer.cancel()
            if llm_stream is not None:
                if getattr(llm_stream, "tools_unsupported", False):
                    self._memory_tools_available = False
                self._pending_saves.append(asyncio.create_task(self._save_usage(llm_stream)))

        self.history.append({"role": "assistant", "content": clean_full_response})
        self._picture_after_turn(clean_full_response)
        # Persist both sides of the turn together — only reached on success.
        saved_text = (
            display_text(clean_full_response) if self._picture is not None else clean_full_response
        )
        self._pending_saves.append(
            asyncio.create_task(self._save_turn(user_text, saved_text, completed_at=completed_at))
        )

        logger.info("[pipeline] Turn complete — assistant: %r", clean_full_response[:120])
        logger.info(
            "pipeline_turn_metrics",
            stage="ok",
            stt_ms=round(stt_ms, 1) if stt_ms is not None else None,
            llm_ms=round(llm_ms, 1) if llm_ms is not None else None,
            tts_send_ms=round(tts_send_ms, 1) if tts_send_ms is not None else None,
            tts_chunks_sent=tts_chunks_sent,
            tts_audio_bytes=tts_bytes,
            turn_total_ms=round((time.perf_counter() - turn_t0) * 1000, 1),
        )
        await self._send_status(ws, turn_id, "listening")

        await self._send_json(ws, {"type": "turn_complete", "turn_id": turn_id})

    async def _save_usage(self, stream: object) -> None:
        """Persists token usage from an LLMStream to the DB.

        Completely defensive — silently ignores any error, including:
        - stream not being an LLMStream instance
        - provider not returning usage (all fields None)
        - DB connectivity issues
        """
        if self._user_id is None:
            return
        try:
            if not isinstance(stream, LLMStream):
                return
            if stream.prompt_tokens is None and stream.completion_tokens is None:
                return
            # Lazy import to avoid circular imports
            from app.models.llm_usage import LLMUsage  # noqa: PLC0415

            async with db_session() as db:
                db.add(
                    LLMUsage(
                        user_id=self._user_id,
                        source="conversation",
                        prompt_tokens=stream.prompt_tokens,
                        completion_tokens=stream.completion_tokens,
                        total_tokens=stream.total_tokens,
                        study_plan_id=self._study_plan_id,
                    )
                )
                await db.commit()
        except Exception:
            logger.debug("[pipeline] Failed to save token usage — ignored")

    async def _save_turn(
        self, user_text: str, assistant_text: str, *, completed_at: datetime
    ) -> None:
        # Preserve transcript order even when background DB writes overlap turns.
        async with self._transcript_lock:
            response_id = await self._save_message(
                "assistant", assistant_text, user_text=user_text, completed_at=completed_at
            )
        if response_id is not None:
            await record_lingu_practice(response_id, user_agent=self._user_agent)

    async def _save_message(
        self,
        role: str,
        content: str,
        *,
        user_text: str | None = None,
        completed_at: datetime | None = None,
    ) -> int | None:
        """Persists a conversation transcript message to chat_history.

        Completely defensive — silently ignores any error.
        The save is sent to the background via asyncio.create_task so it never
        blocks the voice pipeline.
        """
        if self._user_id is None or self._conversation_id is None:
            return
        try:
            from sqlalchemy import update  # noqa: PLC0415

            from app.models.chat_history import ChatHistory  # noqa: PLC0415
            from app.models.conversation import Conversation  # noqa: PLC0415
            from app.services.progress_rewards import reward_conversation  # noqa: PLC0415

            completed_at = completed_at or datetime.now(UTC).replace(tzinfo=None)
            async with db_session() as db:
                reply_to_id = None
                if user_text is not None:
                    user_message = ChatHistory(
                        user_id=self._user_id,
                        conversation_id=self._conversation_id,
                        role="user",
                        content=user_text,
                        study_plan_id=self._study_plan_id,
                        target_language=self._target_language,
                        modality="voice",
                        created_at=completed_at,
                    )
                    db.add(user_message)
                    await db.flush()
                    reply_to_id = user_message.id
                message = ChatHistory(
                    user_id=self._user_id,
                    conversation_id=self._conversation_id,
                    role=role,
                    content=content,
                    study_plan_id=self._study_plan_id,
                    target_language=self._target_language,
                    modality="voice",
                    reply_to_id=reply_to_id,
                    created_at=completed_at,
                )
                db.add(message)
                await db.execute(
                    update(Conversation)
                    .where(Conversation.id == self._conversation_id)
                    .values(updated_at=completed_at)
                )
                if role == "assistant":
                    await db.flush()
                    await reward_conversation(db, message.id)
                await db.commit()
                response_id = message.id
            return response_id
        except Exception:
            logger.debug("[pipeline] Failed to save message — ignored")

    async def cancel_current(self) -> None:
        if self.current_task and not self.current_task.done():
            logger.debug(
                "[pipeline] cancel_current: cancelling task state=%s",
                self._task_state(self.current_task),
            )
            self.current_task.cancel()
            try:
                await self.current_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("[pipeline] cancel_current await failed: %s", self._fmt_exc(exc))

    async def cleanup(self) -> None:
        # Record actual session duration once, best-effort
        if not self._recorded:
            self._recorded = True
            elapsed = int(time.monotonic() - self._session_start)
            if self._redis is not None and self._user_id is not None and elapsed > 0:
                try:
                    await record_session_seconds(self._redis, self._user_id, elapsed)
                except Exception:
                    logger.debug("[pipeline] Failed to record session seconds — ignored")
                if self._freemium_voice:
                    try:
                        from app.services.freemium_service import record_voice_usage

                        await record_voice_usage(self._redis, self._user_id, elapsed)
                    except Exception:
                        logger.debug("[pipeline] Failed to record freemium voice seconds — ignored")
        await self.cancel_current()
        for t in self._timer_tasks:
            t.cancel()
        # Wait for any in-flight DB saves (transcripts, token usage) so they
        # are not silently cancelled when the event loop shuts down the task.
        if self._pending_saves:
            await asyncio.gather(*self._pending_saves, return_exceptions=True)
        logger.debug("[pipeline] Cleanup complete")

    # --- Timeout watchers ---

    async def _max_duration_watcher(self, ws: WebSocket) -> None:
        """Closes session after max_duration seconds, with a 60s warning."""
        warn_at = self.max_duration - WARNING_ADVANCE_SECONDS
        if warn_at > 0:
            await asyncio.sleep(warn_at)
            logger.info(
                "[pipeline] Max duration warning — %ss remaining",
                WARNING_ADVANCE_SECONDS,
            )
            await self._send_json(
                ws,
                {
                    "type": "session_warning",
                    "reason": "max_duration",
                    "remaining_seconds": WARNING_ADVANCE_SECONDS,
                },
            )
            await asyncio.sleep(WARNING_ADVANCE_SECONDS)
        else:
            await asyncio.sleep(self.max_duration)
        logger.info("[pipeline] Session ended by max_duration")
        await self._send_json(ws, {"type": "session_end", "reason": "max_duration"})
        await self._close_ws(ws, code=1000)

    async def _inactivity_watcher(self, ws: WebSocket) -> None:
        """Closes session if user is silent for inactivity_timeout seconds."""
        while True:
            await asyncio.sleep(5)  # check every 5 seconds
            if self.current_task is not None and not self.current_task.done():
                continue
            elapsed = time.monotonic() - self._last_activity
            remaining = self.inactivity_timeout - elapsed

            if 0 < remaining <= WARNING_ADVANCE_SECONDS and not self._inactivity_warning_sent:
                self._inactivity_warning_sent = True
                logger.info("[pipeline] Inactivity warning — %ds remaining", int(remaining))
                await self._send_json(
                    ws,
                    {
                        "type": "session_warning",
                        "reason": "inactivity",
                        "remaining_seconds": int(remaining),
                    },
                )

            if elapsed >= self.inactivity_timeout:
                logger.info("[pipeline] Session ended by inactivity (elapsed %.0fs)", elapsed)
                await self._send_json(ws, {"type": "session_end", "reason": "inactivity"})
                await self._close_ws(ws, code=1000)
                return
