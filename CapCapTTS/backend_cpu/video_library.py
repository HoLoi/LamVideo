"""
Video library manager — dữ liệu tại {VIDEO_LIBRARY_DIR}/library.json
Cấu trúc thư mục mỗi video:
  data/videos/<video_id>/
    index.mp4
    metadata.json
    transcript.json
    transcript.txt
    tts_outputs/
"""
import asyncio
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from config import VIDEO_LIBRARY_DIR

LIBRARY_FILE = VIDEO_LIBRARY_DIR / "library.json"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

_lock = asyncio.Lock()


def is_valid_video_id(video_id: str) -> bool:
    return bool(video_id) and bool(ID_RE.match(video_id))


def video_dir(video_id: str) -> Path:
    """Folder tuyệt đối của 1 video. Chặn path traversal: chỉ id an toàn mới resolve được."""
    if not is_valid_video_id(video_id):
        raise ValueError(f"video_id không hợp lệ: {video_id!r}")
    d = (VIDEO_LIBRARY_DIR / video_id).resolve()
    if not str(d).startswith(str(VIDEO_LIBRARY_DIR.resolve())):
        raise ValueError("Path nằm ngoài video library")
    return d


def ensure_video_dir(video_id: str) -> Path:
    d = video_dir(video_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "tts_outputs").mkdir(parents=True, exist_ok=True)
    return d


def _read_library() -> list[dict]:
    if not LIBRARY_FILE.exists():
        return []
    try:
        data = json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_library(videos: list[dict]) -> None:
    LIBRARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    LIBRARY_FILE.write_text(json.dumps(videos, ensure_ascii=False, indent=2), encoding="utf-8")


async def list_videos() -> list[dict]:
    async with _lock:
        videos = _read_library()
        videos.sort(key=lambda v: v.get("created_at", ""), reverse=True)
        return videos


async def get_video(video_id: str) -> dict | None:
    async with _lock:
        for v in _read_library():
            if v.get("id") == video_id:
                return v
    return None


async def upsert_video(meta: dict) -> dict:
    async with _lock:
        videos = _read_library()
        videos = [v for v in videos if v.get("id") != meta.get("id")]
        videos.append(meta)
        _write_library(videos)
    return meta


async def update_video(video_id: str, **fields) -> dict | None:
    async with _lock:
        videos = _read_library()
        for v in videos:
            if v.get("id") == video_id:
                v.update(fields)
                _write_library(videos)
                return v
    return None


async def delete_video(video_id: str, remove_files: bool = True) -> bool:
    if not is_valid_video_id(video_id):
        return False
    async with _lock:
        videos = _read_library()
        videos = [v for v in videos if v.get("id") != video_id]
        _write_library(videos)
    if remove_files:
        d = video_dir(video_id)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    return True


def get_transcript(video_id: str) -> dict | None:
    """Đọc transcript.json trong folder video (nếu có)."""
    try:
        d = video_dir(video_id)
        p = d / "transcript.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_transcript_txt(video_id: str, result: dict) -> str:
    """Ghi transcript.txt (text thuần) từ transcript.json. Trả về text."""
    text = (result.get("text") or "").strip()
    d = ensure_video_dir(video_id)
    (d / "transcript.txt").write_text(text, encoding="utf-8")
    return text


def get_transcript_vi(video_id: str) -> dict | None:
    """Đọc transcript_vi.json trong folder video (nếu có)."""
    try:
        d = video_dir(video_id)
        p = d / "transcript_vi.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_transcript_vi(video_id: str, result: dict) -> str:
    """Ghi transcript_vi.json (giữ segment timings) + transcript_vi.txt (text thuần). Trả về text."""
    text = (result.get("text") or "").strip()
    d = ensure_video_dir(video_id)
    (d / "transcript_vi.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (d / "transcript_vi.txt").write_text(text, encoding="utf-8")
    return text


def get_duration(video_id: str) -> float | None:
    """Đọc duration trong metadata.json nếu đã probe."""
    try:
        d = video_dir(video_id)
        p = d / "metadata.json"
        if p.exists():
            meta = json.loads(p.read_text(encoding="utf-8"))
            return meta.get("duration")
    except Exception:
        pass
    return None


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")