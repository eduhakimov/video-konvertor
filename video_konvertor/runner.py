"""ffmpeg jarayonini ishga tushirish, `-progress pipe:1` dan foizni o'qish, bekor qilish."""
import os
import shutil
import subprocess
import threading
from typing import Callable, List, Optional

from uzkit.widgets import no_window_flags


def parse_progress_line(line: str, state: dict) -> Optional[float]:
    """Bitta `key=value` qatorni o'qiydi. Vaqt (soniya) aniqlansa qaytaradi."""
    line = line.strip()
    if "=" not in line:
        return None
    k, v = line.split("=", 1)
    state[k] = v
    if k in ("out_time_us", "out_time_ms"):
        try:
            return max(0.0, int(v) / 1_000_000)
        except ValueError:
            return None
    if k == "out_time" and v and v[0].isdigit():
        try:
            h, m, s = v.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
        except ValueError:
            return None
    return None


class Cancelled(Exception):
    pass


class FFmpegRun:
    """Bitta ffmpeg buyrug'ini bajaradi. cancel() boshqa oqimdan chaqirilishi mumkin."""

    def __init__(self):
        self.proc: Optional[subprocess.Popen] = None
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()
        p = self.proc
        if p and p.poll() is None:
            try:
                p.stdin and p.stdin.close()
            except Exception:
                pass
            try:
                p.kill()
            except Exception:
                pass

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self, cmd: List[str], duration: float, on_progress: Callable[[float], None] = None) -> str:
        """Muvaffaqiyatda '' qaytaradi, xatoda ffmpeg xabari (stderr). Bekor qilinsa Cancelled."""
        if self.cancelled:
            raise Cancelled()
        self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     creationflags=no_window_flags())
        err_chunks: List[bytes] = []

        def read_err():
            for chunk in iter(lambda: self.proc.stderr.read(4096), b""):
                err_chunks.append(chunk)
        t = threading.Thread(target=read_err, daemon=True)
        t.start()
        state: dict = {}
        for raw in iter(self.proc.stdout.readline, b""):
            sec = parse_progress_line(raw.decode("utf-8", "replace"), state)
            if sec is not None and duration > 0 and on_progress:
                on_progress(min(100.0, sec * 100.0 / duration))
            if self.cancelled:
                break
        self.proc.wait()
        t.join(timeout=2)
        if self.cancelled:
            raise Cancelled()
        if self.proc.returncode != 0:
            return b"".join(err_chunks).decode("utf-8", "replace").strip()[-2000:] or f"exit {self.proc.returncode}"
        if on_progress:
            on_progress(100.0)
        return ""


def remove_partial(path: str):
    """Bekor qilingan/xato ishning chala chiqish faylini o'chiradi (faqat o'zimiz yaratgan fayl)."""
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def run_with_fallbacks(cmds: List[List[str]], out: str, duration: float, runner: FFmpegRun,
                       on_progress=None, on_attempt=None) -> str:
    """Buyruqlarni navbat bilan sinaydi; birinchisi muvaffaqiyatli bo'lsa ''. Aks holda oxirgi xato matni."""
    err = "no command"
    for i, c in enumerate(cmds):
        if on_attempt:
            on_attempt(i, c)
        err = runner.run(c, duration, on_progress)
        if not err:
            return ""
        remove_partial(out) if not os.path.isdir(out) else None
    return err
