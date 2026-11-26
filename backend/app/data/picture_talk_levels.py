"""Per-level data of picture talk: silence wait, hint rungs and question style."""

from dataclasses import dataclass

PICTURE_CONTEXT_PREFIX = "<picture_scene>\n"
PICTURE_CONTEXT_SUFFIX = "\n</picture_scene>"

# A tutor line spoken in the learner's native language starts with this marker, on its own line.
NATIVE_LINE_MARKER = "NATIVE: "

NO_ANSWER_TRIGGER = (
    "[The learner stayed silent. Apply the hint rung given in the picture turn context.]"
)
NEXT_SCENE_TRIGGER = (
    "[A new picture is now shown. Open with one or two short sentences about it, following "
    "the picture talk guidance.]"
)

RUNG_REPHRASE = "rephrase"
RUNG_TWO_OPTIONS = "two_options"
RUNG_FIRST_WORD = "first_word"
RUNG_MODEL_ANSWER = "model_answer"

RUNG_INSTRUCTIONS = {
    RUNG_REPHRASE: (
        "Ask the open question again with simpler words and a shorter sentence. "
        "Do not give the answer."
    ),
    RUNG_TWO_OPTIONS: (
        "Ask the open question again as an either-or question that names two options, "
        "one of them the right answer."
    ),
    RUNG_FIRST_WORD: (
        "Say only the first word of the answer and invite the learner to finish the sentence."
    ),
    RUNG_MODEL_ANSWER: (
        "Give the model answer. Reply with exactly two lines: first the full answer in the "
        "study language, then its meaning on one marked native-language line. Add nothing else."
    ),
}

NARRATE_GUIDANCE = (
    "Mostly narrate the picture yourself in short, simple sentences. Ask only yes-or-no or "
    "either-or questions whose answer sits inside the question, so the learner can answer "
    "with one word taken from it."
)
OPEN_GUIDANCE = (
    "Narrate briefly, then ask one open question about the picture that the learner can "
    "answer with a short sentence."
)


@dataclass(frozen=True)
class PictureTalkLevel:
    silence_wait_s: int
    rungs: tuple[str, ...]
    guidance: str


LEVELS: dict[str, PictureTalkLevel] = {
    "A1": PictureTalkLevel(
        14, (RUNG_REPHRASE, RUNG_TWO_OPTIONS, RUNG_FIRST_WORD, RUNG_MODEL_ANSWER), NARRATE_GUIDANCE
    ),
    "A2": PictureTalkLevel(
        12, (RUNG_REPHRASE, RUNG_TWO_OPTIONS, RUNG_FIRST_WORD, RUNG_MODEL_ANSWER), OPEN_GUIDANCE
    ),
    "B1": PictureTalkLevel(10, (RUNG_REPHRASE, RUNG_FIRST_WORD, RUNG_MODEL_ANSWER), OPEN_GUIDANCE),
    "B2": PictureTalkLevel(8, (RUNG_REPHRASE, RUNG_MODEL_ANSWER), OPEN_GUIDANCE),
    "C1": PictureTalkLevel(7, (RUNG_REPHRASE, RUNG_MODEL_ANSWER), OPEN_GUIDANCE),
    "C2": PictureTalkLevel(6, (RUNG_MODEL_ANSWER,), OPEN_GUIDANCE),
}
DEFAULT_LEVEL = "A2"


def level_data(cefr_level: str) -> PictureTalkLevel:
    return LEVELS.get(cefr_level, LEVELS[DEFAULT_LEVEL])
