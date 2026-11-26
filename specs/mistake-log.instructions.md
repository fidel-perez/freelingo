---
description: "Current-state specification for the per-plan mistake log: storage, capture from chat, voice chat and evaluated exercises, and the SM-2 flashcard each mistake creates."
applyTo: "backend/app/models/mistake.py, backend/app/services/{mistake_service,conversation_pipeline}.py, backend/app/routers/{chat,lessons}.py, backend/app/services/prompts/common.py, backend/alembic/versions/0062_mistakes.py"
---

# Mistake Log

## Purpose and scope

The mistake log records each learner mistake that Lingu corrects in chat or voice chat, and each
correction of an evaluated exercise. Each new mistake becomes one SM-2 flashcard in the same study
plan. The feature names no study language: the text, the correction and the note are stored as given.

## Data model

`mistakes` stores:

- `study_plan_id`: owning plan, cascade delete. Language ownership follows the plan, as for flashcards.
- `original`, `corrected`, `note`: the learner's text, the corrected text and a short note in the
  native language. The note may be empty.
- `original_key`: the original text with whitespace collapsed and case folded, at most 300 characters.
  A unique constraint on `(study_plan_id, original_key)` makes a repeat update the existing row.
- `source`: one of `chat`, `voice` or `exercise`, set when the mistake is first logged.
- `occurrences`: how often the mistake was logged, starting at 1.
- `flashcard_id`: the card created for the mistake, set to null when the card is deleted.
- `created_at`, `updated_at`.

Alembic revision `0062_mistakes` creates the table.

## Capture

- Chat (`routers/chat.py`) and voice chat (`conversation_pipeline.py`) offer the native `log_mistake`
  tool next to `save_user_memory`. The tool takes `original`, `corrected` and an optional `note`. The
  memory prompt block, which is also off when memory tools are off, tells Lingu to call the tool once
  per correction.
- The tool executor logs in the conversation's study plan with source `chat` or `voice`. Without a
  study plan, or with a blank text, the tool result is an error and nothing is stored.
- The tool result carries `logged`, never `saved`, so a logged mistake does not raise the
  `memory_updated` notification.
- Exercise evaluation (`answer_exercise`) logs one mistake per entry of the exercise's `corrections`
  with source `exercise`, in the same transaction as the answer. Exercise logging makes no LLM call.
- An entry whose original equals its correction after normalization is skipped.

## Flashcards

- A new mistake creates one flashcard in the same study plan with `source` `mistake`. The `word` holds
  the original text cut to 100 characters, the definition and `translation` hold the corrected text,
  and the example sentence holds the note, or the corrected text when the note is empty.
- A repeat in the same plan increments `occurrences`, refreshes the correction and the note, and
  rewrites the existing card. The card's SM-2 schedule restarts as a failed review (quality 1).
- When the card was deleted, a repeat creates a new card and relinks the mistake.
- The mistake and its card are written in one transaction. Flashcards are scoped to the plan, so
  mistakes of one study language never reach the cards of another.
