import importlib
import unicodedata
from dataclasses import dataclass, field

import httpx

from app.core.app_logger import get_logger
from app.core.config import settings
from app.services.language_helpers import get_iso639

logger = get_logger(__name__)

# Recogniser phones and lexicon phones fold to one coarse set before they compare.
_DROP = set("ˈˌːˑʰʱʲʷˠˤʼ˞ʻ.‿ ,;:!?'\"-–—()…·ʔ▁")
_FOLD = {
    "b": "p",
    "d": "t",
    "g": "k",
    "ɡ": "k",
    "β": "p",
    "ð": "t",
    "ɣ": "k",
    "z": "s",
    "ʒ": "ʃ",
    "v": "f",
    "ʋ": "f",
    "ɾ": "r",
    "ɹ": "r",
    "ɻ": "r",
    "ʁ": "r",
    "ɽ": "r",
    "ɭ": "l",
    "ɫ": "l",
    "ʎ": "l",
    "ɛ": "e",
    "æ": "e",
    "ɜ": "e",
    "ɪ": "i",
    "ɨ": "u",
    "ɯ": "u",
    "ʊ": "u",
    "ʉ": "u",
    "y": "i",
    "ɔ": "o",
    "ɒ": "o",
    "ʌ": "o",
    "ɵ": "o",
    "ɑ": "a",
    "ɐ": "a",
    "ə": "a",
    "ɚ": "a",
    "ᵻ": "i",
    "ɲ": "n",
    "ɳ": "n",
    "ɱ": "m",
    "ç": "x",
    "ɕ": "ʃ",
    "ʂ": "ʃ",
    "ɦ": "h",
    "w": "u",
    "j": "i",
}
_MAX_AUDIO_BYTES = 10 * 1024 * 1024


class PronunciationUnavailable(Exception):
    """The local scorer cannot answer; the caller falls back to the LLM comparison."""


@dataclass
class WordScore:
    word: str
    score: float
    weak_phonemes: list[str] = field(default_factory=list)


@dataclass
class PronunciationScore:
    score: float
    words: list[WordScore]


def fold(ipa: str) -> list[str]:
    out = []
    for ch in unicodedata.normalize("NFD", ipa):
        if unicodedata.combining(ch) or ch in _DROP or ch.isspace():
            continue
        out.append(_FOLD.get(ch, ch))
    return out


def load_phoneme_data(target_language: str):
    """Return the language's phoneme module from its data package, or None."""
    for package in (target_language.replace("-", "_"), get_iso639(target_language)):
        try:
            return importlib.import_module(f"app.data.{package}.phonemes")
        except ModuleNotFoundError:
            continue
    return None


def target_phonemes(word: str, data) -> list[str]:
    """Folded phonemes of one word: the lexicon entry, else a longest-match letter map."""
    composed = unicodedata.normalize("NFC", word.lower())
    start, end = 0, len(composed)
    while start < end and not composed[start].isalnum():
        start += 1
    while end > start and not composed[end - 1].isalnum():
        end -= 1
    composed = composed[start:end]
    if composed in data.LEXICON:
        return fold(data.LEXICON[composed])
    word = unicodedata.normalize(data.NORMALIZATION, composed)
    keys = sorted(data.LETTER_MAP, key=len, reverse=True)
    out, i = "", 0
    while i < len(word):
        key = next((k for k in keys if word.startswith(k, i)), None)
        out += data.LETTER_MAP[key] if key else ""
        i += len(key) if key else 1
    return fold(out)


def aligned_matches(target: list[str], spoken: list[str]) -> list[bool]:
    """Edit-distance alignment; True where a target phoneme meets an equal spoken one."""
    n, m = len(target), len(spoken)
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        cost[i][0] = i
    for j in range(m + 1):
        cost[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost[i][j] = min(
                cost[i - 1][j] + 1,
                cost[i][j - 1] + 1,
                cost[i - 1][j - 1] + (target[i - 1] != spoken[j - 1]),
            )
    matched = [False] * n
    i, j = n, m
    while i > 0 and j > 0:
        if cost[i][j] == cost[i - 1][j - 1] + (target[i - 1] != spoken[j - 1]):
            matched[i - 1] = target[i - 1] == spoken[j - 1]
            i, j = i - 1, j - 1
        elif cost[i][j] == cost[i - 1][j] + 1:
            i -= 1
        else:
            j -= 1
    return matched


def score_phonemes(text: str, spoken_ipa: str, data) -> PronunciationScore:
    spoken = fold(spoken_ipa)
    per_word = [(w, target_phonemes(w, data)) for w in text.split()]
    per_word = [(w, p) for w, p in per_word if p]
    flat = [p for _, phones in per_word for p in phones]
    if not flat:
        raise PronunciationUnavailable("target has no phonemes")
    matched = aligned_matches(flat, spoken)
    words, pos = [], 0
    for word, phones in per_word:
        hits = matched[pos : pos + len(phones)]
        weak = [p for p, ok in zip(phones, hits, strict=True) if not ok]
        words.append(WordScore(word, round(sum(hits) / len(phones), 3), weak))
        pos += len(phones)
    return PronunciationScore(round(sum(matched) / len(flat), 3), words)


class LocalPronunciationProvider:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def recognise(self, audio: bytes, filename: str, mime_type: str, language: str) -> str:
        """Post the audio to the phoneme service and return its IPA string."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/phonemes",
                files={"file": (filename, audio, mime_type)},
                data={"language": language},
                timeout=60.0,
            )
            response.raise_for_status()
        body = response.json()
        phonemes = body.get("phonemes") if isinstance(body, dict) else None
        if isinstance(phonemes, list) and all(isinstance(p, str) for p in phonemes):
            phonemes = "".join(phonemes)
        if not isinstance(phonemes, str):
            raise ValueError("phoneme service returned an unusable body")
        return phonemes

    async def score(
        self,
        audio: bytes,
        filename: str,
        mime_type: str,
        *,
        target: str,
        target_language: str,
    ) -> PronunciationScore:
        data = load_phoneme_data(target_language)
        if data is None:
            raise PronunciationUnavailable(f"no phoneme data for {target_language}")
        if len(audio) > _MAX_AUDIO_BYTES:
            raise PronunciationUnavailable("audio too large")
        try:
            spoken = await self.recognise(audio, filename, mime_type, target_language)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            logger.warning("[pronunciation] service failed: %s", exc)
            raise PronunciationUnavailable(str(exc)) from exc
        return score_phonemes(target, spoken, data)


def get_pronunciation_provider() -> LocalPronunciationProvider | None:
    if settings.PRONUNCIATION_PROVIDER == "local" and settings.PRONUNCIATION_BASE_URL:
        return LocalPronunciationProvider(settings.PRONUNCIATION_BASE_URL)
    return None
