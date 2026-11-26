---
description: "Current-state specification for opt-in lesson and situation images drawn in the learner's browser: image prompt endpoints and caches, the user setting, the browser worker, and the content security policy."
applyTo: "backend/app/routers/{lessons,situations,auth}.py, backend/app/models/{lesson,user,situation_image_prompt}.py, backend/app/schemas/**, backend/app/core/config.py, backend/alembic/versions/**, frontend/src/middleware.ts, frontend/src/lib/image-*, frontend/src/workers/**, frontend/src/components/**/*Image*, docker-compose*.yml, .env.example"
---

# Browser Images

## Purpose

Lesson pages and situation pages can show an image that the learner's browser draws. The image
model runs on the learner's GPU, so the server runs no image model and calls no image provider.

The browser calls no LLM, TTS, or STT provider. The browser fetches only the model weights, from
the host that `IMAGE_MODEL_BASE_URL` names. The backend remains the only gateway to LLM providers.

## Image prompt endpoints

Each endpoint returns `{"prompt": str}`, a short English image prompt. The image model accepts
English prompts only, so the backend asks the configured LLM to write the prompt from the lesson or
situation content, whatever the learner's languages are.

- `GET /api/lessons/{lesson_id}/image-prompt` answers only for the owner of the lesson's study plan.
- `GET /api/situations/{situation_id}/image-prompt?study_plan_id=` answers only when
  `get_situation_plan` finds that plan owned by the caller.

Both endpoints require authentication. Ownership rules follow `study-plan.instructions.md` and
`situations.instructions.md`. Complete request and error contracts live in
`api-endpoints.instructions.md`.

## Prompt caches

The backend stores each English prompt, so a revisit costs no LLM call.

- `lessons.image_prompt` caches the prompt per lesson.
- `situation_image_prompts` caches the prompt per situation id. Every learner shares that row,
  because the situation catalog is server-owned and identical for all learners.

One Alembic revision adds the `lessons.image_prompt` column, the `situation_image_prompts` table,
and the `users.images_enabled` column. Column definitions live in `database-models.instructions.md`.

## User setting

- `users.images_enabled` is a boolean that defaults to `false`, because the first model load
  downloads several GB.
- `PATCH /api/auth/me` writes `images_enabled`.
- The user response carries `images_enabled`, `image_model_base_url`, and `image_model_configured`.
  `image_model_configured` is true when `IMAGE_MODEL_BASE_URL` is nonempty.
- The setting is global per user and is not scoped to a study language.
- The Settings page shows the toggle only when `navigator.gpu` exists and
  `image_model_configured` is true.

## Configuration

`IMAGE_MODEL_BASE_URL` is a backend `Settings` field with an empty default, and an empty value
keeps the feature off. The operator sets it to the URL that serves the model weights. A deployment
may point it at a mirror.

Both Compose files pass `IMAGE_MODEL_BASE_URL` through the `environment:` of the backend and of the
frontend. `docker.instructions.md` owns the propagation rules.

## Content security policy

`frontend/src/middleware.ts` builds the production policy per request from
`process.env.IMAGE_MODEL_BASE_URL`. The policy function is exported and has its own unit tests.

- `connect-src` is `'self' ws: wss:` when the variable is empty.
- `connect-src` adds `https:` when the variable is set, because the worker fetches weights from the
  configured host.
- The middleware matcher covers every path, including `/_next/static`.

## Browser worker

- A Web Worker runs `onnxruntime-web` on WebGPU and draws a 512x512 image from the English prompt.
- The frontend's own origin serves the runtime's `.wasm` and `.mjs` files.
- The browser caches the weights and each drawn image, so a revisit draws nothing and downloads
  nothing.
- The page requests the prompt from the backend first and hands the prompt to the worker.

## Failure behavior

- A browser without WebGPU shows the page without an image and without an error.
- A failed weight download, worker error, or prompt error shows the page without an image and
  without an error.
- An empty `IMAGE_MODEL_BASE_URL`, or `images_enabled` set to `false`, requests no prompt and starts
  no worker.

## Related specifications

- `situations.instructions.md`: situation pages that show an image.
- `learning-resources.instructions.md`: lesson pages that show an image.
- `architecture-frontend.instructions.md`: frontend structure and middleware.
- `api-endpoints.instructions.md`, `database-models.instructions.md`, `docker.instructions.md`, and
  `services.instructions.md`: contracts that this spec references.
