"""A cautious pass that changes only how a few things are pronounced.

Rules apply only where testing showed Kokoro gets something wrong, and each
leaves the words themselves alone. Anything not covered is passed through.
"""
import re

# Pronounced as words, never spelled, even when a paper defines them
SAY_AS_WORD = {"NASA", "COVID", "AIDS", "UNESCO", "NATO", "OPEC", "LASER",
               "RADAR", "SARS", "MERS", "PISA", "UNICEF", "ASEAN", "FIFA"}
CURRENCY = {"AU": "Australian", "US": "U-S", "NZ": "New Zealand",
            "CA": "Canadian", "HK": "Hong Kong", "NT": "New Taiwan",
            "SG": "Singapore"}


def spelled(abbr):
    """SOC -> S-O-C, which Kokoro reads as smooth separate letters."""
    return "-".join(abbr)


def defined_abbreviations(text):
    """Abbreviations the paper introduces as 'Long Form (ABBR)'."""
    found = {}
    for m in re.finditer(r"((?:[A-Z][\w'’-]*[,\s]+(?:and\s+|of\s+|with\s+|the\s+|for\s+)?){1,8})\(([A-Z]{2,6})s?\)", text):
        abbr = m.group(2)
        initials = "".join(w[0] for w in re.findall(r"[A-Z][\w'’-]*", m.group(1)))
        # Only trust it when the capitals of the long form spell the short one
        if abbr not in SAY_AS_WORD and abbr[0] in initials:
            found[abbr] = m.group(1).strip(" ,")
    return found


def speakable(text, known=None):
    known = known if known is not None else defined_abbreviations(text)

    # Currency ("AU $20") is deliberately left alone. Rewriting it as
    # "20 Australian dollars" broke the grammar of "a AU $20 gift voucher",
    # and doing it properly needs real grammar handling.

    # "(SOC)" straight after its long form: a short pause either side
    for abbr in known:
        text = re.sub(r"\s*\(" + abbr + r"s?\)", f", {spelled(abbr)},", text)

    # Every other use of a defined abbreviation, and bare two-letter capitals
    # In text set in capitals ("RESULTS OF THE STUDY") a two-letter word is
    # an ordinary word, so the two-letter rule stands down there.
    caps = sum(c.isupper() for c in text) / max(1, sum(c.isalpha() for c in text))
    shouting = caps > 0.5

    def letters(m):
        w = m.group(0)
        if w in SAY_AS_WORD:
            return w
        if w in known or (len(w) == 2 and not shouting):
            return spelled(w)
        return w
    text = re.sub(r"\b[A-Z]{2,6}\b", letters, text)
    return re.sub(r",\s*,", ",", text)
