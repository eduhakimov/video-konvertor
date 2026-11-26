# FFmpeg Shell: user guide

## Convert
1. Drop video or audio files onto the area at the top (or use **Add files**).
2. Pick a **Preset** on the right: compress, Telegram, Reels, YouTube, WhatsApp, format, audio, GIF, trim, resolution, rotate, remove audio, frames.
3. Adjust quality, height, format and other options if needed. The table shows the estimated size of each output.
4. **Start / End** are optional: `90`, `1:30` or `00:01:30.5`.
5. Press **Start**. Each file shows its own progress; **Cancel** stops the work.
6. Output is saved next to the source (or in the chosen folder) as `name_compressed.mp4` and similar. Existing files are never overwritten.

If several rows are selected, only those are processed.

## Encoder
**Auto** picks the fastest working H.264 encoder (NVIDIA NVENC, AMD AMF, Intel QSV, Windows Media Foundation or OpenH264). If a hardware encoder fails, the job is retried with OpenH264.

## Advanced command
Select a file in the queue and press **Command (advanced)** to view, copy or edit the ffmpeg command and apply it to that file.

## Merge
Add files and reorder them with **Up / Down**. When all files share the same format, **Fast** mode joins them without re-encoding.

## Media info
Drop a file to see duration, resolution, codecs, bitrate and every stream.
