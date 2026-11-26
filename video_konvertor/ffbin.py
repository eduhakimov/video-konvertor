"""ffmpeg / ffprobe ni topish, ishga tushirish va mavjud H.264 kodlovchini aniqlash."""
import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from uzkit.paths import exe_dir, resource
from uzkit.widgets import no_window_flags

_PROJECT = Path(__file__).resolve().parents[1]


def _candidates(name: str):
    yield resource("ffmpeg", name)                       # exe ichida (PyInstaller)
    yield exe_dir() / "ffmpeg" / name                    # exe yonida
    yield _PROJECT / "vendor" / "ffmpeg" / name          # loyiha vendor/
    yield _PROJECT.parent / "_resurslar" / "ffmpeg" / name   # ishlab chiqish muhiti


def find_tool(name: str) -> Optional[str]:
    for p in _candidates(name):
        if p.exists():
            return str(p)
    w = shutil.which(name)
    return w


def ffmpeg() -> str:
    return find_tool("ffmpeg.exe") or "ffmpeg"


def ffprobe() -> str:
    return find_tool("ffprobe.exe") or "ffprobe"


def run(cmd: List[str], timeout: float = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=no_window_flags())


def probe_json(path: str) -> dict:
    r = run([ffprobe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path], timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "ffprobe error")
    return json.loads(r.stdout or "{}")


# H.264 kodlovchilar: tezkor (apparat) birinchi, keyin dasturiy.
H264_ORDER = ["h264_nvenc", "h264_amf", "h264_qsv", "h264_mf", "libopenh264"]
SOFTWARE_H264 = "libopenh264"
_encoders_cache: Optional[List[str]] = None


def encoder_works(name: str) -> bool:
    try:
        r = run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                 "color=c=black:s=256x144:r=25", "-t", "0.2", "-pix_fmt", "yuv420p",
                 "-c:v", name, "-b:v", "300k", "-f", "null", "-"], timeout=20)
        return r.returncode == 0
    except Exception:
        return False


def available_h264(refresh: bool = False) -> List[str]:
    """Shu kompyuterda haqiqatan ishlaydigan H.264 kodlovchilar ro'yxati (tartib bo'yicha)."""
    global _encoders_cache
    if _encoders_cache is None or refresh:
        _encoders_cache = [e for e in H264_ORDER if encoder_works(e)] or [SOFTWARE_H264]
    return list(_encoders_cache)


def best_h264() -> str:
    return available_h264()[0]
