"""Fetching the models, without downloading a gigabyte to find out."""
import io
import json
import os
import zipfile

import pytest

from livetranslator import models


class Response(io.BytesIO):
    """Enough of urlopen's answer for fetch() to read it."""

    def __init__(self, body, length=None):
        super().__init__(body)
        self.headers = {"Content-Length": str(
            len(body) if length is None else length)}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def serve(body, fail_after=None):
    """A urlopen that returns body, optionally dying part way through."""
    sent = {"requests": []}

    def opener(request, timeout=None):
        sent["requests"].append(request)
        if fail_after is None:
            return Response(body)

        class Broken(Response):
            def read(self, n=-1):
                block = super().read(n)
                if self.tell() > fail_after:
                    raise ConnectionError("the network went away")
                return block

        return Broken(body)

    sent["opener"] = opener
    return sent


def test_a_file_arrives_whole_or_not_at_all(tmp_path, monkeypatch):
    """A download cut off halfway must not leave something that looks done."""
    served = serve(b"x" * 5000, fail_after=1000)
    monkeypatch.setattr(models.urllib.request, "urlopen", served["opener"])
    dest = tmp_path / "voice.onnx"
    with pytest.raises(ConnectionError):
        models.fetch("https://example/voice.onnx", str(dest))
    assert not dest.exists(), "a half file must never take the real name"
    assert (tmp_path / "voice.onnx.part").exists(), "the part file is kept"


def test_a_finished_download_is_renamed_into_place(tmp_path, monkeypatch):
    served = serve(b"hello there")
    monkeypatch.setattr(models.urllib.request, "urlopen", served["opener"])
    dest = tmp_path / "deep" / "voice.onnx"
    models.fetch("https://example/voice.onnx", str(dest))
    assert dest.read_bytes() == b"hello there"
    assert not (tmp_path / "deep" / "voice.onnx.part").exists()


def test_progress_is_reported_against_the_real_size(tmp_path, monkeypatch):
    served = serve(b"y" * (models.CHUNK * 2 + 17))
    monkeypatch.setattr(models.urllib.request, "urlopen", served["opener"])
    seen = []
    models.fetch("https://example/v.bin", str(tmp_path / "v.bin"),
                 on_progress=lambda name, done, total: seen.append((done, total)))
    assert seen, "a long download with no progress looks like a hang"
    assert seen[-1][0] == seen[-1][1] == models.CHUNK * 2 + 17
    assert [d for d, _t in seen] == sorted(d for d, _t in seen)


def test_the_download_claims_to_be_a_browser(tmp_path, monkeypatch):
    """argos-net.com answers 403 to urllib's own user agent."""
    served = serve(b"data")
    monkeypatch.setattr(models.urllib.request, "urlopen", served["opener"])
    models.fetch("https://argos-net.com/v1/x.argosmodel", str(tmp_path / "x"))
    agent = served["requests"][0].get_header("User-agent")
    assert agent and "Mozilla" in agent


def test_a_pack_is_unzipped_and_the_archive_thrown_away(tmp_path, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("translate-en_zt-1_9/metadata.json",
                    json.dumps({"from_code": "en", "to_code": "zt"}))
        zf.writestr("translate-en_zt-1_9/model/model.bin", "weights")
    served = serve(buf.getvalue())
    monkeypatch.setattr(models.urllib.request, "urlopen", served["opener"])

    got = models.install_pack(
        "https://argos-net.com/v1/translate-en_zt-1_9.argosmodel", str(tmp_path))
    assert got == ["translate-en_zt-1_9"]
    assert (tmp_path / "translate-en_zt-1_9" / "metadata.json").is_file()
    assert not list(tmp_path.glob("*.argosmodel")), "the zip is not left behind"


def test_what_is_missing_is_worked_out_from_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "TTS_DIR", str(tmp_path / "tts"))
    monkeypatch.setattr(models, "packs_missing", lambda: [("en", "zt")])
    voices, packs, size = models.missing()
    assert voices == [name for name, _s in models.VOICE_FILES]
    assert packs == [("en", "zt")]
    assert size > 300e6, "the estimate should be in the right order"

    os.makedirs(models.voice_dir())
    for name, _s in models.VOICE_FILES:
        open(os.path.join(models.voice_dir(), name), "wb").close()
    voices, _p, _s = models.missing()
    assert voices == [], "files that are there are not fetched again"


def test_installing_again_downloads_nothing(monkeypatch):
    """Running the installer twice must not re-fetch a gigabyte."""
    monkeypatch.setattr(models, "voice_missing", lambda: [])
    monkeypatch.setattr(models, "packs_missing", lambda: [])
    monkeypatch.setattr(models, "fetch", lambda *a, **k: pytest.fail(
        "nothing should be downloaded"))
    assert models.install() is True


def test_a_pack_with_no_download_listed_is_survived(monkeypatch, tmp_path):
    """One missing entry in the index must not abandon the rest."""
    monkeypatch.setattr(models, "voice_missing", lambda: [])
    calls = {"n": 0}
    monkeypatch.setattr(models, "packs_missing",
                        lambda: [("en", "zt"), ("zt", "en")])
    monkeypatch.setattr(models, "pack_links",
                        lambda: {("zt", "en"): "https://example/z.argosmodel"})
    monkeypatch.setattr(models, "install_pack",
                        lambda url, root: calls.__setitem__("n", calls["n"] + 1))
    models.install()
    assert calls["n"] == 1, "the pack that was listed still installs"


def test_every_wanted_pair_is_reachable_from_english():
    """English is the pivot, so these four must cover the language menu."""
    from livetranslator.config import LANGUAGES, PIVOT_LANG

    for code in LANGUAGES:
        if code == PIVOT_LANG:
            continue
        assert (PIVOT_LANG, code) in models.WANTED, f"nothing translates into {code}"
        assert (code, PIVOT_LANG) in models.WANTED, f"nothing translates from {code}"


@pytest.mark.network
def test_the_index_really_lists_every_pack():
    """A renamed pack in the index would strand a language silently."""
    try:
        links = models.pack_links()
    except Exception as e:
        pytest.skip(f"no network: {type(e).__name__}")
    for pair in models.WANTED:
        assert pair in links, f"{pair} is not in the Argos index any more"
        assert links[pair].endswith(".argosmodel")
