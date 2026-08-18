"""
Whisper transcription dùng faster-whisper.

Vendored từ ai-video-downloader-main/backend/transcribe.py (2026-08-17).
Thay đổi so với bản gốc:
- Bỏ rich console + store.py (index.csv).
- Lazy import faster-whisper.
- ffmpeg: resolve_ffmpeg() — env FFMPEG_PATH → PATH (shutil.which) → Playwright bundle → lỗi rõ.
- subprocess ffmpeg luôn capture stdout/stderr; returncode != 0 → RuntimeError kèm stderr (30 dòng cuối).
- Kiểm tra video trước khi transcribe: file tồn tại, size > 0, có audio stream.
"""
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from .util import _playwright_ffmpeg

_STDERR_TAIL_LINES = 30


def _default_log(msg: str) -> None:
    print(f"[video_dl] {msg}")


def resolve_ffmpeg() -> str:
    """Tìm ffmpeg theo thứ tự ưu tiên:
    1. env FFMPEG_PATH (file ffmpeg.exe hoặc thư mục bin chứa ffmpeg)
    2. PATH qua shutil.which("ffmpeg")
    3. Playwright bundled ffmpeg (chỉ fallback)
    4. Không có → RuntimeError rõ ràng.
    """
    env_path = os.environ.get("FFMPEG_PATH", "").strip()
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return str(p)
        for name in ("ffmpeg.exe", "ffmpeg"):
            cand = p / name
            if cand.is_file():
                return str(cand)
    which = shutil.which("ffmpeg")
    if which:
        return which
    bundled = _playwright_ffmpeg()
    if bundled:
        return str(bundled)
    raise RuntimeError(
        "Không tìm thấy ffmpeg. Cài FFmpeg (winget install Gyan.FFmpeg) hoặc đặt "
        "env FFMPEG_PATH trỏ tới ffmpeg.exe / thư mục bin của ffmpeg."
    )


def _stderr_tail(proc: subprocess.CompletedProcess, lines: int = _STDERR_TAIL_LINES) -> str:
    err = proc.stderr or ""
    if isinstance(err, bytes):
        err = err.decode("utf-8", errors="replace")
    return "\n".join(err.splitlines()[-lines:])


def _probe_audio_stream(video_path: str, ffmpeg: str) -> None:
    """ffmpeg -hide_banner -i <video> → kiểm tra có stream audio không."""
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", video_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    info = proc.stderr or ""
    if "Audio:" not in info:
        raise RuntimeError(
            "Video không có audio stream nên không thể transcribe.\n"
            + _stderr_tail(proc, lines=10)
        )


def _extract_audio(video_path: str, audio_path: str) -> None:
    ffmpeg = resolve_ffmpeg()
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-y", "-i", video_path,
         "-vn", "-ar", "16000", "-ac", "1", "-f", "wav", audio_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg trích xuất audio thất bại (exit {proc.returncode}, ffmpeg: {ffmpeg}).\n"
            + _stderr_tail(proc)
        )


def _run_faster_whisper(audio_path: str, model: str, language: str | None) -> dict:
    from faster_whisper import WhisperModel

    m = WhisperModel(model, device="cpu", compute_type="int8")
    segments, info = m.transcribe(
        audio_path, language=language, beam_size=5,
        word_timestamps=False,
        vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
    )

    result_segments = []
    full_text_parts = []
    for seg in segments:
        result_segments.append({
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
        })
        full_text_parts.append(seg.text.strip())

    return {
        "language": info.language,
        "language_probability": round(info.language_probability, 4),
        "text": " ".join(full_text_parts).strip(),
        "segments": result_segments,
    }


async def transcribe_video(
    video_path: str,
    model: str = "large-v3-turbo",
    language: str | None = None,
    aweme_id: str | None = None,
    log: Optional[Callable[[str], None]] = None,
) -> dict:
    log = log or _default_log
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy file: {video_path}")
    if path.stat().st_size <= 0:
        raise RuntimeError("File video rỗng (0 bytes) — không thể transcribe.")
    ffmpeg = resolve_ffmpeg()
    _probe_audio_stream(str(path), ffmpeg)

    vid_id = aweme_id or path.stem
    log(f"Video: {path.name}  aweme_id: {vid_id}  Model: {model}  ffmpeg: {ffmpeg}")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_path = tmp.name

    try:
        log("Trích xuất audio...")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _extract_audio, video_path, audio_path)
        result = await loop.run_in_executor(None, _run_faster_whisper, audio_path, model, language)
    finally:
        Path(audio_path).unlink(missing_ok=True)

    # Lưu transcript.json vào folder chứa video (cấu trúc library: {video_id}/index.mp4)
    folder = path.parent
    transcript_path = folder / "transcript.json"
    transcript_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Đã lưu transcript.json → {transcript_path}")
    return result