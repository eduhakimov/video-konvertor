"""Oyna orqali to'liq jarayon: fayl qo'shish -> andoza -> Boshlash -> natija (vaqtinchalik papkada)."""
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
def win(tmp_path):
    from PySide6.QtWidgets import QApplication
    from uzkit import app as appmod, i18n
    from uzkit.settings import Settings
    appmod.META = appmod.load_meta()
    appmod.SETTINGS = Settings(appmod.META["slug"] + "_test")
    qapp = QApplication.instance() or QApplication([])
    i18n.set_language("uz")
    from video_konvertor.window import MainWindow
    w = MainWindow()
    yield qapp, w
    w.close()


def test_queue_audio_and_compress(win, tmp_path):
    from video_konvertor import ffbin
    qapp, w = win
    src = tmp_path / "klip.mp4"
    r = ffbin.run([ffbin.ffmpeg(), "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-f", "lavfi", "-i",
                   "sine=frequency=440", "-t", "2", "-c:v", "libopenh264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                   "-shortest", str(src)])
    assert r.returncode == 0
    page = w.convert
    page.out_dir.set_path(str(tmp_path / "out"))
    page.add_files([str(src), str(src)])           # takror qo'shilmaydi
    assert len(page.items) == 1
    _wait(qapp, lambda: page.items[0].info is not None)
    for preset in ("audio", "compress"):
        page.preset.setCurrentIndex(page.preset.findData(preset))
        page.start_queue()
        _wait(qapp, lambda: page.thread is None, 120)
        assert page.items[0].status == "st_done", page.items[0].message
    outs = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert outs == ["klip_audio.mp3", "klip_compressed.mp4"]


def test_bad_time_does_not_crash(win, monkeypatch):
    qapp, w = win
    import video_konvertor.window as W
    shown = []
    monkeypatch.setattr(W, "warn", lambda parent, text, title="": shown.append(text))
    w.convert.start.setText("abc")
    w.convert.items.clear()
    w.convert.start_queue()
    assert shown
