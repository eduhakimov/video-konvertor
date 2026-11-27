"""Chuqur ssenariylar: manba ustiga yozmaslik, chegaraviy vaqtlar, buzilgan sozlamalar, bekor qilish."""
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _wait(qapp, cond, timeout=60):
    t = time.time()
    while not cond():
        qapp.processEvents()
        time.sleep(0.02)
        if time.time() - t > timeout:
            raise TimeoutError
    qapp.processEvents()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    from uzkit import app as appmod, i18n
    from uzkit.settings import Settings
    appmod.META = appmod.load_meta()
    appmod.SETTINGS = Settings(appmod.META["slug"])
    qapp = QApplication.instance() or QApplication([])
    i18n.set_language("uz")
    import video_konvertor.window as W
    msgs = []
    for n in ("error", "warn", "info"):
        monkeypatch.setattr(W, n, (lambda n: lambda *a, **k: msgs.append((n, a[1] if len(a) > 1 else "")))(n))
    monkeypatch.setattr(W, "open_path", lambda p: None)
    src = tmp_path / "Ўзбек 'video'"
    src.mkdir()
    return qapp, W, msgs, src


def _gen(path, *args):
    from video_konvertor import ffbin
    r = ffbin.run([ffbin.ffmpeg(), "-y", "-loglevel", "error", *args, str(path)])
    assert r.returncode == 0, r.stderr
    return str(path)


def test_preset_guards():
    from video_konvertor import presets as P
    from video_konvertor.media import MediaInfo
    audio_only = MediaInfo(duration=3, has_audio=True, acodec="mp3")
    with pytest.raises(ValueError, match="no_video"):
        P.build_commands(P.Job(preset="noaudio"), "a.mp3", audio_only, "o.mp3")
    clip = MediaInfo(duration=4, has_video=True, width=320, height=240)
    with pytest.raises(ValueError, match="start_beyond"):
        P.build_commands(P.Job(preset="trim", start=100), "a.mp4", clip, "o.mp4")


def test_merge_never_overwrites_input(env):
    qapp, W, msgs, src = env
    a = _gen(src / "a ў.mp4", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-t", "1",
             "-c:v", "libopenh264", "-pix_fmt", "yuv420p")
    b = _gen(src / "b.mp4", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-t", "1",
             "-c:v", "libopenh264", "-pix_fmt", "yuv420p")
    w = W.MainWindow()
    m = w.merge
    m.add_files([a, b])
    _wait(qapp, lambda: m.list.count() == 2)
    size = os.path.getsize(a)
    m.out.set_path(a)
    m.go()
    assert m.thread is None and msgs[-1][0] == "warn"
    assert os.path.getsize(a) == size
    out = src / "natija.mp4"
    m.out.set_path(str(out))
    m.go()
    _wait(qapp, lambda: m.thread is None)
    assert out.stat().st_size > 1000
    w.close()


def test_cancel_and_bad_out_dir(env, tmp_path):
    qapp, W, msgs, src = env
    v = _gen(src / "uzun.mp4", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30", "-t", "40",
             "-c:v", "libopenh264", "-pix_fmt", "yuv420p")
    w = W.MainWindow()
    pg = w.convert
    pg.add_files([v])
    _wait(qapp, lambda: pg.items[0].info is not None)
    pg.out_dir.set_path(str(tmp_path / "Q" / "yoq" / "\0bad"))
    pg.start_queue()
    assert msgs and msgs[-1][0] == "error" and pg.thread is None
    out = tmp_path / "out"
    pg.out_dir.set_path(str(out))
    pg.preset.setCurrentIndex(pg.preset.findData("compress"))
    pg.start_queue()
    pg.start_queue()                     # takroriy bosish: ikkinchi navbat ochilmaydi
    time.sleep(0.6)
    pg.cancel()
    _wait(qapp, lambda: pg.thread is None, 30)
    assert pg.items[0].status == "st_cancelled"
    assert not out.exists() or not list(out.iterdir())
    w.close()


def test_corrupt_settings(env):
    from uzkit import app as appmod
    from uzkit.paths import data_dir
    from uzkit.settings import Settings
    qapp, W, msgs, src = env
    (data_dir("video-konvertor") / "settings.json").write_text(json.dumps(
        {"job": {"quality": "x", "preset": 5, "height": "big", "frame_every": "z", "copy": "no"},
         "out_dir": 5, "encoder": [1]}), encoding="utf-8")
    appmod.SETTINGS = Settings("video-konvertor")
    w = W.MainWindow()
    assert w.convert.quality.value() == 60
    w.close()
