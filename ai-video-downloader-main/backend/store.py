"""
Quản lý index.csv — track tất cả video đã crawl/tải/transcribe.
Thread-safe với asyncio.Lock.
"""
import asyncio
import csv
from datetime import datetime
from pathlib import Path

INDEX_PATH = Path(__file__).parent.parent / "index.csv"

FIELDNAMES = [
    "aweme_id",
    "author",
    "title",
    "status",
    "local_path",
    "download_url",
    "downloaded_at",
    "whisper-caption",
    "transcribed_at",
]

_lock = asyncio.Lock()


def _read_all() -> dict[str, dict]:
    if not INDEX_PATH.exists():
        return {}
    with open(INDEX_PATH, newline="", encoding="utf-8") as f:
        return {row["aweme_id"]: row for row in csv.DictReader(f)}


def _write_all(rows: dict[str, dict]) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(INDEX_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows.values())


async def upsert_download(
    aweme_id: str,
    author: str,
    title: str,
    local_path: str,
    download_url: str,
    status: str = "pending",
) -> None:
    async with _lock:
        rows = _read_all()
        existing = rows.get(aweme_id, {})
        rows[aweme_id] = {
            "aweme_id": aweme_id,
            "author": author,
            "title": title,
            "status": status,
            "local_path": local_path,
            "download_url": download_url,
            "downloaded_at": existing.get("downloaded_at") or datetime.now().isoformat(timespec="seconds"),
            "whisper-caption": existing.get("whisper-caption", ""),
            "transcribed_at": existing.get("transcribed_at", ""),
        }
        _write_all(rows)


async def upsert_caption(aweme_id: str, caption: str) -> None:
    async with _lock:
        rows = _read_all()
        row = rows.setdefault(aweme_id, {f: "" for f in FIELDNAMES})
        row["aweme_id"] = aweme_id
        row["whisper-caption"] = caption
        row["transcribed_at"] = datetime.now().isoformat(timespec="seconds")
        _write_all(rows)


def get_row(aweme_id: str) -> dict | None:
    return _read_all().get(aweme_id)


def get_aweme_id_from_path(path: str) -> str | None:
    parts = Path(path).stem.rsplit("__", 1)
    return parts[-1] if len(parts) == 2 else None
