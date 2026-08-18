"""
yt-dlp downloader — YouTube / TikTok / Facebook / Instagram / v.v.

Bảo mật & giới hạn:
  - Dùng Python API (yt_dlp), KHÔNG shell=True, không câu lệnh shell.
  - KHÔNG dùng cookies (không đọc browser cookies / account).
  - noplaylist=True với single video; playlist/feed theo max_items (<= 20).
  - Xuất ra staging VIDEO_TMP_DIR/<job_id>, sau khi xong mới move vào library
    data/videos/<video_id>/ với metadata + source_info.
"""
import asyncio
import json
import shutil
import traceback
from pathlib import Path

from config import VIDEO_TMP_DIR, MAX_VIDEO_ITEMS, VIDEO_LIBRARY_DIR
from video_jobs import job_manager, format_job_error
from video_dl.transcribe import resolve_ffmpeg
import video_library as lib

VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi", ".flv", ".3gp"}
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _safe_id(raw_id: str) -> str:
    import hashlib
    cleaned = "".join(c for c in (raw_id or "") if c.isalnum() or c in "._-").strip(".")
    return cleaned[:64] or hashlib.md5(raw_id.encode("utf-8", "ignore")).hexdigest()[:16]


def _merge_to_mp4(src: Path, dst: Path) -> None:
    """Remux/convert bất kỳ video ext → mp4 bằng ffmpeg (copy nếu được, không thì re-encode)."""
    ffmpeg = resolve_ffmpeg()
    args = [str(ffmpeg), "-y", "-i", str(src)]
    args += ["-c", "copy", str(dst)]
    proc = __import__("subprocess").run(
        args, stdout=__import__("subprocess").DEVNULL, stderr=__import__("subprocess").PIPE, timeout=900
    )
    if proc.returncode != 0:
        # fallback re-encode
        sub = __import__("subprocess")
        args2 = [str(ffmpeg), "-y", "-i", str(src),
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                 "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dst)]
        proc2 = sub.run(args2, stdout=sub.DEVNULL, stderr=sub.PIPE, timeout=1800)
        if proc2.returncode != 0:
            raise RuntimeError(f"Không convert được video sang mp4: {proc2.stderr.decode('utf-8', 'ignore')[-300:]}")


def _import_video_sync(info: dict, tmp: Path) -> dict:
    """Move 1 video từ staging vào library. CHẠY TRONG WORKER THREAD (to_thread)."""
    vid = _safe_id(info.get("id") or "")
    if not vid:
        raise RuntimeError("yt-dlp không trả về video id (info extractor error).")
    videos_dir = VIDEO_LIBRARY_DIR
    vdir = videos_dir / vid
    if vdir.exists():
        shutil.rmtree(vdir)
    vdir.mkdir(parents=True, exist_ok=True)

    raw_files = [f for f in tmp.iterdir() if f.name.startswith(vid + ".")]
    mp4s = [f for f in raw_files if f.suffix == ".mp4"]
    vids = [f for f in raw_files if f.suffix in VIDEO_EXTS]
    src = mp4s[0] if mp4s else (vids[0] if vids else None)
    if not src:
        raise RuntimeError(f"Không tìm thấy file video đã tải cho id {vid}.")
    index = vdir / "index.mp4"
    if src.suffix == ".mp4":
        shutil.move(str(src), str(index))
    else:
        _merge_to_mp4(src, index)
        src.unlink(missing_ok=True)

    thumb = None
    for f in raw_files:
        if f.suffix in IMG_EXTS:
            thumb = vdir / f"thumbnail{f.suffix}"
            shutil.move(str(f), str(thumb))
            break
    for f in raw_files:
        if f.suffix == ".json":
            shutil.move(str(f), str(vdir / "ytdlp_info.json"))
            break

    duration = info.get("duration")
    try:
        duration = round(float(duration)) if duration else None
    except (TypeError, ValueError):
        duration = None
    size = index.stat().st_size if index.exists() else 0
    meta = {
        "video_id": vid,
        "source_url": info.get("webpage_url") or info.get("original_url") or "",
        "platform": "unknown",
        "title": info.get("title") or f"video_{vid}",
        "author": info.get("uploader") or info.get("channel") or info.get("creator") or "",
        "duration": duration,
        "thumbnail_path": str(thumb.relative_to(videos_dir)) if thumb else None,
        "video_path": f"{vid}/index.mp4",
        "source_repo": "yt-dlp (Python API)",
        "extractor": info.get("extractor_key") or info.get("extractor") or "yt-dlp",
        "status": "downloaded",
        "created_at": lib.now_iso(),
    }
    for ext, plat in (("youtube", "youtube"), ("tiktok", "tiktok"), ("facebook", "facebook"),
                      ("instagram", "instagram"), ("bilibili", "bilibili"), ("douyin", "douyin")):
        if (info.get("extractor") or "").lower().startswith(ext):
            meta["platform"] = plat
            break
    vdir.joinpath("metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    entry = {
        "id": vid,
        "url": meta["source_url"],
        "platform": meta["platform"],
        "title": meta["title"],
        "author": meta["author"],
        "status": "downloaded",
        "video_path": f"data/videos/{vid}/index.mp4",
        "thumbnail_path": meta["thumbnail_path"],
        "transcript_path": None,
        "created_at": meta["created_at"],
        "duration": meta["duration"],
        "source_repo": meta["source_repo"],
        "extractor": meta["extractor"],
    }
    return {"entry": entry, "meta": meta}


async def _salvage_staging(tmp: Path, job_id: str) -> list[str]:
    """Nhập video đã tải trong staging (khi yt-dlp raise 'Maximum number of downloads reached').

    Mỗi video trong staging có <id>.info.json — dùng chính info đó để import.
    """
    imported: list[str] = []
    for f in tmp.iterdir():
        if f.suffix != ".json":
            continue
        try:
            info = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(info, dict) or not info.get("id"):
            continue
        try:
            res = await asyncio.to_thread(_import_video_sync, info, tmp)
            await lib.upsert_video(res["entry"])
            imported.append(res["entry"]["id"])
        except Exception as e:
            print(f"[ytdlp:{job_id}] salvage skip {info.get('id')}: {e!r}")
    return imported


async def run_ytdlp_job(job_id: str, url: str, platform: str,
                        max_items: int = 1, parsed: dict = None) -> None:
    """Chạy job download qua yt-dlp. job_id do caller tạo (prefix 'dl_')."""
    parsed = parsed or {}
    is_feed = bool(parsed.get("is_feed")) or parsed.get("mode") in ("feed", "user")
    limit = max(1, min(int(max_items or 1), MAX_VIDEO_ITEMS))
    tmp = VIDEO_TMP_DIR / job_id
    tmp.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()

    def _hook(d):
        if d.get("status") == "downloading" and d.get("total_bytes"):
            try:
                done = d.get("downloaded_bytes", 0)
                pct = int(done * 100 / d["total_bytes"])
                asyncio.run_coroutine_threadsafe(
                    job_manager.update(job_id, progress=min(pct, 99)), loop)
            except Exception:
                pass

    opts = {
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "outtmpl": str(tmp / "%(id)s.%(ext)s"),
        "noplaylist": not is_feed,
        "playlistend": limit if is_feed else 1,
        "max_downloads": limit,
        "quiet": True,
        "no_warnings": True,
        "writethumbnail": True,
        "writeinfojson": True,
        "nocheckcertificate": False,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "progress_hooks": [_hook],
        "noprogress": True,
    }
    if platform == "youtube":
        # mặc định bị YouTube chặn (HTTP 403) — dùng player_client tv/android
        opts["extractor_args"] = {"youtube": {"player_client": ["tv", "android"]}}
    fpath = resolve_ffmpeg()
    if fpath and Path(fpath).exists():
        opts["ffmpeg_location"] = str(Path(fpath).parent)

    def _run():
        import yt_dlp
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return info

    try:
        await job_manager.update(job_id, status="running", progress=2,
                                 message=f"Đang tải (yt-dlp, {platform})...")
        try:
            info = await asyncio.to_thread(_run)
        except ImportError as e:
            await job_manager.finish_fail(
                job_id, "Thiếu dependency yt-dlp — chạy: pip install yt-dlp. "
                        f"({type(e).__name__}: {e})")
            return
        except Exception as e:
            msg = str(e).strip() or f"{type(e).__name__}: {e}"
            short = msg[:600]
            if "Maximum number of downloads reached" in msg:
                # yt-dlp đã tải đủ max_downloads nhưng raise lỗi này ở playlist —
                # tận dụng file đã tải trong staging (mỗi video có info.json).
                salvaged = await _salvage_staging(tmp, job_id)
                if salvaged:
                    await job_manager.update(job_id, video_ids=salvaged, parsed=parsed,
                                             strategy="yt_dlp", progress=100)
                    await job_manager.finish_ok(
                        job_id, salvaged,
                        f"Đạt giới hạn max_downloads — đã nhập {len(salvaged)} video đã tải: "
                        f"{', '.join(salvaged)}.")
                    return
            hint = ""
            if "Sign in" in msg or "login" in msg.lower():
                hint = " [Link yêu cầu đăng nhập — tạm thời không hỗ trợ cookies/login.]"
            elif "Unsupported URL" in msg:
                hint = " [yt-dlp không hỗ trợ URL này.]"
            await job_manager.finish_fail(job_id, f"Lỗi yt-dlp: {short}{hint}")
            return

        entries = info.get("entries") or [info] if isinstance(info, dict) else [info]
        entries = [e for e in entries if e and isinstance(e, dict)]
        imported = []
        for e in entries:
            res = await asyncio.to_thread(_import_video_sync, e, tmp)
            await lib.upsert_video(res["entry"])
            imported.append({
                "video_id": res["entry"]["id"],
                "title": res["meta"]["title"],
                "platform": res["meta"]["platform"],
                "duration": res["meta"]["duration"],
                "size": res["entry"].get("size", 0),
            })
        ids = [r["video_id"] for r in imported]
        await job_manager.update(job_id, video_ids=ids, parsed=parsed,
                                 strategy="yt_dlp", progress=100)
        await job_manager.finish_ok(
            job_id, ids,
            f"Tải xong {len(ids)} video qua yt-dlp: {', '.join(ids) or 'không có'}.")
    except Exception as e:
        tb = traceback.format_exc()
        await job_manager.finish_fail(job_id, format_job_error(e) + f"\n{tb[-500:]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
