"""
Video downloader service — orchestrate download → video library.

MVP-5: đa nền tảng.
- Douyin: crawl (playwright) qua video_dl — đúng video theo URL (modal_id / /video/<id>),
  verify id trước khi import.
- YouTube / TikTok / Facebook / ...: yt-dlp Python API (không shell, không cookies).
Không dùng shell. Không cookie mặc định. Không Chrome profile thật.
"""
import asyncio
import json
import shutil
import traceback
from pathlib import Path

from config import VIDEO_TMP_DIR, MAX_VIDEO_ITEMS
from video_dl import crawler, downloader
from video_dl.util import probe_duration, run_async_in_worker
from video_jobs import job_manager, format_job_error
from video_url_parser import (
    parse_video_url, resolve_douyin_short, _normalize_after_resolve, URLError,
)
import video_library as lib

VALID_SOURCE_TYPES = ("auto", "douyin", "youtube", "tiktok", "facebook", "bilibili", "ytdlp")
VALID_STRATEGIES = ("auto", "douyin_crawler", "yt_dlp")


def validate_url(url: str, source_type: str = "auto") -> str:
    """Validate + normalize URL. Trả normalized_url (modal → /video/<id>). Raise URLError."""
    parsed = parse_video_url(url)
    st = (source_type or "auto").strip().lower()
    if st not in VALID_SOURCE_TYPES:
        raise URLError(f"source_type không hợp lệ: {st!r}. Hỗ trợ: {', '.join(VALID_SOURCE_TYPES)}.")
    if st == "auto":
        return parsed.normalized_url
    if st == "douyin" and parsed.platform != "douyin":
        raise URLError(f"source_type=douyin nhưng URL là nền tảng {parsed.platform}.")
    if st in ("youtube", "tiktok", "facebook", "bilibili") and parsed.platform not in (st, "unknown"):
        raise URLError(f"source_type={st} nhưng URL là nền tảng {parsed.platform}.")
    if st == "ytdlp" and parsed.platform == "douyin":
        raise URLError("source_type=ytdlp không dùng cho Douyin (dùng douyin_crawler).")
    return parsed.normalized_url


def _job_log(job_id: str, msg: str):
    print(f"[video_job:{job_id}] {msg}")


async def _crawl_videos(parsed, max_items: int, headless: bool, job_id: str):
    """Crawl danh sách video Douyin từ ParsedVideoUrl. Trả list dict video.

    single (video/modal_id): chỉ crawl ĐÚNG video — dùng normalized URL
    (/video/<id>), max_items luôn = 1, không bao giờ quét feed.
    Playwright spawn subprocess (node driver + Chromium) nên phần crawl phải
    chạy trong worker thread với event loop riêng (Proactor trên Windows) —
    loop chính của FastAPI có thể là Selector (uvicorn --reload) và không hỗ trợ
    subprocess (NotImplementedError). Xem video_dl.util.run_async_in_worker.
    """
    url = parsed.normalized_url
    is_single = parsed.mode == "single" or max_items <= 1
    mode_label = "single" if is_single else "feed"

    def _log(msg: str):
        _job_log(job_id, msg)

    try:
        if is_single:
            _job_log(job_id, f"Crawl single video: {url}")
            v = await asyncio.to_thread(
                run_async_in_worker, crawler.crawl_single_url,
                url=url, headless=headless, cookies_file=None, log=_log,
            )
            return [v] if v else []

        async def _collect_jingxuan():
            videos = []
            async for v in crawler.crawl_jingxuan(
                max_videos=max_items,
                scroll_count=max(1, min(10, max_items)),
                headless=headless,
                cookies_file=None,
                start_url=url,
                log=_log,
            ):
                videos.append(v)
                if len(videos) >= max_items:
                    break
            return videos

        _job_log(job_id, f"Crawl feed Douyin (tối đa {max_items}): {url}")
        return await asyncio.to_thread(run_async_in_worker, _collect_jingxuan)
    except ImportError as e:
        raise RuntimeError(
            "Thiếu dependency video: chạy lệnh pip install -r backend_cpu/requirements.txt (playwright). "
            f"Chi tiết: {e}"
        ) from e


async def _staging_move_to_library(tmp_dir: Path, url: str, job_id: str) -> list[str]:
    """Di chuyển file từ staging (author__title__aweme_id/index.mp4) vào library theo video_id."""
    video_ids: list[str] = []
    for folder in tmp_dir.iterdir():
        if not folder.is_dir():
            continue
        mp4 = folder / "index.mp4"
        if not mp4.exists():
            continue
        aweme_id = folder.name.rsplit("__", 1)[-1]
        if not lib.is_valid_video_id(aweme_id):
            _job_log(job_id, f"Bỏ qua aweme_id không hợp lệ: {folder.name}")
            continue
        meta_path = folder / "metadata.json"
        crawl_meta = {}
        if meta_path.exists():
            try:
                crawl_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                crawl_meta = {}
        title = crawl_meta.get("title") or folder.name
        author = crawl_meta.get("author") or "unknown"

        target = lib.ensure_video_dir(aweme_id)
        if (target / "index.mp4").exists():
            (target / "index.mp4").unlink()
        shutil.move(str(mp4), str(target / "index.mp4"))
        if meta_path.exists():
            if (target / "metadata.json").exists():
                (target / "metadata.json").unlink()
            shutil.move(str(meta_path), str(target / "metadata.json"))

        # probe duration (không bắt buộc, sai → None)
        duration = None
        try:
            duration = probe_duration(str(target / "index.mp4"))
        except Exception:
            duration = None

        entry = {
            "id": aweme_id,
            "url": crawl_meta.get("detail_url") or url,
            "platform": "douyin",
            "title": title,
            "author": author,
            "status": "downloaded",
            "video_path": f"data/videos/{aweme_id}/index.mp4",
            "thumbnail_path": None,
            "transcript_path": None,
            "created_at": lib.now_iso(),
            "duration": duration,
            "source_repo": "ai-video-downloader-main (vendored video_dl)",
            "function_used": "crawl_single_url / crawl_jingxuan + download_videos",
        }
        await lib.upsert_video(entry)
        video_ids.append(aweme_id)
        _job_log(job_id, f"Đã nhập library: {aweme_id}")
    return video_ids


async def _verify_single_requested(tmp_dir: Path, expected_id: str) -> None:
    """Xác nhận video đã download == video yêu cầu. Mismatch → raise (job fail, không import)."""
    if not expected_id:
        return
    found = None
    for folder in tmp_dir.iterdir():
        if not folder.is_dir():
            continue
        if (folder / "index.mp4").exists():
            found = folder.name.rsplit("__", 1)[-1]
            break
    if found and found != expected_id:
        raise RuntimeError(
            f"Downloaded video id {found} does not match requested id {expected_id}"
        )


async def run_download_job(job_id: str, url: str, source_type: str, max_items: int,
                           headless: bool, download_strategy: str = "auto",
                           parsed=None) -> None:
    """Background job download. Cập nhật trạng thái qua job_manager."""
    try:
        parsed = parsed or parse_video_url(url, max_items)
    except URLError as e:
        await job_manager.finish_fail(job_id, str(e))
        return

    strategy = (download_strategy or "auto").strip().lower()
    if strategy == "auto":
        strategy = "yt_dlp" if parsed.platform != "douyin" else "douyin_crawler"
    if strategy not in VALID_STRATEGIES:
        await job_manager.finish_fail(job_id, f"download_strategy không hợp lệ: {download_strategy!r}.")
        return

    try:
        max_items = max(1, min(int(max_items), MAX_VIDEO_ITEMS))
    except (ValueError, TypeError):
        await job_manager.finish_fail(job_id, "max_items phải là số nguyên.")
        return

    tmp_dir = VIDEO_TMP_DIR / job_id

    # Short link Douyin (v.douyin.com): resolve redirect để biết đúng video
    if parsed.mode == "resolve":
        await job_manager.update(job_id, status="running", progress=2,
                                 parsed=parsed.to_dict(), strategy=strategy,
                                 message="Resolve link rút gọn Douyin...")
        final_url, reason = await resolve_douyin_short(url)
        final_url = _normalize_after_resolve(final_url)
        try:
            resolved = parse_video_url(final_url, max_items)
            if resolved.mode != "resolve":
                parsed = resolved
                _job_log(job_id, f"Short link → {resolved.normalized_url} ({reason})")
            else:
                _job_log(job_id, f"Không resolve được tới video cụ thể: {reason}")
        except URLError:
            _job_log(job_id, f"Short link resolve không parse được: {reason}")
    else:
        await job_manager.update(job_id, status="running", progress=3,
                                 parsed=parsed.to_dict(), strategy=strategy,
                                 message=f"Chuẩn bị tải ({parsed.platform} / {parsed.mode})...")

    # ─── Douyin diagnostics (MVP-6) ───
    steps: list[dict] = []
    cdn_diags: dict[str, dict] = {}

    def _step(stage: str, ok: bool, detail: str = "", **extra):
        steps.append({"stage": stage, "ok": bool(ok), "detail": detail, **extra})

    def _classify() -> str:
        """Phân loại nguyên nhân fail dựa trên steps + cdn_diags."""
        for s in steps:
            if s.get("stage") == "cdn_request" and not s.get("ok"):
                d = s.get("detail", "")
                if "HTTP 403" in d or "HTTP 401" in d or "HTTP 451" in d:
                    return "douyin_cdn_blocked_auth"
                if "HTTP 429" in d:
                    return "douyin_rate_limited"
                if "HTML" in d:
                    return "douyin_anti_bot_html"
        for s in steps:
            if s.get("stage") == "crawl" and not s.get("ok"):
                if "Timeout" in s.get("detail", "") or "timed out" in s.get("detail", "").lower():
                    return "network_timeout"
                return "dead_link_or_anti_bot"
        return "unknown_error"

    def _build_diags(error: str = "") -> dict:
        return {
            "steps": steps,
            "cdn": cdn_diags,
            "error": error[:500],
            "conclusion": _classify() if error else "ok",
        }

    try:
        if strategy == "yt_dlp":
            from video_ytdlp_downloader import run_ytdlp_job
            await run_ytdlp_job(
                job_id, parsed.normalized_url or url, parsed.platform,
                max_items, parsed.to_dict(),
            )
            return

        # ---- Douyin crawler ----
        _step("parse_url", True,
              f"platform={parsed.platform}, mode={parsed.mode}, video_id={parsed.video_id or '-'}",
              normalized_url=parsed.normalized_url)
        await job_manager.update(job_id, progress=5, message="Đang crawl video Douyin...")
        try:
            videos = await _crawl_videos(parsed, max_items, headless, job_id)
            _step("crawl", len(videos) > 0,
                  f"tìm thấy {len(videos)} video (mode={parsed.mode})",
                  url=parsed.normalized_url)
        except Exception as e:
            _step("crawl", False, f"{type(e).__name__}: {str(e)[:200]}")
            raise
        if not videos:
            raise RuntimeError("Không tìm thấy video nào từ link này (có thể do captcha/login — hãy bỏ tick 'Ẩn browser').")

        await job_manager.update(job_id, progress=10, message=f"Đang tải {len(videos)} video...")
        tmp_dir.mkdir(parents=True, exist_ok=True)

        n = len(videos)
        completed = 0

        async def _progress(aweme_id: str, done: int, total: int):
            frac = (done / total) if total and total > 0 else 0.0
            pct = int(10 + (completed + frac) / n * 89)
            await job_manager.update(job_id, progress=min(pct, 99), message=f"Đang tải {aweme_id}...")

        async def _status(aweme_id: str, status: str, msg: str):
            nonlocal completed
            if status in ("success", "skipped", "failed"):
                completed += 1
            if status == "failed":
                _job_log(job_id, f"Tải thất bại {aweme_id}: {msg}")

        async def _diag(aweme_id: str, diag: dict):
            cdn_diags[aweme_id] = diag
            for a in diag.get("attempts", []):
                if a.get("label") != "http":
                    continue
                st = a.get("status")
                if st and st not in (200, 206):
                    _step("cdn_request", False,
                          f"Douyin CDN blocked this request: HTTP {st}",
                          aweme_id=aweme_id, status=st,
                          content_type=a.get("content_type", ""))
                ct = (a.get("content_type") or "").lower()
                if st in (200, 206) and ("text/html" in ct or "html" in ct):
                    _step("cdn_request", False,
                          "Douyin CDN trả HTML thay vì video (anti-bot) — HTTP 200",
                          aweme_id=aweme_id, content_type=ct)
                if a.get("error"):
                    _step("cdn_request", False,
                          f"Douyin CDN error: {a['error']}", aweme_id=aweme_id)

        success, fail = await downloader.download_videos(
            videos, output_dir=str(tmp_dir), concurrency=2,
            progress_cb=_progress, status_cb=_status, diag_cb=_diag,
        )
        if success == 0:
            raise RuntimeError("Tải thất bại toàn bộ video (kiểm tra mạng / link).")

        # QUAN TRỌNG: verify đúng video trước khi move vào library
        if parsed.mode == "single" and parsed.video_id:
            await _verify_single_requested(tmp_dir, parsed.video_id)
        _step("verify_single", True, f"video_id khớp yêu cầu {parsed.video_id}")

        video_ids = await _staging_move_to_library(tmp_dir, parsed.normalized_url or url, job_id)
        if not video_ids:
            raise RuntimeError("Không có video nào được đưa vào library.")
        _step("import_library", True, f"đã nhập {len(video_ids)} video")

        await job_manager.update(job_id, video_ids=video_ids, progress=100,
                                 diagnostics=_build_diags())
        await job_manager.finish_ok(job_id, video_ids, f"Tải xong {len(video_ids)} video.")
    except Exception as e:
        _job_log(job_id, f"LỖI: {e!r}")
        _job_log(job_id, traceback.format_exc())
        await job_manager.update(job_id, diagnostics=_build_diags(str(e)))
        await job_manager.finish_fail(job_id, format_job_error(e))
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)