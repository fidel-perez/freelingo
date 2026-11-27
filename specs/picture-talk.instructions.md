# Picture Talk

Picture talk is a spoken session in which the learner talks with Lingu about a picture the browser
draws. It reuses the voice pipeline's button turn and TTS, the browser image worker, and the mistake
log. The contracts of those parts stay in their own specs. Picture talk hears no learner audio: the
learner answers aloud and presses a button, so the correction style and pronunciation scoring do not
apply.

## Entry and Gating

- `/conversation?picture=<study plan id>` opens `ConversationMode` in picture mode, as `?situation=`
  and `?lesson=` do for their sources.
- The Situations catalog shows the picture entry only when `pictureTalkAvailable(user)` holds:
  `images_enabled` is true and `image_model_base_url` is set. WebGPU is not required.
- The page needs only a signed-in user. A browser without WebGPU still talks about the scene, and the
  image slot shows the scene's words (`data-image-state="unsupported"`).
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
- The `FAST_LLM_MODEL` model at `FAST_LLM_BASE_URL` writes a scene through `structured_output` at the
  plan's `cefr_level`. When no fast model is set, the configured LLM writes it. The scene
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
- Every picture-talk LLM call (opening, hint rung, answered turn, next scene) uses `FAST_LLM_BASE_URL`
  and `FAST_LLM_MODEL`, and falls back to the configured LLM when they are empty. Upstream chat keeps
  its own LLM setting.
- The tutor opens the session with one or two short sentences about the picture, with native lines. The
  server then sends `{type: "scene", scene_id}`.
- The client sends `{type: "next_scene", scene_id}` after it fetched a new scene. The pipeline loads
  the scene for the same plan, cancels the current turn, resets the ladder, sends `scene`, and starts
  a tutor turn about the new picture. A scene that is missing or belongs to another plan sends
  `picture_unavailable` and keeps the session.
- One conversation record holds every picture of the session.

## Speech and Buttons Page

- The picture page renders no `input`, no `textarea` and no conversation starters. The learner answers
  aloud and presses the buttons of `button-turn-controls.tsx`.
- The page opens no microphone and loads no STT or VAD. The browser sends no audio frame, and the
  server ignores any audio frame it receives in a picture session.
- The client sends `{type: "button_event", event: "answered" | "took_too_long"}` for each press and
  `{type: "next_scene", scene_id}` for a next picture.
- An `answered` press ends the learner's turn. The tutor cannot hear the answer, so it speaks the model
  answer with its native line and asks the next question. The press logs no mistake.
- The page shows the picture, the transcript, the two answer buttons and a next-picture control while
  the session is active.

## Native Line

- The prompt tells the tutor to add the meaning of each study-language sentence on its own line that
  starts with `NATIVE: `. A learner whose native language is the study language gets no such line.
- The pipeline splits the streamed reply by line. A study line is spoken sentence by sentence while the
  model streams. A native line is spoken whole.
- The final `transcript` frame carries the text with the marker removed and `native_lines`, the list of
  native texts. Saved assistant messages carry the text without the marker.
- Before each audio frame the server sends `{type: "audio_voice", voice: "study" | "native"}`. The
  client plays the next binary frame with that voice and falls back to `study`.
- Each button press cancels queued study and native audio together, on the server and in the client.

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
- The rungs, in order, are `rephrase`, `two_options`, `first_word`, and `model_answer`.
- Each CEFR level has its own rungs:
  - A1: `rephrase`, `two_options`, `first_word`, `model_answer`.
  - A2: `rephrase`, `two_options`, `first_word`, `model_answer`.
  - B1: `rephrase`, `first_word`, `model_answer`.
  - B2: `rephrase`, `model_answer`.
  - C1: `rephrase`, `model_answer`.
  - C2: `model_answer`.
- An unknown level uses A2.
- At A1 the tutor mostly narrates and asks yes-or-no or either-or questions whose answer sits in the
  question. Other levels narrate briefly and ask one open question.
- Each `took_too_long` event steps the ladder one rung. A new scene or an `answered` press resets it.
- The pipeline holds the rung of the open question and puts it in the turn context as `<picture_turn>`.
- The `model_answer` rung starts with two lines: the full answer in the study language and its meaning
  on a `NATIVE: ` line. The tutor then asks a new easy question with its own native line, and the
  ladder starts over for that question.
- There is no silence timer. The client sends nothing when the learner stays quiet.

## Mistakes

- When the ladder ends at a model answer after `took_too_long` presses, the pipeline logs one mistake
  through `log_mistakes` with source `voice`: the native meaning as the front and the model answer as
  the back. It becomes an SM-2 card, and mistake cards come first when a later scene picks its words.
- When the learner gets no native line, the pipeline logs the study line as both the front and the back.
- An `answered` press logs no mistake.

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
