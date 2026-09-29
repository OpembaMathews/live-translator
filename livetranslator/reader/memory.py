"""Translations worth keeping, and the sentences still waiting for one.

The offline packs get roughly half a journal sentence's meaning across, and
about one sentence in seven cannot be mended at all. No amount of tuning
fixes that, so the remaining route is to record the right answer once and
use it for ever after.

Two rules make this safe:

  * Only a translation a person has approved goes in. Storing what the
    machine produced would simply preserve its mistakes, and a memory that
    lies is worse than no memory, because nothing re-checks it.
  * The file is plain JSON, sorted and indented, so it can be corrected in
    a text editor by someone who reads the language. That is the whole
    interface -- there is no clever format to learn.

A seed file ships with the app and a second file holds what this machine
has learned since; the local one wins where both have an answer.
"""
import json
import os
import re

from ..log import log
from ..paths import CORRECTIONS_DIR, MEMORY_PATH, MEMORY_SEED

WHITESPACE = re.compile(r"\s+")


def pair(source, target):
    return f"{source}>{target}"


def key(text):
    """What counts as the same sentence: spacing and edge punctuation vary
    between one printing of a paper and another, the words do not."""
    return WHITESPACE.sub(" ", text).strip()


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        # A memory that will not parse must not stop the reading
        log(f"memory: cannot read {path}: {type(e).__name__}: {e}")
        return {}
    return data if isinstance(data, dict) else {}


class Memory:
    """Approved translations, looked up by sentence."""

    def __init__(self, path=None, seed=None):
        self.path = path or MEMORY_PATH
        self.seed = seed or MEMORY_SEED
        self.entries = {}
        for source in (self.seed, self.path):
            for route, sentences in read(source).items():
                if isinstance(sentences, dict):
                    self.entries.setdefault(route, {}).update(
                        {key(k): v for k, v in sentences.items()
                         if isinstance(v, str) and v.strip()})
        self.hits = 0

    def __len__(self):
        return sum(len(v) for v in self.entries.values())

    def get(self, text, source, target):
        found = self.entries.get(pair(source, target), {}).get(key(text))
        if found:
            self.hits += 1
        return found

    def put(self, text, source, target, translation):
        """Record an approved translation. Not for machine output."""
        if not translation or not translation.strip():
            return False
        self.entries.setdefault(pair(source, target), {})[key(text)] = \
            translation.strip()
        return True

    def save(self):
        """Write what this machine has learned, keeping the seed out of it."""
        seeded = read(self.seed)
        mine = {}
        for route, sentences in self.entries.items():
            shipped = {key(k): v for k, v in seeded.get(route, {}).items()}
            keep = {k: v for k, v in sentences.items() if shipped.get(k) != v}
            if keep:
                mine[route] = keep
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(mine, f, ensure_ascii=False, indent=2, sort_keys=True)
        except OSError as e:
            log(f"memory: cannot write {self.path}: {type(e).__name__}: {e}")
            return False
        return True


def write_wanted(name, wanted, source, target, folder=None):
    """Save the sentences that did not translate cleanly, ready to correct.

    The file is the memory's own format with the machine's attempt in place
    of the answer, so correcting it is a matter of replacing the text on the
    right and moving the file alongside the memory -- no conversion step and
    nothing to explain.
    """
    if not wanted:
        return None
    folder = folder or CORRECTIONS_DIR
    safe = re.sub(r"[^\w.-]+", "-", name).strip("-") or "paper"
    path = os.path.join(folder, f"{safe}.json")
    body = {pair(source, target): {key(en): zh for en, zh in wanted}}
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=2, sort_keys=True)
    except OSError as e:
        log(f"memory: cannot write {path}: {type(e).__name__}: {e}")
        return None
    log(f"memory: {len(wanted)} sentences to correct saved to {path}")
    return path
