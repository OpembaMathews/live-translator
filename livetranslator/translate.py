"""Translation: the local models, and the optional Claude and Gemini services.
"""

import collections
import pathlib
import re
import json as _json
import urllib.error
import urllib.request
from .config import LANGUAGES, PIVOT_LANG


def translation_pairs():
    """Direct routes to verify at startup: every language to and from the pivot.

    Other combinations (Chinese to Kiswahili, say) are reached by pivoting, so
    they need no pack of their own.
    """
    return ([(c, PIVOT_LANG) for c in LANGUAGES if c != PIVOT_LANG]
            + [(PIVOT_LANG, c) for c in LANGUAGES if c != PIVOT_LANG])


class Translator:
    """Base interface. Subclasses implement translate()."""

    def translate(self, text, source, target):
        raise NotImplementedError


Attempt = collections.namedtuple("Attempt", "text ok repaired")


class Checked(Translator):
    """Wraps a translator, spots a translation that broke off, retries it.

    The offline packs are trained largely on film subtitles: they are good
    for a caption and give up partway through the long sentences of a
    journal. Measured on 100 sentences of one paper, 43 came back ending on
    a dangling comma. Translating those again one clause at a time recovered
    24 of the 43.

    Only a sentence that already failed is retried, which is the whole point
    of checking first: translating everything clause by clause helped some
    sentences and spoiled others, and this cannot spoil what was right.
    """

    # A translation that ends on a clause separator stopped early. The
    # source sentences all end in a full stop, so this needs no second
    # model to judge it.
    DANGLING = re.compile(r"[,\uff0c\u3001;\uff1b:\uff1a]\s*$")
    CLAUSE = re.compile(r",\s+(?=\w)")
    ENDINGS = "\u3002.!?\uff01\uff1f"
    # A clause often comes back with its own comma attached; joining
    # without trimming put ",，" in the middle of the sentence.
    TRIM = ENDINGS + ",\uff0c\u3001;\uff1b:\uff1a "
    JOIN = "\uff0c"          # the Chinese comma joins clauses back together

    def __init__(self, engine):
        self.engine = engine
        self.checked = 0
        self.repairs = 0
        self.suspect = 0

    def translate(self, text, source, target):
        return self.attempt(text, source, target).text

    def attempt(self, text, source, target):
        """Translate, and say whether the result can be trusted."""
        out = self.engine.translate(text, source, target).strip()
        self.checked += 1
        if not self.DANGLING.search(out):
            return Attempt(out, True, False)
        mended = self._by_clause(text, source, target, out)
        if mended is None:
            self.suspect += 1
            return Attempt(out, False, False)
        self.repairs += 1
        return Attempt(mended, True, True)

    def _by_clause(self, text, source, target, was):
        """The sentence again, a clause at a time, or None if that is no
        better. A one-clause sentence has nothing to split, and a retry that
        breaks off in the same way has not helped."""
        parts = [p.strip() for p in self.CLAUSE.split(text) if p.strip()]
        if len(parts) < 2:
            return None
        pieces = []
        for part in parts:
            got = self.engine.translate(part, source, target).strip()
            got = got.rstrip(self.TRIM).strip()
            if got:
                pieces.append(got)
        if not pieces:
            return None
        mended = self.JOIN.join(pieces) + self.ENDINGS[0]
        if self.DANGLING.search(mended[:-1]) or len(mended) <= len(was):
            return None
        return mended


class LeanEngine(Translator):
    """Runs Argos's models directly on CTranslate2, skipping argostranslate.

    argostranslate imports stanza for sentence splitting, stanza requires
    torch, and torch alone is 526MB - about two thirds of a packaged app,
    for a feature we do not use. The language packs themselves are plain
    CTranslate2 models with a SentencePiece vocabulary, so they load without
    any of that. Dropping this chain removes roughly 760MB of dependencies.
    """

    SPLIT = re.compile(r"(?<=[.!?。！？；;])\s*")
    MARKER = "▁"          # SentencePiece word-boundary marker

    def __init__(self, pairs):
        self._packages = self._discover()
        self._loaded = {}
        self._pairs = list(pairs)
        for source, target in self._pairs:
            if not self._route(source, target):
                raise RuntimeError(
                    f"No CTranslate2 package for {source}->{target}. "
                    f"Have: {sorted(self._packages)}"
                )

    @staticmethod
    def _roots():
        """Where translation packs live: argostranslate's own install folder."""
        root = (pathlib.Path.home() / ".local" / "share"
                / "argos-translate" / "packages")
        return [root] if root.exists() else []

    @classmethod
    def _discover(cls):
        """Map (from, to) -> package directory by reading each metadata.json."""
        import json

        found = {}
        for root in cls._roots():
            for pkg in root.iterdir():
                meta = pkg / "metadata.json"
                if not meta.is_file():
                    continue
                try:
                    data = json.loads(meta.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                key = (data.get("from_code"), data.get("to_code"))
                # First root wins, so a bundled pack beats an installed one
                if None not in key and key not in found:
                    found[key] = pkg
        return found

    def _route(self, source, target):
        """Direct pair if we have it, otherwise pivot through English."""
        if source == target:
            return []
        if (source, target) in self._packages:
            return [(source, target)]
        if (source, "en") in self._packages and ("en", target) in self._packages:
            return [(source, "en"), ("en", target)]
        return None

    def _model(self, pair):
        if pair not in self._loaded:
            import ctranslate2
            import sentencepiece

            pkg = self._packages[pair]
            self._loaded[pair] = (
                ctranslate2.Translator(str(pkg / "model"), device="cpu",
                                       compute_type="int8"),
                sentencepiece.SentencePieceProcessor(
                    str(pkg / "sentencepiece.model")),
            )
        return self._loaded[pair]

    def _hop(self, text, pair):
        translator, sp = self._model(pair)
        chunks = [c for c in self.SPLIT.split(text) if c.strip()] or [text]
        results = translator.translate_batch(
            [sp.encode(c, out_type=str) for c in chunks], beam_size=4,
        )
        out = []
        for res in results:
            # decode() leaves the boundary marker on these vocabularies, so
            # join the pieces the way SentencePiece itself would
            joined = "".join(res.hypotheses[0]).replace(self.MARKER, " ")
            # The model emits <unk> for a piece it has no word for, which is
            # common on a garbled transcript. Showing it to an audience is
            # worse than showing nothing, so it never leaves the engine.
            joined = joined.replace("<unk>", "")
            out.append(re.sub(r"\s{2,}", " ", joined).strip())
        return " ".join(x for x in out if x)

    def translate(self, text, source, target):
        route = self._route(source, target)
        if route is None:
            raise RuntimeError(f"No route for {source}->{target}")
        for pair in route:
            text = self._hop(text, pair)
        return text

    def warm_up(self):
        for source, target in self._pairs:
            self.translate("warm up", source, target)


CLAUDE_MODEL = "claude-haiku-4-5-20251001"   # fast and cheap, ample for translation
CLAUDE_URL = "https://api.anthropic.com/v1/messages"
GEMINI_MODEL = "gemini-flash-latest"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
LLM_TIMEOUT = 20


def _http_json(url, payload, headers):
    body = _json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
        return _json.loads(resp.read().decode("utf-8"))


class LLMEngine(Translator):
    """Translation through a large language model the user pays for directly.

    No package to install and no model to download - the whole appeal for
    someone who already has a Claude or Gemini subscription. One HTTP call per
    phrase. Quality is well above the offline models, especially for languages
    like Kiswahili where the small local models struggle.
    """

    name = "cloud"

    def __init__(self, api_key):
        if not api_key:
            raise RuntimeError("no API key configured")
        self.api_key = api_key

    @staticmethod
    def _prompt(text, source, target):
        s = LANGUAGES.get(source, {}).get("name", source)
        t = LANGUAGES.get(target, {}).get("name", target)
        return (
            f"Translate this {s} text into {t}. It is a live caption, so keep "
            f"it natural and concise. Reply with only the translation, no "
            f"notes or quotation marks.\n\n{text}"
        )

    def translate(self, text, source, target):
        out = self._call(self._prompt(text, source, target)).strip()
        # Models sometimes wrap the answer even when told not to
        if len(out) > 1 and out[0] in "\"'“「" and out[-1] in "\"'”」":
            out = out[1:-1].strip()
        return out or text

    def _call(self, prompt):
        raise NotImplementedError

    def warm_up(self):
        # One tiny call confirms the key works before the first real phrase
        self._call("Reply with the single word: ok")


class ClaudeEngine(LLMEngine):
    name = "Claude"

    def _call(self, prompt):
        data = _http_json(CLAUDE_URL, {
            "model": CLAUDE_MODEL,
            "max_tokens": 400,
            "messages": [{"role": "user", "content": prompt}],
        }, {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        })
        return "".join(
            block.get("text", "") for block in data.get("content", [])
        )


class GeminiEngine(LLMEngine):
    name = "Gemini"

    def _call(self, prompt):
        url = f"{GEMINI_URL}/{GEMINI_MODEL}:generateContent"
        data = _http_json(url, {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 400, "temperature": 0.3},
        }, {
            "content-type": "application/json",
            "x-goog-api-key": self.api_key,
        })
        cands = data.get("candidates", [])
        if not cands:
            raise RuntimeError(data.get("promptFeedback", "no response"))
        return "".join(
            part.get("text", "")
            for part in cands[0].get("content", {}).get("parts", [])
        )


def build_cloud_engine(provider, api_key):
    if provider == "claude":
        return ClaudeEngine(api_key)
    if provider == "gemini":
        return GeminiEngine(api_key)
    raise ValueError(f"unknown cloud provider {provider!r}")


def build_engine():
    """The local translator. The only offline engine that runs on this
    machine: argostranslate itself needs PyTorch, which Smart App Control
    blocks, and the unofficial googletrans scraper no longer imports."""
    return LeanEngine(translation_pairs())
