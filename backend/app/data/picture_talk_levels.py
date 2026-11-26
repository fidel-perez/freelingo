"""Per-level data of picture talk: hint rungs and question style."""

from dataclasses import dataclass

PICTURE_CONTEXT_PREFIX = "<picture_scene>\n"
PICTURE_CONTEXT_SUFFIX = "\n</picture_scene>"

# A tutor line spoken in the learner's native language starts with this marker, on its own line.
NATIVE_LINE_MARKER = "NATIVE: "

TOOK_TOO_LONG_TRIGGER = "[The learner pressed 'I took too long'. Apply the hint rung given in the picture turn context.]"
ANSWERED_TRIGGER = "[The learner pressed 'I answered'. Follow the answered instruction in the picture turn context.]"
ANSWERED_INSTRUCTION = (
    "The learner said an answer aloud and pressed 'I answered'. You did not hear it. Do not ask "
    "them to repeat it. Say the model answer to your open question in the study language, give its "
    "meaning on one marked native-language line, then ask one new, easy question about the picture "
    "with its marked native-language line."
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
        "Give the model answer. Start with exactly two lines: first the full answer in the "
        "study language, then its meaning on one marked native-language line. Then ask one new, "
        "easy question about the picture, with its marked native-language line."
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
    rungs: tuple[str, ...]
    guidance: str


LEVELS: dict[str, PictureTalkLevel] = {
    "A1": PictureTalkLevel(
        (RUNG_REPHRASE, RUNG_TWO_OPTIONS, RUNG_FIRST_WORD, RUNG_MODEL_ANSWER), NARRATE_GUIDANCE
    ),
    "A2": PictureTalkLevel(
        (RUNG_REPHRASE, RUNG_TWO_OPTIONS, RUNG_FIRST_WORD, RUNG_MODEL_ANSWER), OPEN_GUIDANCE
    ),
    "B1": PictureTalkLevel((RUNG_REPHRASE, RUNG_FIRST_WORD, RUNG_MODEL_ANSWER), OPEN_GUIDANCE),
    "B2": PictureTalkLevel((RUNG_REPHRASE, RUNG_MODEL_ANSWER), OPEN_GUIDANCE),
    "C1": PictureTalkLevel((RUNG_REPHRASE, RUNG_MODEL_ANSWER), OPEN_GUIDANCE),
    "C2": PictureTalkLevel((RUNG_MODEL_ANSWER,), OPEN_GUIDANCE),
}
DEFAULT_LEVEL = "A2"


def level_data(cefr_level: str) -> PictureTalkLevel:
    return LEVELS.get(cefr_level, LEVELS[DEFAULT_LEVEL])
