"""Small facts about text: which script it is in, and the other language.
"""

import re
from .config import DEFAULT_SECONDARY, LANGUAGES, PIVOT_LANG


LATIN_PATTERN = re.compile(r"[A-Za-z]")

# Han characters. Google's zh-TW model happily returns Latin text when you
# speak English at it, so the presence of Han glyphs - not the confidence
# score - is what actually identifies Chinese speech.
CJK_PATTERN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def has_cjk(text):
    return bool(CJK_PATTERN.search(text or ""))


def other_lang(code):
    """The language Auto shows for a given source.

    English is the hub, so anything else becomes English, and English becomes
    whichever second language is configured. That keeps the old two-language
    behaviour intact while leaving room for a third.
    """
    return DEFAULT_SECONDARY if code == PIVOT_LANG else PIVOT_LANG


def script_of(text):
    return "han" if has_cjk(text) else "latin"


def script_matches(code, text):
    """Whether transcribed text is written in the script that language uses.

    English and Kiswahili share the Latin script, so this only catches gross
    mismatches - it cannot tell those two apart. Language detection does that.
    """
    expected = LANGUAGES.get(code, {}).get("script")
    if expected == "han":
        return has_cjk(text)
    if expected == "latin":
        return bool(LATIN_PATTERN.search(text))
    return True
