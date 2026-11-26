"""Andozalar va ffmpeg buyruqlari: mantiq + haqiqiy ffmpeg bilan qisqa sinov (faqat vaqtinchalik papkada)."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from video_konvertor import ffbin, presets as P  # noqa: E402
from video_konvertor.media import MediaInfo, fmt_duration, parse_probe, parse_time, probe  # noqa: E402
from video_konvertor.runner import FFmpegRun, parse_progress_line, run_with_fallbacks  # noqa: E402

HAVE_FFMPEG = os.path.exists(ffbin.ffmpeg())


def make_info(w=1920, h=1080, dur=60.0, fps=30.0, audio=True, vbit=8_000_000):
    return MediaInfo(path="x.mp4", duration=dur, size=int((vbit + 128000) * dur / 8), bitrate=vbit + 128000,
                     has_video=True, width=w, height=h, fps=fps, vcodec="h264", vbitrate=vbit,
                     has_audio=audio, acodec="aac", abitrate=128000, sample_rate=48000, channels=2)


def test_parse_time():
    assert parse_time("") is None
    assert parse_time("90") == 90
    assert parse_time("1:30") == 90
    assert parse_time("00:01:30.5") == 90.5
    assert parse_time("1,5") == 1.5
    with pytest.raises(ValueError):
        parse_time("abc")
    with pytest.raises(ValueError):
        parse_time("1:2:3:4")


def test_fmt_duration():
    assert fmt_duration(0) == "—"
    assert fmt_duration(75) == "1:15"
    assert fmt_duration(3725) == "1:02:05"


def test_quality_mapping_monotonic():
    assert P.quality_to_crf(100) == 18 and P.quality_to_crf(0) == 35
    kb = [P.video_kbps(1920, 1080, 30, q) for q in (0, 50, 100)]
    assert kb[0] < kb[1] < kb[2]


def test_scaled_size():
    assert P.scaled_size(1920, 1080, 720) == (1280, 720)
    assert P.scaled_size(1080, 1920, 720) == (720, 1280)
    assert P.scaled_size(640, 360, 720) == (640, 360)   # kattalashtirmaydi


def test_output_path_no_overwrite():
    existing = {os.path.join("C:\\v", "a_compressed.mp4")}
    out = P.output_path(P.Job(preset="compress"), "C:\\v\\a.mov", "C:\\v", exists=lambda p: p in existing)
    assert out.endswith("a_compressed (2).mp4")
    assert P.output_path(P.Job(preset="audio", audio_fmt="aac"), "C:\\v\\a.mov", "C:\\o",
                         exists=lambda p: False).endswith("a_audio.m4a")


def test_telegram_cap_under_2gb():
    info = make_info(dur=4 * 3600, vbit=20_000_000)      # 4 soatlik katta video
    job = P.Job(preset="telegram", quality=100)
    est = P.estimate_size(job, info)
    assert est < P.TELEGRAM_LIMIT
    cmd = P.build_commands(job, "in.mp4", info, "out.mp4", "ffmpeg")[0]
    assert "aac" in cmd and "-b:v" in cmd


def test_compress_never_bigger_than_source():
    info = make_info(vbit=1_000_000)
    plan = P.video_plan(P.Job(preset="compress", quality=100), info)
    assert plan.kbps <= 850


def test_reels_blur_uses_filter_complex():
    info = make_info()
    cmd = P.build_commands(P.Job(preset="reels", fit="blur"), "in.mp4", info, "o.mp4")[0]
    assert "-filter_complex" in cmd and "[vout]" in cmd
    cmd = P.build_commands(P.Job(preset="reels", fit="pad"), "in.mp4", info, "o.mp4")[0]
    i = cmd.index("-vf")
    assert "pad=1080:1920" in cmd[i + 1]


def test_trim_args_and_duration():
    info = make_info(dur=100)
    job = P.Job(preset="trim", start=10, end=25)
    assert P.effective_duration(job, info) == 15
    cmd = P.build_commands(job, "in.mp4", info, "o.mp4")[0]
    assert cmd[cmd.index("-ss") + 1] == "10.000" and cmd[cmd.index("-t") + 1] == "15.000"
    assert "copy" in cmd


def test_rotate_and_fallback_encoder():
    job = P.Job(preset="rotate", rotate="cw", encoder="h264_nvenc")
    cmds = P.build_commands(job, "in.mp4", make_info(), "o.mp4")
    assert len(cmds) == 2 and "transpose=1" in cmds[0] and "libopenh264" in cmds[1]


def test_no_video_error():
    info = MediaInfo(duration=10, has_audio=True, acodec="mp3")
    with pytest.raises(ValueError):
        P.build_commands(P.Job(preset="gif"), "a.mp3", info, "o.gif")


def test_concat_list_and_compat():
    txt = P.concat_list_text(["C:\\a b\\it's.mp4", "D:\\x.mp4"])
    assert "file 'C:/a b/it'\\''s.mp4'" in txt
    a, b = make_info(), make_info()
    assert P.concat_compatible([a, b])
    b.width = 1280
    assert not P.concat_compatible([a, b])


def test_estimates():
    info = make_info(dur=60)
    assert P.estimate_size(P.Job(preset="audio", audio_fmt="mp3", audio_kbps=192), info) == 192000 * 60 // 8
    assert P.frame_count(P.Job(preset="frames", frame_every=2), info) == 31
    assert P.estimate_size(P.Job(preset="gif"), info) > 0


def test_progress_parse():
    st = {}
    assert parse_progress_line("out_time_us=1500000", st) == 1.5
    assert parse_progress_line("out_time=00:00:02.500000", st) == 2.5
    assert parse_progress_line("progress=continue", st) is None and st["progress"] == "continue"


def test_parse_probe_rotation():
    data = {"format": {"duration": "12.5", "size": "1000", "bit_rate": "640"},
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080, "avg_frame_rate": "30000/1001",
                         "codec_name": "h264", "side_data_list": [{"rotation": -90}]},
                        {"codec_type": "audio", "codec_name": "aac", "sample_rate": "44100", "channels": 2}]}
    mi = parse_probe(data)
    assert mi.duration == 12.5 and abs(mi.fps - 29.97) < 0.01 and mi.display_size == (1080, 1920)


# ------------------------------------------------------------------ haqiqiy ffmpeg bilan
@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg yo'q")
    d = tmp_path_factory.mktemp("vk")
    src = d / "sample.mp4"
    r = ffbin.run([ffbin.ffmpeg(), "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25", "-f", "lavfi",
                   "-i", "sine=frequency=440:sample_rate=44100", "-t", "3", "-c:v", "libopenh264",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)])
    assert r.returncode == 0, r.stderr
    return src


@pytest.mark.parametrize("preset,extra", [
    ("compress", {}), ("whatsapp", {}), ("reels", {"fit": "blur"}), ("audio", {"audio_fmt": "mp3"}),
    ("gif", {"gif_width": 160, "gif_fps": 8}), ("trim", {"start": 0.5, "end": 2.0}), ("rotate", {}),
    ("noaudio", {}), ("container", {"container": "mkv"}), ("container", {"container": "webm", "copy": False}),
])
def test_real_encode(sample, preset, extra):
    info = probe(str(sample))
    assert info.has_video and info.has_audio and abs(info.duration - 3) < 0.3
    job = P.Job(preset=preset, encoder=ffbin.SOFTWARE_H264, **extra)
    out = P.output_path(job, str(sample))
    cmds = P.build_commands(job, str(sample), info, out, ffbin.ffmpeg())
    seen = []
    err = run_with_fallbacks(cmds, out, P.effective_duration(job, info), FFmpegRun(), seen.append)
    assert err == "", err
    assert os.path.getsize(out) > 0 and seen[-1] == 100.0


def test_real_frames_and_concat(sample, tmp_path):
    info = probe(str(sample))
    job = P.Job(preset="frames", frame_every=1)
    out = tmp_path / "frames"
    out.mkdir()
    cmds = P.build_commands(job, str(sample), info, str(out), ffbin.ffmpeg())
    assert run_with_fallbacks(cmds, str(out), info.duration, FFmpegRun()) == ""
    assert len(list(out.glob("*.jpg"))) >= 3
    lst = tmp_path / "list.txt"
    lst.write_text(P.concat_list_text([str(sample), str(sample)]), encoding="utf-8")
    merged = tmp_path / "merged.mp4"
    cmds = P.build_concat_commands([str(sample)] * 2, [info, info], str(merged), str(lst), copy=False,
                                   encoder=ffbin.SOFTWARE_H264, ffmpeg=ffbin.ffmpeg())
    assert run_with_fallbacks(cmds, str(merged), 6, FFmpegRun()) == ""
    assert abs(probe(str(merged)).duration - 6) < 0.5


def test_cancel(sample, tmp_path):
    import threading
    info = probe(str(sample))
    r = FFmpegRun()
    cmd = [ffbin.ffmpeg(), "-y", "-progress", "pipe:1", "-nostats", "-re", "-stream_loop", "-1", "-i", str(sample),
           "-f", "null", "-"]
    threading.Timer(0.8, r.cancel).start()
    from video_konvertor.runner import Cancelled
    with pytest.raises(Cancelled):
        r.run(cmd, info.duration)
