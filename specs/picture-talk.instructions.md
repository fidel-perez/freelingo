# Picture Talk

Picture talk is a voice session in which the learner talks with Lingu about a picture the browser
draws. It reuses the voice pipeline, the browser image worker, the mistake log, the correction style,
and pronunciation scoring. The contracts of those parts stay in their own specs.

## Entry and Gating

- `/conversation?picture=<study plan id>` opens `ConversationMode` in picture mode, as `?situation=`
  and `?lesson=` do for their sources.
- The Situations catalog shows the picture entry only when `pictureTalkAvailable(user)` holds:
  `images_enabled` is true, `image_model_base_url` is set, and the browser supports WebGPU.
- The entry is a navigation aid. The backend authorizes the scene and the session by plan ownership.
- The page asks for a scene first. A bad plan id, an unavailable entry, or a failed scene request shows
  an alert with a retry control and a link back to Situations.
- The session starts only after the learner presses start. Picture mode does not auto-start.

## Scene

- `POST /api/picture-talk/scenes` takes `{study_plan_id, conversation_id?}` and returns
  `{id, study_plan_id, image_prompt, words}`. Rate limit: `30/minute`.
- The plan must pass `get_situation_plan` for the caller. A foreign or missing plan, or a
  conversation that is not the caller's or not on that plan, returns 404 `picture_unavailable`.
- The backend reads the study-language side of up to 5 flashcards of the plan. Due cards come first,
  then mistake cards, then the earliest review date. A mistake card contributes its corrected study
  text. Suspended cards are skipped.
- The configured LLM writes a scene through `structured_output` at the plan's `cefr_level`. The scene
  holds an English image prompt and at most 5 study-language words. The image prompt is English because
  the image model takes English prompts only.
- An `LLMError` returns 503 `picture_generation_failed`.
- Each scene is stored in `picture_talk_scenes` under its study plan.

## Image

- `PictureTalkImage` draws the scene prompt through the browser image worker in
  `browser-images.instructions.md` and keeps the bitmap in the page.
- The container carries `data-picture-talk-image` and `data-image-state`, which is `loading`, `done`,
  or `failed`. A failed draw shows an alert and does not end the session.

## Session

- The WebSocket auth message carries `scene_id`, a strict integer in `1..2147483647`.
- `scene_id` is exclusive with `lesson_id`, `grammar_practice_session_id`, the situation pair, and
  `voice_trial_token`. A conflicting, foreign, or missing scene sends
  `{type: "error", code: "picture_unavailable"}` and closes with 1008.
- The scene's persisted plan decides the language and the progress owner, not client state.
- The pipeline reads the scene as `<picture_scene>` practice context. The prompt treats it as
  reference data, not instructions.
- The voice access check, the chat history writes, and the memory rules run as for every voice session.
  Picture talk consumes no allowance of its own.
- The tutor opens the session with one or two short sentences about the picture. The server then sends
  `{type: "scene", scene_id, silence_wait_s}`.
- The client sends `{type: "next_scene", scene_id}` after it fetched a new scene. The pipeline loads
  the scene for the same plan, cancels the current turn, resets the ladder, sends `scene`, and starts
  a tutor turn about the new picture. A scene that is missing or belongs to another plan sends
  `picture_unavailable` and keeps the session.
- One conversation record holds every picture of the session.

## Spoken-Only Page

- The picture page has no text input and no conversation starters. The learner answers by voice only.
- The page shows the picture, the transcript, and a next-picture control while the session is active.

## Native Line

- The prompt tells the tutor to add the meaning of each study-language sentence on its own line that
  starts with `NATIVE: `. A learner whose native language is the study language gets no such line.
- The pipeline splits the streamed reply by line. A study line is spoken sentence by sentence while the
  model streams. A native line is spoken whole.
- The final `transcript` frame carries the text with the marker removed and `native_lines`, the list of
  native texts. Saved assistant messages carry the text without the marker.
- Before each audio frame the server sends `{type: "audio_voice", voice: "study" | "native"}`. The
  client plays the next binary frame with that voice and falls back to `study`.
- Barge-in cancels queued study and native audio together.

## Native Voice

- `NATIVE_TTS_PROVIDER` is empty, `local`, `openai`, or `gemini`. It builds a second instance of the
  provider class that `TTS_PROVIDER` uses for the study voice.
- `NATIVE_TTS_BASE_URL` is the `local` provider's endpoint. `openai` and `gemini` use their existing
  keys and settings.
- `NATIVE_TTS_VOICES` maps a native language to a voice id, as a comma list such as `es=<voice-id>`
  or `es=<voice-id>,fr=<voice-id>`. A full code such as `pt-br` wins over its base `pt`.
- The native line is skipped, with no error, when the provider is empty, when the provider's own
  settings are missing, or when the learner's native language has no entry in the map. The tutor still
  writes the line and the client shows it in the transcript.
- The skip rule exists because the `local` class (`KokoroTTSService`) drops the `language` argument and
  speaks one fixed voice. Only the voice id picks the language, so a language without a voice cannot
  be spoken correctly.
- The native voice server runs outside this repository and is not part of the Compose stack.
  Both Compose files forward the three variables, documented in `.env.example`.

## Audio Delivery

- The `local` class requests `mp3`. The native voice server answers WAV whatever `response_format`
  says. The backend relays the bytes as received, and the binary frame carries them unchanged.
- `capSilence` in `frontend/src/lib/cap-silence.ts` shortens silent runs in 16-bit mono WAV. It
  passes any other bytes through unchanged, so an mp3 or a non-mono WAV plays as received.
- The client trims each native frame with `capSilence` and then plays it. A frame that is a WAV gets
  the `audio/wav` type, any other frame `audio/mpeg`.
- Study frames are not trimmed.

## Playback Speed

- `picture-audio-queue.ts` plays every frame through an `HTMLAudioElement`, one frame at a time.
- A native frame sets `preservesPitch = true` and `playbackRate` to the learner's
  `native_speech_rate`. A study frame sets `playbackRate` to 1.
- The speed is a client setting. The backend never sends a provider speed for it, because a provider
  speed is not linear and the native voice engine renders a doubled request at about 1.3x.
- `AudioBufferSourceNode.playbackRate` raises the pitch, so picture talk does not use the Web Audio
  buffer path for native audio.
- `users.native_speech_rate` is a float from 1.0 to 2.5, default 1.8. `PATCH /api/auth/me` accepts
  it and `GET /api/auth/me` returns it. Out-of-range or non-finite values return 422.
- Settings shows the rate in `NativeSpeechRateSection`, in steps of 0.1. Its strings live under
  `pictureTalk` in every `messages/*.json`.

## Hint Ladder

- Per-level data lives in `backend/app/data/picture_talk_levels.py` and names no language.
- The wait is the silence, in seconds, after the tutor's audio ends before the client sends
  `{type: "no_answer"}`. The server sends the wait in each `scene` event.
- The rungs, in order, are `rephrase`, `two_options`, `first_word`, and `model_answer`.
- Each CEFR level has its own wait and rungs:
  - A1: 14 s; `rephrase`, `two_options`, `first_word`, `model_answer`.
  - A2: 12 s; `rephrase`, `two_options`, `first_word`, `model_answer`.
  - B1: 10 s; `rephrase`, `first_word`, `model_answer`.
  - B2: 8 s; `rephrase`, `model_answer`.
  - C1: 7 s; `rephrase`, `model_answer`.
  - C2: 6 s; `model_answer`.
- An unknown level uses A2.
- At A1 the tutor mostly narrates and asks yes-or-no or either-or questions whose answer sits in the
  question. Other levels narrate briefly and ask one open question.
- The client arms the timer when the turn is complete and the audio queue is idle. New audio, a new
  scene, a learner turn, or session end clears it. One `no_answer` is sent per completed turn.
- The server ignores `no_answer` while no question is open or a turn is running.
- A `no_answer`, a reply that the LLM classifies as "I do not know", and a reply in the native
  language each step the ladder one rung. A study-language answer resets the ladder.
- After the last rung the ladder resets, and the next turn closes the question and asks a new easy one.
- The pipeline puts the active rung in the turn context as `<picture_turn>`.
- The `model_answer` rung replies with exactly two lines: the full answer in the study language and its
  meaning on a `NATIVE: ` line.

## Pronunciation and Mistakes

- The next learner turn after a model answer is a repeat. The pipeline scores it against the model
  answer and sends `{type: "repeat_score", score, model_answer, turn_id}`.
- With `PRONUNCIATION_PROVIDER=local`, the local provider scores the repeat from its audio. If the
  provider is absent or unavailable, the LLM scores the transcript. If the LLM fails, a string
  similarity ratio gives the score.
- The tutor reacts to the score in the next turn and the ladder resets.
- Each model answer logs one mistake through `log_mistakes` with source `voice`: the native meaning
  as the front and the model answer as the back. It becomes an SM-2 card, and mistake cards come
  first when a later scene picks its words.
- With `PRONUNCIATION_PROVIDER=local`, each free answer is scored against its own transcript. Words
  below 0.7 join the turn context, up to 5, and the tutor corrects them in the learner's correction
  style and logs them with `log_mistake`.
- The correction style and the `log_mistake` tool run as in `mistake-log.instructions.md`.

## Persistence

- `picture_talk_scenes` and `users.native_speech_rate` are in revision `0065_picture_talk`.
  Their fields are in `database-models.instructions.md`.

## Related Specifications

- `voice-conversation.instructions.md`: the voice pipeline and WebSocket protocol.
- `speech-services.instructions.md`: TTS and STT provider contracts.
- `browser-images.instructions.md`: the browser image worker and prompts.
- `mistake-log.instructions.md`: mistake cards and `log_mistake`.
- `api-endpoints.instructions.md`, `database-models.instructions.md`,
  `rate-limiting.instructions.md`, and `docker.instructions.md`: contracts that this spec references.
