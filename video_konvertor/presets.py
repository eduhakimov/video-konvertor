"""Tayyor andozalar (presets) va ffmpeg buyrug'ini tuzish. Interfeysdan mustaqil, unit-test qilinadi."""
import math
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import List, Optional

from .media import MediaInfo

PRESETS = ["compress", "telegram", "reels", "youtube", "whatsapp", "container", "audio", "gif",
           "trim", "resize", "rotate", "noaudio", "frames"]

VIDEO_PRESETS = {"compress", "telegram", "reels", "youtube", "whatsapp", "resize", "rotate"}
CONTAINERS = ["mp4", "mkv", "mov", "webm", "avi"]
AUDIO_FORMATS = ["mp3", "aac", "wav"]
HEIGHTS = [0, 2160, 1440, 1080, 720, 480, 360]
ROTATIONS = ["cw", "ccw", "180", "hflip", "vflip"]
FIT_MODES = ["blur", "pad", "crop"]
TELEGRAM_LIMIT = 2 * 1000 ** 3            # 2 GB
SAFE_TELEGRAM = int(TELEGRAM_LIMIT * 0.97)


@dataclass
class Job:
    preset: str = "compress"
    quality: int = 60               # 0..100 (yuqori = sifatliroq, katta fayl)
    container: str = "mp4"
    copy: bool = True               # konteyner/kesish: qayta kodlamasdan (tez)
    audio_fmt: str = "mp3"
    audio_kbps: int = 192
    gif_fps: int = 12
    gif_width: int = 480
    start: Optional[float] = None
    end: Optional[float] = None
    height: int = 0                 # 0 = o'zgarmasin
    rotate: str = "cw"
    fit: str = "blur"
    frame_every: float = 1.0        # soniya
    frame_fmt: str = "jpg"
    encoder: str = "libopenh264"


# ---------------------------------------------------------------- yordamchi hisoblar
def quality_to_crf(q: int) -> int:
    """0..100 sifat -> x264 CRF ga teng qiymat (35..18)."""
    q = max(0, min(100, int(q)))
    return round(35 - q * 17 / 100)


def bits_per_pixel(crf: int) -> float:
    """CRF 23 ≈ 0.085 bpp; har +6 CRF bitreytni ikki baravar kamaytiradi."""
    return 0.085 * 2 ** ((23 - crf) / 6)


def video_kbps(width: int, height: int, fps: float, quality: int) -> int:
    fps = fps if 1 <= (fps or 0) <= 240 else 30
    return max(150, int(width * height * min(fps, 60) * bits_per_pixel(quality_to_crf(quality)) / 1000))


def effective_duration(job: Job, info: MediaInfo) -> float:
    d = info.duration or 0
    s = job.start or 0
    e = job.end if job.end is not None and job.end > 0 else d
    if d:
        e = min(e, d)
    return max(0.0, e - s)


def scaled_size(w: int, h: int, target: int):
    """Qisqa tomoni `target` bo'ladigan juft o'lcham (kattalashtirilmaydi)."""
    if not target or not w or not h or min(w, h) <= target:
        return w - w % 2, h - h % 2
    if w >= h:
        nw = round(w * target / h)
        return nw - nw % 2, target - target % 2
    nh = round(h * target / w)
    return target - target % 2, nh - nh % 2


def _scale_filter(w: int, h: int, target: int) -> Optional[str]:
    if not target or not w or not h or min(w, h) <= target:
        return None
    return f"scale=-2:{target}" if w >= h else f"scale={target}:-2"


def fit_filter(W: int, H: int, mode: str) -> str:
    if mode == "crop":
        return (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1")
    if mode == "pad":
        return (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
    # xira fon (blur): video markazda, orqasida o'zining xiralashgan nusxasi
    return (f"split=2[bg][fg];[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"gblur=sigma=30[bgb];[fg]scale={W}:{H}:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1")


def output_ext(job: Job, src: str) -> str:
    p = job.preset
    if p == "container":
        return job.container
    if p == "audio":
        return {"mp3": "mp3", "aac": "m4a", "wav": "wav"}[job.audio_fmt]
    if p == "gif":
        return "gif"
    if p == "frames":
        return ""
    if p in ("trim", "noaudio"):
        ext = Path(src).suffix.lstrip(".").lower()
        return ext or "mp4"
    return "mp4"


SUFFIX = {"compress": "compressed", "telegram": "telegram", "reels": "reels", "youtube": "youtube",
          "whatsapp": "whatsapp", "container": "converted", "audio": "audio", "gif": "gif", "trim": "trim",
          "resize": "resized", "rotate": "rotated", "noaudio": "noaudio", "frames": "frames"}


def output_path(job: Job, src: str, out_dir: str = "", exists=os.path.exists) -> str:
    """Mavjud faylni ustidan yozmaydigan chiqish yo'li: video_compressed.mp4, video_compressed (2).mp4 ..."""
    srcp = Path(src)
    folder = Path(out_dir) if out_dir else srcp.parent
    base = f"{srcp.stem}_{SUFFIX.get(job.preset, 'out')}"
    ext = output_ext(job, src)
    n = 1
    while True:
        name = base if n == 1 else f"{base} ({n})"
        cand = folder / (f"{name}.{ext}" if ext else name)
        if not exists(str(cand)):
            return str(cand)
        n += 1


# ---------------------------------------------------------------- video parametrlari
@dataclass
class VideoPlan:
    filters: Optional[str] = None
    width: int = 0
    height: int = 0
    fps: float = 0
    max_fps: float = 0
    kbps: int = 0
    akbps: int = 128
    max_kbps: int = 0


def video_plan(job: Job, info: MediaInfo) -> VideoPlan:
    w, h = info.display_size
    fps = info.fps or 30
    p = job.preset
    plan = VideoPlan(width=w, height=h, fps=fps)
    flt = []
    if p in ("reels", "youtube"):
        W, H = (1080, 1920) if p == "reels" else (1920, 1080)
        flt.append(fit_filter(W, H, job.fit))
        plan.width, plan.height = W, H
        plan.max_fps = 30 if p == "reels" else 60
    elif p == "rotate":
        flt.append({"cw": "transpose=1", "ccw": "transpose=2", "180": "hflip,vflip",
                    "hflip": "hflip", "vflip": "vflip"}[job.rotate])
        if job.rotate in ("cw", "ccw"):
            plan.width, plan.height = h, w
    else:
        target = job.height
        if p == "telegram":
            target = min(target or 1080, 1080)
        elif p == "whatsapp":
            target = min(target or 720, 720)
        sf = _scale_filter(w, h, target)
        if sf:
            flt.append(sf)
            plan.width, plan.height = scaled_size(w, h, target)
        if p == "whatsapp":
            plan.max_fps = 30
    if plan.max_fps and fps > plan.max_fps + 0.5:
        flt.append(f"fps={plan.max_fps:g}")
        plan.fps = plan.max_fps
    plan.filters = ",".join(flt) if flt else None
    q = job.quality
    plan.kbps = video_kbps(plan.width or 1280, plan.height or 720, plan.fps, q)
    plan.akbps = {"whatsapp": 96, "youtube": 192}.get(p, 128)
    if p == "compress" and info.vbitrate:
        # siqish hech qachon manbadan kattaroq bitreyt bermasin
        plan.kbps = min(plan.kbps, max(150, int(info.vbitrate / 1000 * 0.85)))
    if p == "whatsapp":
        plan.kbps = min(plan.kbps, 2000)
    if p == "telegram":
        dur = effective_duration(job, info)
        if dur > 0:
            cap = int(SAFE_TELEGRAM * 8 / dur / 1000) - plan.akbps
            plan.kbps = max(100, min(plan.kbps, cap))
    plan.max_kbps = int(plan.kbps * 1.5)
    return plan


def h264_args(encoder: str, kbps: int, max_kbps: int) -> List[str]:
    a = ["-c:v", encoder, "-b:v", f"{kbps}k", "-maxrate", f"{max_kbps}k", "-bufsize", f"{max_kbps * 2}k",
         "-pix_fmt", "yuv420p"]
    if encoder == "h264_nvenc":
        a += ["-preset", "p5", "-rc", "vbr"]
    elif encoder == "h264_qsv":
        a += ["-preset", "medium"]
    elif encoder == "h264_amf":
        a += ["-quality", "balanced", "-rc", "vbr_peak"]
    return a


def _time_args(job: Job, info: MediaInfo):
    """(-ss kirish oldidan, -t chiqish uchun)."""
    pre, post = [], []
    if job.start:
        pre = ["-ss", f"{job.start:.3f}"]
    if job.end is not None and job.end > 0:
        dur = effective_duration(job, info)
        post = ["-t", f"{dur:.3f}"]
    return pre, post


# ---------------------------------------------------------------- asosiy funksiya
def build_commands(job: Job, src: str, info: MediaInfo, out: str, ffmpeg: str = "ffmpeg",
                   fallback_encoder: str = "libopenh264") -> List[List[str]]:
    """Bajariladigan buyruqlar ro'yxati: birinchisi asosiy, qolganlari xato bo'lsa zaxira variantlar."""
    p = job.preset
    if p in VIDEO_PRESETS | {"gif", "frames"} and not info.has_video:
        raise ValueError("no_video")
    if p == "audio" and not info.has_audio:
        raise ValueError("no_audio")
    pre, post = _time_args(job, info)
    head = [ffmpeg, "-hide_banner", "-y", "-nostdin", "-progress", "pipe:1", "-nostats", "-loglevel", "error"]
    inp = pre + ["-i", src]
    cmds: List[List[str]] = []

    if p in VIDEO_PRESETS:
        plan = video_plan(job, info)

        def mk(enc):
            c = head + inp + post
            if plan.filters and ";" in plan.filters:
                c += ["-filter_complex", f"[0:v]{plan.filters}[vout]", "-map", "[vout]", "-map", "0:a:0?"]
            else:
                c += ["-map", "0:v:0", "-map", "0:a:0?"]
                if plan.filters:
                    c += ["-vf", plan.filters]
            c += h264_args(enc, plan.kbps, plan.max_kbps)
            if info.has_audio:
                c += ["-c:a", "aac", "-b:a", f"{plan.akbps}k", "-ac", "2"]
            c += ["-movflags", "+faststart", "-map_metadata", "0", out]
            return c
        cmds.append(mk(job.encoder))
        if job.encoder != fallback_encoder:
            cmds.append(mk(fallback_encoder))
        return cmds

    if p == "container":
        ext = job.container
        if job.copy and ext != "webm":
            c = head + inp + post + ["-map", "0:v?", "-map", "0:a?"]
            if ext == "mkv":
                c += ["-map", "0:s?"]
            c += ["-c", "copy"]
            if ext in ("mp4", "mov"):
                c += ["-movflags", "+faststart"]
            cmds.append(c + [out])
        cmds += _reencode_container(job, info, head + inp + post, out, fallback_encoder)
        return cmds

    if p == "audio":
        c = head + inp + post + ["-vn", "-map", "0:a:0"]
        if job.audio_fmt == "mp3":
            c += ["-c:a", "libmp3lame", "-b:a", f"{job.audio_kbps}k"]
        elif job.audio_fmt == "aac":
            c += ["-c:a", "aac", "-b:a", f"{job.audio_kbps}k", "-movflags", "+faststart"]
        else:
            c += ["-c:a", "pcm_s16le"]
        return [c + [out]]

    if p == "gif":
        fg = (f"fps={job.gif_fps},scale={job.gif_width}:-1:flags=lanczos,split[s0][s1];"
              f"[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5")
        return [head + inp + post + ["-filter_complex", f"[0:v]{fg}", "-loop", "0", out]]

    if p == "trim":
        if job.copy:
            cmds.append(head + inp + post + ["-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero", out])
        plan = video_plan(replace(job, preset="resize", height=0), info)
        cmds.append(head + inp + post + ["-map", "0:v:0", "-map", "0:a:0?"] +
                    h264_args(job.encoder, plan.kbps, plan.max_kbps) +
                    (["-c:a", "aac", "-b:a", "160k"] if info.has_audio else []) + ["-movflags", "+faststart", out])
        if job.encoder != fallback_encoder:
            cmds.append(cmds[-1][:])
            i = cmds[-1].index("-c:v")
            cmds[-1][i + 1] = fallback_encoder
        return cmds

    if p == "noaudio":
        return [head + inp + post + ["-map", "0:v", "-c", "copy", "-an", out]]

    if p == "frames":
        os_out = os.path.join(out, f"{Path(src).stem}_%05d.{job.frame_fmt}")
        every = max(0.01, float(job.frame_every or 1))
        c = head + inp + post + ["-vf", f"fps=1/{every:g}"]
        if job.frame_fmt == "jpg":
            c += ["-q:v", "2"]
        return [c + [os_out]]
    raise ValueError(f"unknown preset {p}")


def _reencode_container(job: Job, info: MediaInfo, base: List[str], out: str, fallback: str) -> List[List[str]]:
    ext = job.container
    res = []
    vplan = video_plan(replace(job, preset="resize", height=0), info) if info.has_video else None
    if ext == "webm":
        c = base[:]
        if info.has_video:
            crf = quality_to_crf(job.quality) + 8
            c += ["-c:v", "libvpx-vp9", "-crf", str(crf), "-b:v", "0", "-row-mt", "1", "-deadline", "good",
                  "-cpu-used", "4", "-pix_fmt", "yuv420p"]
        c += ["-c:a", "libopus", "-b:a", "128k"] if info.has_audio else []
        return [c + [out]]
    if ext == "avi":
        c = base[:]
        if info.has_video:
            c += ["-c:v", "mpeg4", "-q:v", str(max(2, min(10, round(10 - job.quality / 12)))), "-tag:v", "XVID"]
        c += ["-c:a", "libmp3lame", "-b:a", "192k"] if info.has_audio else []
        return [c + [out]]
    for enc in dict.fromkeys([job.encoder, fallback]):
        c = base[:]
        if info.has_video:
            c += h264_args(enc, vplan.kbps, vplan.max_kbps)
        if info.has_audio:
            c += ["-c:a", "aac", "-b:a", "192k"]
        if ext in ("mp4", "mov"):
            c += ["-movflags", "+faststart"]
        res.append(c + [out])
        if not info.has_video:
            break
    return res


def build_concat_commands(files: List[str], infos: List[MediaInfo], out: str, list_file: str,
                          copy: bool, encoder: str, ffmpeg: str = "ffmpeg",
                          fallback_encoder: str = "libopenh264") -> List[List[str]]:
    """Bir nechta faylni birlashtirish. copy=True: concat demuxer (tez); aks holda filtr bilan qayta kodlash."""
    head = [ffmpeg, "-hide_banner", "-y", "-nostdin", "-progress", "pipe:1", "-nostats", "-loglevel", "error"]
    cmds = []
    if copy:
        cmds.append(head + ["-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy",
                            "-movflags", "+faststart", out])
    first = next((i for i in infos if i.has_video), None)
    any_audio = any(i.has_audio for i in infos)
    if first is None:   # faqat audio
        parts = "".join(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo[a{k}];" for k in range(len(files)))
        fg = parts + "".join(f"[a{k}]" for k in range(len(files))) + f"concat=n={len(files)}:v=0:a=1[a]"
        c = head + sum((["-i", f] for f in files), []) + ["-filter_complex", fg, "-map", "[a]", "-c:a", "aac",
                                                           "-b:a", "192k", out]
        return cmds + [c]
    W, H = first.display_size
    W, H = W - W % 2, H - H % 2
    fps = first.fps or 30
    inputs, fg = [], ""
    extra = len(files)
    for k, (f, inf) in enumerate(zip(files, infos)):
        inputs += ["-i", f]
    for k, inf in enumerate(infos):
        fg += (f"[{k}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
               f"setsar=1,fps={fps:g},format=yuv420p[v{k}];")
        if any_audio:
            if inf.has_audio:
                fg += f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo[a{k}];"
            else:
                inputs += ["-f", "lavfi", "-t", f"{max(inf.duration, 0.1):.3f}", "-i",
                           "anullsrc=channel_layout=stereo:sample_rate=48000"]
                fg += f"[{extra}:a]aformat=channel_layouts=stereo[a{k}];"
                extra += 1
    fg += "".join(f"[v{k}]" + (f"[a{k}]" if any_audio else "") for k in range(len(files)))
    fg += f"concat=n={len(files)}:v=1:a={1 if any_audio else 0}[v]" + ("[a]" if any_audio else "")
    total = sum(i.duration for i in infos)
    kbps = video_kbps(W, H, fps, 65)
    for enc in dict.fromkeys([encoder, fallback_encoder]):
        c = head + inputs + ["-filter_complex", fg, "-map", "[v]"] + (["-map", "[a]"] if any_audio else [])
        c += h264_args(enc, kbps, int(kbps * 1.5))
        if any_audio:
            c += ["-c:a", "aac", "-b:a", "192k"]
        cmds.append(c + ["-movflags", "+faststart", out])
    _ = total
    return cmds


def concat_list_text(files: List[str]) -> str:
    lines = []
    for f in files:
        p = f.replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{p}'")
    return "\n".join(lines) + "\n"


def concat_compatible(infos: List[MediaInfo]) -> bool:
    """Tez (copy) birlashtirish mumkinmi: kodek, o'lcham va audio bir xil bo'lishi kerak."""
    if not infos:
        return False
    a = infos[0]
    return all(i.vcodec == a.vcodec and i.width == a.width and i.height == a.height and
               i.acodec == a.acodec and i.has_audio == a.has_audio for i in infos)


# ---------------------------------------------------------------- hajmni taxmin qilish
def estimate_size(job: Job, info: MediaInfo) -> Optional[int]:
    """Chiqish faylining taxminiy hajmi (bayt). Noma'lum bo'lsa None."""
    dur = effective_duration(job, info)
    if not dur:
        return None
    p = job.preset
    frac = dur / info.duration if info.duration else 1
    try:
        if p in VIDEO_PRESETS:
            if not info.has_video:
                return None
            plan = video_plan(job, info)
            return int((plan.kbps + (plan.akbps if info.has_audio else 0)) * 1000 * dur / 8 * 1.02)
        if p == "audio":
            if not info.has_audio:
                return None
            kb = 1411 if job.audio_fmt == "wav" else job.audio_kbps
            if job.audio_fmt == "wav" and info.sample_rate:
                kb = info.sample_rate * 16 * max(1, info.channels) / 1000
            return int(kb * 1000 * dur / 8)
        if p in ("trim", "noaudio", "container") and (job.copy or p == "noaudio"):
            if p == "noaudio" and info.has_audio and info.abitrate:
                return int(max(0, info.size * frac - info.abitrate * dur / 8))
            if p == "container" and job.container == "webm":
                raise KeyError
            return int(info.size * frac)
        if p in ("container", "trim"):
            if job.container == "webm" and p == "container":
                return int(info.size * frac * 0.7) if info.size else None
            plan = video_plan(replace(job, preset="resize", height=0), info)
            return int((plan.kbps + 192) * 1000 * dur / 8)
        if p == "gif":
            if not info.width:
                return None
            h = job.gif_width * info.height / max(1, info.width)
            return int(job.gif_width * h * job.gif_fps * dur * 0.12)
        if p == "frames":
            n = math.floor(dur / max(0.01, job.frame_every)) + 1
            w, h = info.display_size
            per = w * h * (0.18 if job.frame_fmt == "jpg" else 1.4)
            return int(n * per)
    except KeyError:
        return int(info.size * frac * 0.7) if info.size else None
    return None


def frame_count(job: Job, info: MediaInfo) -> int:
    dur = effective_duration(job, info)
    return math.floor(dur / max(0.01, job.frame_every)) + 1 if dur else 0
