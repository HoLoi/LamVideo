"""
Async downloader: tải video, retry tự động.

Vendored từ ai-video-downloader-main/backend/downloader.py (2026-08-17).
Thay đổi so với bản gốc:
- Bỏ rich console + store.py (index.csv) — tiến độ báo qua callback.
- Hỗ trợ CẢ callback sync lẫn async (await nếu kết quả là coroutine) — xem _run_cb.
- Giữ nguyên logic: retry 3 lần, skip nếu file >10KB, ghi metadata.json.
"""
import asyncio
import re
from pathlib import Path
from typing import Callable, Optional

import aiofiles
import aiohttp

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.douyin.com/",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

CHUNK_SIZE = 1024 * 256
MAX_RETRIES = 3

# progress_cb(aweme_id, done_bytes, total_bytes) — báo tiến độ từng video
ProgressCb = Optional[Callable[[str, int, int], None]]
# status_cb(aweme_id, status, message) — status: success | failed | skipped | pending
StatusCb = Optional[Callable[[str, str, str], None]]
# diag_cb(aweme_id, diag_dict) — ghi diagnostics CDN request từng video (MVP-6)
DiagCb = Optional[Callable[[str, dict], None]]


async def _run_cb(cb, *args):
    """Gọi callback — hỗ trợ cả sync lẫn async (await nếu kết quả là coroutine)."""
    if cb is None:
        return
    result = cb(*args)
    if asyncio.iscoroutine(result):
        await result


def _safe_filename(text: str, max_len: int = 80) -> str:
    text = re.sub(r'[\\/:*?"<>|]', "_", text)
    return re.sub(r"\s+", " ", text).strip()[:max_len] or "video"


async def _download_one(
    session: aiohttp.ClientSession,
    video: dict,
    output_dir: Path,
    semaphore: asyncio.Semaphore,
    progress_cb: ProgressCb = None,
    status_cb: StatusCb = None,
    diag_cb: DiagCb = None,
) -> bool:
    import json

    aweme_id = video["aweme_id"]
    url = video["download_url"]
    author = _safe_filename(video.get("author", "unknown"))
    title = _safe_filename(video.get("title", aweme_id))
    folder = output_dir / f"{author}__{title}__{aweme_id}"
    folder.mkdir(parents=True, exist_ok=True)
    filepath = folder / "index.mp4"

    # Write metadata.json
    meta = {
        "aweme_id": aweme_id,
        "author": video.get("author", "unknown"),
        "title": video.get("title", ""),
        "download_url": url,
        "detail_url": f"https://www.douyin.com/video/{aweme_id}",
    }
    (folder / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    if status_cb:
        await _run_cb(status_cb, aweme_id, "pending", "")

    if filepath.exists() and filepath.stat().st_size > 10_000:
        if status_cb:
            await _run_cb(status_cb, aweme_id, "skipped", "Đã có sẵn")
        return True

    diag = {"video_id": aweme_id, "attempts": [], "ok": False}

    def _emit():
        """Gửi bản snapshot diagnostics hiện tại cho callback (fire-and-forget)."""
        if diag_cb:
            asyncio.create_task(_run_cb(diag_cb, aweme_id, dict(diag)))

    async with semaphore:
        for attempt in range(1, MAX_RETRIES + 1):
            entry = {"attempt": attempt}
            try:
                async with session.get(
                    url, headers=_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=180, sock_read=60),
                ) as resp:
                    redirects = [str(h.url) for h in resp.history]
                    final_url = str(resp.url)
                    entry.update({
                        "label": "http",
                        "status": resp.status,
                        "content_type": resp.headers.get("Content-Type", ""),
                        "content_length": resp.headers.get("Content-Length", ""),
                        "final_url": final_url,
                        "final_domain": _safe_domain(final_url),
                        "redirects": redirects,
                    })
                    if resp.status not in (200, 206):
                        entry["error"] = f"HTTP {resp.status} (Không phải 200/206)"
                        diag["attempts"].append(entry)
                        _emit()
                        raise aiohttp.ClientResponseError(
                            resp.request_info, resp.history, status=resp.status
                        )
                    total = int(resp.headers.get("Content-Length", 0))

                    downloaded = 0
                    async with aiofiles.open(filepath, "wb") as f:
                        async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                            await f.write(chunk)
                            downloaded += len(chunk)
                            if progress_cb:
                                await _run_cb(progress_cb, aweme_id, downloaded, total)

                entry["ok"] = True
                entry["downloaded_bytes"] = downloaded
                diag["attempts"].append(entry)
                diag["ok"] = True
                _emit()
                if status_cb:
                    await _run_cb(status_cb, aweme_id, "success", "")
                return True

            except Exception as e:
                if filepath.exists():
                    filepath.unlink()
                entry["error"] = f"{type(e).__name__}: {str(e)[:200]}"
                diag["attempts"].append(entry)
                _emit()
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(2 ** attempt)
                else:
                    if status_cb:
                        await _run_cb(status_cb, aweme_id, "failed", str(e)[:200])
                    return False
    return False


def _safe_domain(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


async def download_videos(
    videos: list[dict],
    output_dir: str = "downloads",
    concurrency: int = 4,
    progress_cb: ProgressCb = None,
    status_cb: StatusCb = None,
    diag_cb: DiagCb = None,
) -> tuple[int, int]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(limit=concurrency * 2, ssl=False)
    results = []
    async with aiohttp.ClientSession(connector=connector) as session:
        results = await asyncio.gather(
            *[_download_one(session, v, out, semaphore, progress_cb, status_cb, diag_cb) for v in videos],
            return_exceptions=True,
        )
    success = sum(1 for r in results if r is True)
    return success, len(results) - success