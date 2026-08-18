"""
Async downloader: tải video, retry tự động, ghi index.csv real-time.
"""
import asyncio
import re
from pathlib import Path

import aiofiles
import aiohttp
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from .store import upsert_download

console = Console()

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


def _safe_filename(text: str, max_len: int = 80) -> str:
    text = re.sub(r'[\\/:*?"<>|]', "_", text)
    return re.sub(r"\s+", " ", text).strip()[:max_len] or "video"


async def _download_one(
    session: aiohttp.ClientSession,
    video: dict,
    output_dir: Path,
    progress: Progress,
    semaphore: asyncio.Semaphore,
) -> bool:
    aweme_id = video["aweme_id"]
    url = video["download_url"]
    author = _safe_filename(video.get("author", "unknown"))
    title = _safe_filename(video.get("title", aweme_id))
    folder = output_dir / f"{author}__{title}__{aweme_id}"
    folder.mkdir(parents=True, exist_ok=True)
    filepath = folder / "index.mp4"

    # Write metadata.json
    import json
    meta = {
        "aweme_id": aweme_id,
        "author": video.get("author", "unknown"),
        "title": video.get("title", ""),
        "download_url": url,
        "detail_url": f"https://www.douyin.com/video/{aweme_id}",
    }
    (folder / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    await upsert_download(
        aweme_id=aweme_id,
        author=video.get("author", "unknown"),
        title=video.get("title", ""),
        local_path=str(filepath),
        download_url=url,
        status="pending",
    )

    if filepath.exists() and filepath.stat().st_size > 10_000:
        console.print(f"[dim]Bỏ qua (đã có): {filepath.name}[/dim]")
        await upsert_download(
            aweme_id=aweme_id, author=video.get("author", "unknown"),
            title=video.get("title", ""), local_path=str(filepath),
            download_url=url, status="success",
        )
        return True

    task_id: TaskID | None = None

    async with semaphore:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with session.get(
                    url, headers=_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=180, sock_read=60),
                ) as resp:
                    if resp.status not in (200, 206):
                        raise aiohttp.ClientResponseError(
                            resp.request_info, resp.history, status=resp.status
                        )
                    total = int(resp.headers.get("Content-Length", 0))
                    label = f"[cyan]{filepath.name[:45]}[/cyan]"
                    if task_id is None:
                        task_id = progress.add_task(label, total=total or None)
                    else:
                        progress.reset(task_id, total=total or None)

                    downloaded = 0
                    async with aiofiles.open(filepath, "wb") as f:
                        async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                            await f.write(chunk)
                            downloaded += len(chunk)
                            progress.update(task_id, advance=len(chunk))

                progress.update(
                    task_id,
                    description=f"[green]✓ {filepath.name[:45]}[/green]",
                    completed=total or progress._tasks[task_id].completed,
                )
                await upsert_download(
                    aweme_id=aweme_id, author=video.get("author", "unknown"),
                    title=video.get("title", ""), local_path=str(filepath),
                    download_url=url, status="success",
                )
                return True

            except Exception as e:
                if filepath.exists():
                    filepath.unlink()
                if attempt < MAX_RETRIES:
                    if task_id is not None:
                        progress.update(task_id, description=f"[yellow]↻ retry {attempt} {filepath.name[:35]}[/yellow]")
                    await asyncio.sleep(2 ** attempt)
                else:
                    if task_id is not None:
                        progress.update(task_id, description=f"[red]✗ {filepath.name[:35]} — {str(e)[:60]}[/red]")
                    await upsert_download(
                        aweme_id=aweme_id, author=video.get("author", "unknown"),
                        title=video.get("title", ""), local_path=str(filepath),
                        download_url=url, status="failed",
                    )
                    return False
    return False


async def download_videos(
    videos: list[dict],
    output_dir: str = "downloads",
    concurrency: int = 4,
) -> tuple[int, int]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(limit=concurrency * 2, ssl=False)
    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(), DownloadColumn(), TransferSpeedColumn(), TimeRemainingColumn(),
        console=console,
    )
    with progress:
        async with aiohttp.ClientSession(connector=connector) as session:
            results = await asyncio.gather(
                *[_download_one(session, v, out, progress, semaphore) for v in videos],
                return_exceptions=True,
            )
    success = sum(1 for r in results if r is True)
    return success, len(results) - success
