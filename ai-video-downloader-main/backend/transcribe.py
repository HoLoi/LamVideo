"""
Whisper transcription dùng faster-whisper.
"""
import asyncio
import json
import subprocess
import tempfile
from pathlib import Path

from rich.console import Console

from .store import get_aweme_id_from_path, upsert_caption

console = Console()


def _check_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _extract_audio(video_path: str, audio_path: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vn", "-ar", "16000", "-ac", "1", "-f", "wav", audio_path],
        capture_output=True, check=True,
    )


def _run_faster_whisper(audio_path: str, model: str, language: str | None) -> dict:
    from faster_whisper import WhisperModel

    console.print(f"[cyan]Load faster-whisper model '{model}'...[/cyan]")
    m = WhisperModel(model, device="cpu", compute_type="int8")
    console.print("[cyan]Đang transcribe...[/cyan]")
    segments, info = m.transcribe(
        audio_path, language=language, beam_size=5,
        word_timestamps=False,
        vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
    )
    console.print(f"[dim]Detected language: {info.language} (prob={info.language_probability:.2f})[/dim]")

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


def _get_video_folder(video_path: Path) -> Path | None:
    """Trả về folder chứa video nếu theo cấu trúc mới (parent là folder tên video)."""
    parent = video_path.parent
    # Cấu trúc mới: downloads/jingxuan/{folder}/index.mp4
    if video_path.name == "index.mp4":
        return parent
    # Cấu trúc cũ: flat file — không có folder riêng
    return None


async def transcribe_video(
    video_path: str,
    model: str = "large-v3-turbo",
    language: str | None = None,
    aweme_id: str | None = None,
) -> dict:
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy file: {video_path}")
    if not _check_ffmpeg():
        raise RuntimeError("ffmpeg chưa cài. Chạy: brew install ffmpeg")

    vid_id = aweme_id or get_aweme_id_from_path(video_path) or path.stem
    console.print(f"[yellow]Video:[/yellow] {path.name}  [yellow]aweme_id:[/yellow] {vid_id}  [yellow]Model:[/yellow] {model}")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_path = tmp.name

    try:
        console.print("[cyan]Trích xuất audio...[/cyan]")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _extract_audio, video_path, audio_path)
        result = await loop.run_in_executor(None, _run_faster_whisper, audio_path, model, language)
    finally:
        Path(audio_path).unlink(missing_ok=True)

    # Lưu transcript.json vào folder nếu theo cấu trúc mới
    folder = _get_video_folder(path)
    if folder:
        transcript_path = folder / "transcript.json"
        transcript_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[green]Đã lưu transcript.json → {transcript_path}[/green]")

    caption = result["text"]
    console.print(f"\n[green]Caption:[/green]\n{caption}\n")
    await upsert_caption(aweme_id=vid_id, caption=caption)
    console.print("[green]Đã cập nhật index.csv[/green]")
    return result
