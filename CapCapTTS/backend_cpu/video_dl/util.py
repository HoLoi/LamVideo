"""
Utils dùng chung cho video_dl: tìm ffmpeg/ffprobe, probe thời lượng video.

Chỉ chạy subprocess với danh sách argument cố định (không shell=True),
không bao giờ đưa input user vào shell.
"""
import subprocess
from pathlib import Path


def run_async_in_worker(coro_factory, *args, **kwargs):
    """Chạy coroutine trong thread riêng với event loop riêng (thread-blocking).

    Trên Windows loop riêng này luôn là ProactorEventLoop (hỗ trợ subprocess —
    bắt buộc cho Playwright driver). Event loop chính của FastAPI có thể đang là
    SelectorEventLoop (uvicorn --reload trên Windows ép Selector) nên Playwright
    KHÔNG thể chạy trên loop chính: create_subprocess_exec sẽ ném
    NotImplementedError.

    Cách dùng (luôn truyền keyword args để tránh nhầm thứ tự tham số):
        result = await asyncio.to_thread(
            run_async_in_worker, crawler.crawl_single_url,
            url=url, headless=headless, cookies_file=None, log=_log,
        )
    coro_factory được gọi NGAY TRONG worker thread để coroutine được tạo đúng loop.
    """
    import asyncio
    import sys

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro_factory(*args, **kwargs))
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
        try:
            loop.close()
        except Exception:
            pass


def _playwright_ffmpeg() -> Path | None:
    import os
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    if base.exists():
        for d in base.glob("ffmpeg-*"):
            exe = d / "ffmpeg-win64.exe"
            if exe.exists():
                return exe
    return None


def find_ffmpeg() -> str:
    """Trả về path ffmpeg khả dụng: config FFMPEG_DIR → playwright bundle → PATH."""
    try:
        from config import FFMPEG_DIR
        for name in ("ffmpeg.exe", "ffmpeg"):
            p = Path(FFMPEG_DIR) / name
            if p.exists():
                return str(p)
    except Exception:
        pass
    pw = _playwright_ffmpeg()
    if pw:
        return str(pw)
    return "ffmpeg"


def find_ffprobe() -> str | None:
    try:
        from config import FFMPEG_DIR
        p = Path(FFMPEG_DIR) / "ffprobe.exe"
        if p.exists():
            return str(p)
    except Exception:
        pass
    return None


def probe_duration(video_path: str) -> float | None:
    """Đọc thời lượng video (giây) bằng ffprobe hoặc fallback ffmpeg -i. Không dùng shell."""
    from pathlib import Path

    vp = Path(video_path)
    if not vp.exists() or vp.stat().st_size == 0:
        return None

    ffprobe = find_ffprobe()
    if ffprobe:
        try:
            out = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(vp)],
                capture_output=True, text=True, timeout=30,
            )
            if out.returncode == 0 and out.stdout.strip():
                return round(float(out.stdout.strip()), 2)
        except Exception:
            pass

    ffmpeg = find_ffmpeg()
    try:
        out = subprocess.run(
            [ffmpeg, "-i", str(vp)], capture_output=True, text=True, timeout=30,
        )
        for line in out.stderr.splitlines():
            line = line.strip()
            if "Duration:" in line:
                part = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = part.split(":")
                return round(int(h) * 3600 + int(m) * 60 + float(s), 2)
    except Exception:
        pass
    return None