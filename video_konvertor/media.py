"""ffprobe natijasini qulay MediaInfo obyektiga aylantirish."""
import os
from dataclasses import dataclass, field
from typing import List, Optional

from . import ffbin


@dataclass
class MediaInfo:
    path: str = ""
    duration: float = 0.0          # soniya
    size: int = 0                  # bayt
    bitrate: int = 0               # bit/s (umumiy)
    container: str = ""
    has_video: bool = False
    width: int = 0
    height: int = 0
    fps: float = 0.0
    vcodec: str = ""
    vbitrate: int = 0
    pix_fmt: str = ""
    rotation: int = 0
    has_audio: bool = False
    acodec: str = ""
    abitrate: int = 0
    sample_rate: int = 0
    channels: int = 0
    streams: List[dict] = field(default_factory=list)

    @property
    def display_size(self):
        """Burilishni hisobga olgan holda ko'rinadigan o'lcham."""
        if self.rotation in (90, 270, -90, -270):
            return self.height, self.width
        return self.width, self.height


def _fps(rate: str) -> float:
    try:
        if "/" in rate:
            a, b = rate.split("/")
            return float(a) / float(b) if float(b) else 0.0
        return float(rate)
    except Exception:
        return 0.0


def _int(v, default=0) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


def parse_probe(data: dict, path: str = "") -> MediaInfo:
    fmt = data.get("format", {}) or {}
    mi = MediaInfo(path=path)
    mi.duration = float(fmt.get("duration") or 0) if str(fmt.get("duration", "")).replace(".", "", 1).isdigit() else 0.0
    mi.size = _int(fmt.get("size"))
    mi.bitrate = _int(fmt.get("bit_rate"))
    mi.container = fmt.get("format_name", "")
    mi.streams = data.get("streams", []) or []
    for s in mi.streams:
        t = s.get("codec_type")
        if t == "video" and not mi.has_video and s.get("disposition", {}).get("attached_pic", 0) != 1:
            mi.has_video = True
            mi.width, mi.height = _int(s.get("width")), _int(s.get("height"))
            mi.fps = _fps(s.get("avg_frame_rate") or s.get("r_frame_rate") or "0")
            if mi.fps <= 0 or mi.fps > 1000:
                mi.fps = _fps(s.get("r_frame_rate") or "0")
            mi.vcodec = s.get("codec_name", "")
            mi.vbitrate = _int(s.get("bit_rate"))
            mi.pix_fmt = s.get("pix_fmt", "")
            rot = _int((s.get("tags") or {}).get("rotate"))
            for sd in s.get("side_data_list", []) or []:
                if "rotation" in sd:
                    rot = _int(sd.get("rotation"))
            mi.rotation = rot % 360 if rot else 0
            if not mi.duration:
                mi.duration = float(s.get("duration") or 0)
        elif t == "audio" and not mi.has_audio:
            mi.has_audio = True
            mi.acodec = s.get("codec_name", "")
            mi.abitrate = _int(s.get("bit_rate"))
            mi.sample_rate = _int(s.get("sample_rate"))
            mi.channels = _int(s.get("channels"))
            if not mi.duration:
                mi.duration = float(s.get("duration") or 0)
    if not mi.size and path and os.path.exists(path):
        mi.size = os.path.getsize(path)
    if not mi.bitrate and mi.duration and mi.size:
        mi.bitrate = int(mi.size * 8 / mi.duration)
    if mi.has_video and not mi.vbitrate and mi.bitrate:
        mi.vbitrate = max(0, mi.bitrate - (mi.abitrate or 128000 if mi.has_audio else 0))
    return mi


def probe(path: str) -> MediaInfo:
    return parse_probe(ffbin.probe_json(path), path)


def fmt_duration(sec: float) -> str:
    if not sec or sec < 0:
        return "—"
    sec = float(sec)
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    frac = sec - int(sec)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}" + (f".{int(frac * 10)}" if sec < 60 and frac >= 0.1 else "")


def parse_time(text: str) -> Optional[float]:
    """'90', '1:30', '00:01:30.5', '1,5' -> soniya. Bo'sh -> None. Xato -> ValueError."""
    t = (text or "").strip().replace(",", ".")
    if not t:
        return None
    parts = t.split(":")
    if len(parts) > 3:
        raise ValueError(text)
    total = 0.0
    for p in parts:
        if p == "":
            raise ValueError(text)
        v = float(p)
        if v < 0:
            raise ValueError(text)
        total = total * 60 + v
    return total
