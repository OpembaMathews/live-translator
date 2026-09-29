"""Fetching the models, without downloading a gigabyte to find out."""
import io
import json
import os
import zipfile

import pytest

from livetranslator import models


class Response(io.BytesIO):
    """Enough of urlopen's answer for fetch() to read it.

    status matters: 206 means the server honoured a Range request and the
    part file can be carried on; 200 means it sent the whole thing and what
    was already there is no use.
    """

    def __init__(self, body, length=None, status=200):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Length": str(
            len(body) if length is None else length)}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def serve(body, fail_after=None, status=200):
    """A urlopen that returns body, optionally dying part way through."""
    sent = {"requests": []}

    def opener(request, timeout=None):
        sent["requests"].append(request)
        if fail_after is None:
            return Response(body, status=status)

        class Broken(Response):
            def read(self, n=-1):
                block = super().read(n)
                if self.tell() > fail_after:
                    raise ConnectionError("the network went away")
                return block

        return Broken(body, status=status)

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
    assert voices == [name for name, _s, _h in models.VOICE_FILES]
    assert packs == [("en", "zt")]
    assert size > 300e6, "the estimate should be in the right order"

    os.makedirs(models.voice_dir())
    for name, _s, _h in models.VOICE_FILES:
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
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
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


# --- what a person watching actually sees ---------------------------------
def test_progress_is_across_the_whole_download_not_each_file(monkeypatch):
    """A bar that ran nought to a hundred six times answered nothing."""
    monkeypatch.setattr(models, "voice_missing", lambda: ["a.onnx", "b.bin"])
    monkeypatch.setattr(models, "packs_missing", lambda: [])
    monkeypatch.setattr(models, "VOICE_FILES",
                        (("a.onnx", 300, ""), ("b.bin", 100, "")))
    monkeypatch.setattr(models, "check", lambda *a: True)

    def pretend(url, dest, on_progress=None, expected=0, stop=None):
        for got in (expected // 2, expected):
            on_progress(os.path.basename(dest), got, expected)

    monkeypatch.setattr(models, "fetch", pretend)
    seen = []
    models.install(on_progress=lambda n, got, total: seen.append((got, total)))

    assert [t for _g, t in seen] == [400] * len(seen), "the total must not move"
    assert [g for g, _t in seen] == sorted(g for g, _t in seen), \
        "the bar must never go backwards"
    assert seen[-1][0] == 400, "it has to reach the end"


def test_each_step_says_where_it_is_in_the_queue(monkeypatch):
    monkeypatch.setattr(models, "voice_missing", lambda: ["a.onnx"])
    monkeypatch.setattr(models, "packs_missing", lambda: [("en", "zt")])
    monkeypatch.setattr(models, "VOICE_FILES", (("a.onnx", 10, ""),))
    monkeypatch.setattr(models, "check", lambda *a: True)
    monkeypatch.setattr(models, "fetch", lambda *a, **k: None)
    monkeypatch.setattr(models, "pack_links",
                        lambda: {("en", "zt"): "https://x/p.argosmodel"})
    monkeypatch.setattr(models, "install_pack", lambda *a, **k: None)
    steps = []
    models.install(on_step=steps.append)
    assert any("1 of 2" in s for s in steps)
    assert any("2 of 2" in s for s in steps)


def test_the_plain_line_ends_in_a_percentage(monkeypatch, capsys):
    """The installer reads the figure off the end of the line to drive its
    bar, so the shape of this line is a contract, not a convenience."""
    monkeypatch.setattr(models, "watching", lambda: False)
    monkeypatch.setattr(models, "voice_missing", lambda: ["a.onnx"])
    monkeypatch.setattr(models, "packs_missing", lambda: [])
    monkeypatch.setattr(models, "VOICE_FILES", (("a.onnx", 100, ""),))
    monkeypatch.setattr(models, "check", lambda *a: True)

    def pretend(url, dest, on_progress=None, expected=0, stop=None):
        for got in (10, 50, 100):
            on_progress("a.onnx (1 of 1)", got, expected)

    monkeypatch.setattr(models, "fetch", pretend)
    models.main()
    lines = [l for l in capsys.readouterr().out.splitlines() if l.endswith("%")]
    assert lines, "nothing the installer could read"
    for line in lines:
        figure = line.rsplit(":", 1)[-1].strip().rstrip("%")
        assert figure.isdigit(), f"not parseable: {line!r}"
        assert 0 <= int(figure) <= 100


# --- stopping, and carrying on later --------------------------------------
def test_a_part_file_is_carried_on_rather_than_started_again(tmp_path, monkeypatch):
    """Pausing is only worth offering if it does not throw the bytes away."""
    dest = tmp_path / "voice.bin"
    (tmp_path / "voice.bin.part").write_bytes(b"first half")
    served = serve(b"second half", status=206)
    monkeypatch.setattr(models.urllib.request, "urlopen", served["opener"])

    models.fetch("https://example/voice.bin", str(dest), expected=21)
    assert dest.read_bytes() == b"first halfsecond half"
    asked = served["requests"][0].get_header("Range")
    assert asked == "bytes=10-", "the server was not asked to carry on"


def test_a_server_that_ignores_the_range_starts_clean(tmp_path, monkeypatch):
    """A 200 means the whole file is coming, so what was there is no use."""
    dest = tmp_path / "voice.bin"
    (tmp_path / "voice.bin.part").write_bytes(b"stale rubbish")
    served = serve(b"the whole thing", status=200)
    monkeypatch.setattr(models.urllib.request, "urlopen", served["opener"])

    models.fetch("https://example/voice.bin", str(dest))
    assert dest.read_bytes() == b"the whole thing", "stale bytes were kept"


def test_stopping_keeps_what_arrived(tmp_path, monkeypatch):
    import threading

    stop = threading.Event()
    stop.set()                                    # as if Pause were pressed
    served = serve(b"x" * 5000)
    monkeypatch.setattr(models.urllib.request, "urlopen", served["opener"])
    dest = tmp_path / "voice.bin"

    with pytest.raises(models.Stopped):
        models.fetch("https://example/voice.bin", str(dest), stop=stop)
    assert not dest.exists(), "a stopped download is not a finished one"
    assert (tmp_path / "voice.bin.part").exists(), "what arrived must be kept"


def test_the_speed_is_measured_over_a_window_not_from_the_start():
    class Clock:
        def __init__(self):
            self.t = 0.0

    clock = Clock()
    speed = models.Speed(clock=lambda: clock.t)
    for _ in range(3):                            # a slow start
        clock.t += 1.0
        speed.note(int(clock.t * 10_000))
    slow = speed.rate
    for _ in range(12):                           # then it picks up
        clock.t += 1.0
        speed.note(int(30_000 + clock.t * 2_000_000))
    assert speed.rate > slow * 10, "the early crawl should stop counting"
    assert not speed.slow


def test_a_crawl_is_called_slow_and_an_estimate_stays_sensible():
    class Clock:
        def __init__(self):
            self.t = 0.0

    clock = Clock()
    speed = models.Speed(clock=lambda: clock.t)
    for _ in range(10):
        clock.t += 1.0
        speed.note(int(clock.t * 20_000))         # 20 KB/s
    assert speed.slow
    said = speed.describe(200_000, 650_000_000)
    assert "hours" in said, said
    assert "min" not in said, "days-long estimates are not information"


# --- the right file, not just a file --------------------------------------
def test_the_voice_comes_from_the_release_that_reports_durations():
    """Both Kokoro releases ship a kokoro-v1.0.onnx and they differ. Only
    the v1.1 build says how long each sound lasts, and without that the
    reader cannot highlight what it is reading, so it will not start."""
    assert "model-files-v1.1" in models.KOKORO
    assert "model-files-v1.0/" not in models.KOKORO


def test_every_voice_file_is_pinned_by_size_and_checksum():
    for name, size, sha in models.VOICE_FILES:
        assert size > 0, f"{name} has no size to check against"
        assert len(sha) == 64, f"{name} has no checksum"


def test_a_file_of_the_wrong_size_is_refused(tmp_path):
    """27 KB different in 325 MB is what tells the two releases apart."""
    path = tmp_path / "kokoro.onnx"
    path.write_bytes(b"x" * 500)
    with pytest.raises(ValueError, match="wrong file was served"):
        models.check(str(path), 325_505_369, "")
    assert not path.exists(), "a wrong file must not be left to be used"


def test_a_damaged_file_is_refused(tmp_path):
    path = tmp_path / "voices.bin"
    path.write_bytes(b"hello")
    with pytest.raises(ValueError, match="did not arrive intact"):
        models.check(str(path), 5, "0" * 64)
    assert not path.exists()


def test_the_right_file_passes(tmp_path):
    import hashlib

    path = tmp_path / "voices.bin"
    body = b"the real thing"
    path.write_bytes(body)
    assert models.check(str(path), len(body), hashlib.sha256(body).hexdigest())
    assert path.exists()


@pytest.mark.network
def test_the_release_really_serves_what_is_pinned():
    """If the release is ever re-cut, this fails here rather than on a
    tester's machine with a message about durations."""
    import urllib.request

    for name, size, _sha in models.VOICE_FILES:
        request = urllib.request.Request(models.KOKORO + name,
                                         headers={**models.AGENT,
                                                  "Range": "bytes=0-1023"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                rng = response.headers.get("Content-Range", "")
        except Exception as e:
            pytest.skip(f"no network: {type(e).__name__}")
        served = int(rng.split("/")[-1]) if "/" in rng else 0
        assert served == size, f"{name} is now {served:,}, pinned at {size:,}"
