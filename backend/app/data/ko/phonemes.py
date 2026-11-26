"""Korean phoneme data for acoustic pronunciation scoring.

Keys are conjoining jamo (the NFD form of a syllable), so the initial and the final
consonant of one letter map to different sounds.
"""

NORMALIZATION = "NFD"

# Whole-word overrides where spelling and sound differ.
LEXICON: dict[str, str] = {
    "감사합니다": "kamsahamnida",
    "안녕하세요": "annjʌŋhasejo",
    "한국어": "hanɡuɡʌ",
}

LETTER_MAP: dict[str, str] = {
    # initial consonants
    "ᄀ": "g",
    "ᄁ": "k",
    "ᄂ": "n",
    "ᄃ": "d",
    "ᄄ": "t",
    "ᄅ": "r",
    "ᄆ": "m",
    "ᄇ": "b",
    "ᄈ": "p",
    "ᄉ": "s",
    "ᄊ": "s",
    "ᄋ": "",
    "ᄌ": "tɕ",
    "ᄍ": "tɕ",
    "ᄎ": "tɕ",
    "ᄏ": "k",
    "ᄐ": "t",
    "ᄑ": "p",
    "ᄒ": "h",
    # vowels
    "ᅡ": "a",
    "ᅢ": "ɛ",
    "ᅣ": "ja",
    "ᅤ": "jɛ",
    "ᅥ": "ʌ",
    "ᅦ": "e",
    "ᅧ": "jʌ",
    "ᅨ": "je",
    "ᅩ": "o",
    "ᅪ": "wa",
    "ᅫ": "wɛ",
    "ᅬ": "we",
    "ᅭ": "jo",
    "ᅮ": "u",
    "ᅯ": "wʌ",
    "ᅰ": "we",
    "ᅱ": "wi",
    "ᅲ": "ju",
    "ᅳ": "ɯ",
    "ᅴ": "ɯi",
    "ᅵ": "i",
    # final consonants
    "ᆨ": "k",
    "ᆩ": "k",
    "ᆪ": "k",
    "ᆫ": "n",
    "ᆬ": "n",
    "ᆭ": "n",
    "ᆮ": "t",
    "ᆯ": "l",
    "ᆰ": "k",
    "ᆱ": "m",
    "ᆲ": "l",
    "ᆳ": "l",
    "ᆴ": "l",
    "ᆵ": "p",
    "ᆶ": "l",
    "ᆷ": "m",
    "ᆸ": "p",
    "ᆹ": "p",
    "ᆺ": "t",
    "ᆻ": "t",
    "ᆼ": "ŋ",
    "ᆽ": "t",
    "ᆾ": "t",
    "ᆿ": "k",
    "ᇀ": "t",
    "ᇁ": "p",
    "ᇂ": "t",
}
