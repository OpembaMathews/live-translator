"""Keeping approved translations, and collecting the ones still wanted."""
import json

import pytest

from livetranslator.reader import memory
from livetranslator.reader.memory import Memory, write_wanted

EN = "Older adults selected technology intentionally."
ZH = "年長者有意選擇科技。"


@pytest.fixture
def store(tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_text("{}", encoding="utf-8")
    return Memory(path=str(tmp_path / "mine.json"), seed=str(seed))


def test_an_approved_translation_comes_back(store):
    store.put(EN, "en", "zt", ZH)
    assert store.get(EN, "en", "zt") == ZH


def test_spacing_does_not_stop_a_match(store):
    """The same sentence set differently is still the same sentence."""
    store.put(EN, "en", "zt", ZH)
    assert store.get("Older adults   selected\ntechnology intentionally.",
                     "en", "zt") == ZH


def test_a_different_language_pair_is_a_different_answer(store):
    store.put(EN, "en", "zt", ZH)
    assert store.get(EN, "en", "sw") is None


def test_an_empty_translation_is_refused(store):
    assert not store.put(EN, "en", "zt", "   ")
    assert store.get(EN, "en", "zt") is None


def test_it_survives_a_restart(tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_text("{}", encoding="utf-8")
    path = str(tmp_path / "mine.json")
    first = Memory(path=path, seed=str(seed))
    first.put(EN, "en", "zt", ZH)
    assert first.save()
    assert Memory(path=path, seed=str(seed)).get(EN, "en", "zt") == ZH


def test_the_file_is_editable_by_hand(tmp_path):
    """The format is the interface, so it has to be readable and sorted."""
    seed = tmp_path / "seed.json"
    seed.write_text("{}", encoding="utf-8")
    path = tmp_path / "mine.json"
    store = Memory(path=str(path), seed=str(seed))
    store.put("Second sentence.", "en", "zt", "二。")
    store.put("First sentence.", "en", "zt", "一。")
    store.save()
    written = path.read_text(encoding="utf-8")
    assert "一。" in written, "Chinese must not be escaped away"
    assert written.index("First") < written.index("Second"), "sorted"
    assert json.loads(written)["en>zt"]["First sentence."] == "一。"


def test_a_broken_file_does_not_stop_the_reading(tmp_path):
    bad = tmp_path / "mine.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    seed = tmp_path / "seed.json"
    seed.write_text("{}", encoding="utf-8")
    assert len(Memory(path=str(bad), seed=str(seed))) == 0


def test_the_seed_ships_and_the_local_file_wins(tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"en>zt": {EN: "舊。"}}), encoding="utf-8")
    path = tmp_path / "mine.json"
    path.write_text(json.dumps({"en>zt": {EN: ZH}}), encoding="utf-8")
    assert Memory(path=str(path), seed=str(seed)).get(EN, "en", "zt") == ZH


def test_saving_does_not_copy_the_seed_into_the_local_file(tmp_path):
    """The shipped entries belong to the app, not to this machine."""
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"en>zt": {EN: ZH}}), encoding="utf-8")
    path = tmp_path / "mine.json"
    store = Memory(path=str(path), seed=str(seed))
    store.put("Something new.", "en", "zt", "新。")
    store.save()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert EN not in saved["en>zt"]
    assert saved["en>zt"]["Something new."] == "新。"


def test_wanted_sentences_are_written_in_the_memory_format(tmp_path):
    """So correcting one is replacing the text and moving the file."""
    out = write_wanted("aging-2025.pdf", [(EN, "年長者,")],
                       "en", "zt", folder=str(tmp_path))
    body = json.loads(open(out, encoding="utf-8").read())
    assert body["en>zt"][EN] == "年長者,"
    assert out.endswith("aging-2025.pdf.json"), out


def test_nothing_is_written_when_nothing_is_wanted(tmp_path):
    assert write_wanted("paper.pdf", [], "en", "zt", folder=str(tmp_path)) is None
    assert not list(tmp_path.iterdir())


def test_the_reader_asks_the_memory_before_the_engine(tmp_path):
    from livetranslator.reader.player import Passage
    from livetranslator.reader.translated import Translation

    class Engine:
        def __init__(self):
            self.asked = []

        def translate(self, text, source, target):
            self.asked.append(text)
            return "機器。"

    seed = tmp_path / "seed.json"
    seed.write_text("{}", encoding="utf-8")
    store = Memory(path=str(tmp_path / "mine.json"), seed=str(seed))
    store.put(EN, "en", "zt", ZH)
    engine = Engine()
    into = Translation("zt", engine, memory=store)
    item = Passage(0, EN, EN, 0, (0, 0, 1, 1), "text")
    assert into.text_for(item) == ZH
    assert engine.asked == [], "the engine must not be asked what is known"


def test_machine_output_is_never_written_to_the_memory(tmp_path):
    """A memory that keeps guesses preserves mistakes, and nothing rechecks it."""
    from livetranslator.reader.player import Passage
    from livetranslator.reader.translated import Translation

    class Engine:
        def translate(self, text, source, target):
            return "機器。"

    seed = tmp_path / "seed.json"
    seed.write_text("{}", encoding="utf-8")
    store = Memory(path=str(tmp_path / "mine.json"), seed=str(seed))
    into = Translation("zt", Engine(), memory=store)
    into.text_for(Passage(0, EN, EN, 0, (0, 0, 1, 1), "text"))
    assert len(store) == 0


def test_the_shipped_seed_is_valid(tmp_path):
    """It is loaded at every start, so a typo in it breaks translation."""
    from livetranslator.paths import MEMORY_SEED

    body = memory.read(MEMORY_SEED)
    assert isinstance(body, dict)
    for route, sentences in body.items():
        assert ">" in route, f"{route} is not a language pair"
        assert isinstance(sentences, dict)
