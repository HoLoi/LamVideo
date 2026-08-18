"""
Smart URL parser — phân loại link video trước khi download.

Douyin:
  /video/<id>                     → single
  /jingxuan?modal_id=<id>         → single (QUAN TRỌNG: ưu tiên modal_id, KHÔNG crawl feed)
  /jingxuan (không modal_id)      → feed
  /user/<sec_uid>                 → user/feed
  v.douyin.com/xxxx               → resolve (cần theo redirect)

Nền tảng khác (yt-dlp):
  youtube / youtu.be / tiktok / vt.tiktok.com / facebook / fb.watch / instagram
  bilibili / b23.tv / m.bilibili.com
  unknown → yt-dlp với cảnh báo.

Bảo mật: chỉ http/https, reject domain giả dạng (douyin.com.evil.com), strip tracking.
"""
import asyncio
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse, parse_qs, urlunparse, urlencode

MAX_DOWNLOAD_ITEMS = 20

DOUYIN_HOSTS = ("douyin.com", "www.douyin.com", "v.douyin.com")
YOUTUBE_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com")
TIKTOK_HOSTS = ("tiktok.com", "www.tiktok.com", "vt.tiktok.com")
FACEBOOK_HOSTS = ("facebook.com", "www.facebook.com", "m.facebook.com", "fb.watch")
INSTAGRAM_HOSTS = ("instagram.com", "www.instagram.com")
BILIBILI_HOSTS = ("bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv")


class URLError(ValueError):
    pass


@dataclass
class ParsedVideoUrl:
    original_url: str
    normalized_url: str
    platform: str               # douyin | youtube | tiktok | facebook | instagram | unknown
    mode: str                   # single | feed | user | resolve
    video_id: str | None
    max_items_effective: int
    is_feed: bool = False
    reason: str = ""
    debug_info: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "original_url": self.original_url,
            "normalized_url": self.normalized_url,
            "platform": self.platform,
            "mode": self.mode,
            "video_id": self.video_id,
            "max_items_effective": self.max_items_effective,
            "is_feed": self.is_feed,
            "reason": self.reason,
            "debug_info": self.debug_info,
        }


def _host_of(url: str) -> str:
    p = urlparse(url)
    return (p.hostname or "").lower()


def _validate(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise URLError("URL trống.")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise URLError(f"Chỉ chấp nhận link http/https (nhận được: {parsed.scheme or 'thiếu'}://).")
    host = (parsed.hostname or "").lower()
    if not host or "." not in host:
        raise URLError("URL không hợp lệ (thiếu domain).")
    return url


def _match_host(host: str, allowed: tuple) -> bool:
    return host in allowed or any(host.endswith("." + h) for h in allowed)


def _clean_query(parsed, keep: tuple) -> str:
    qs = {k: v for k, v in parse_qs(parsed.query).items() if k in keep}
    if not qs:
        return ""
    flat = {k: v[0] if len(v) == 1 else v for k, v in qs.items()}
    return urlencode(flat)


def _parse_douyin(url: str, parsed, max_items: int) -> ParsedVideoUrl:
    path = parsed.path or "/"
    modal_id = parse_qs(parsed.query).get("modal_id", [None])[0]

    if "/video/" in path:
        vid = path.split("/video/")[-1].split("?")[0].strip("/") or None
        return ParsedVideoUrl(
            original_url=url, normalized_url=f"https://www.douyin.com/video/{vid}",
            platform="douyin", mode="single", video_id=vid,
            max_items_effective=1,
            reason="Douyin direct video",
            debug_info={"path": path},
        )
    if modal_id:
        return ParsedVideoUrl(
            original_url=url,
            normalized_url=f"https://www.douyin.com/video/{modal_id}",
            platform="douyin", mode="single", video_id=modal_id,
            max_items_effective=1,
            reason=f"Douyin jingxuan modal_id={modal_id} — sẽ tải ĐÚNG video này (không crawl feed)",
            debug_info={"path": path, "modal_id": modal_id},
        )
    if "/jingxuan" in path or "/recommend" in path:
        return ParsedVideoUrl(
            original_url=url, normalized_url="https://www.douyin.com/jingxuan",
            platform="douyin", mode="feed",
            video_id=None, max_items_effective=max(1, min(int(max_items), MAX_DOWNLOAD_ITEMS)),
            is_feed=True,
            reason="Douyin jingxuan feed — lấy video theo max_items",
            debug_info={"path": path},
        )
    if "/user/" in path:
        return ParsedVideoUrl(
            original_url=url, normalized_url=url,
            platform="douyin", mode="user",
            video_id=None, max_items_effective=max(1, min(int(max_items), MAX_DOWNLOAD_ITEMS)),
            is_feed=True,
            reason="Douyin user profile — lấy video theo max_items",
            debug_info={"path": path},
        )
    return ParsedVideoUrl(
        original_url=url, normalized_url=url,
        platform="douyin", mode="single", video_id=None, max_items_effective=1,
        reason="Douyin unknown path — thử crawl single",
        debug_info={"path": path},
    )


def _parse_youtube(url: str, parsed, max_items: int) -> ParsedVideoUrl:
    path = parsed.path or "/"
    qs = parse_qs(parsed.query)
    vid = qs.get("v", [None])[0]
    if not vid and "/shorts/" in path:
        vid = path.split("/shorts/")[-1].split("/")[0]
    elif not vid and "/embed/" in path:
        vid = path.split("/embed/")[-1].split("/")[0]
    elif not vid and parsed.hostname in ("youtu.be",):
        vid = path.strip("/") or None
    is_playlist = bool(qs.get("list")) and "/playlist" in path
    if is_playlist:
        return ParsedVideoUrl(
            original_url=url, normalized_url=url, platform="youtube", mode="feed",
            video_id=None, max_items_effective=max(1, min(int(max_items), MAX_DOWNLOAD_ITEMS)),
            is_feed=True, reason="YouTube playlist — lấy theo max_items",
            debug_info={"list": qs.get("list", [""])[0]},
        )
    return ParsedVideoUrl(
        original_url=url, normalized_url=url, platform="youtube", mode="single",
        video_id=vid or None, max_items_effective=1,
        reason="YouTube video qua yt-dlp",
        debug_info={"v": vid or ""},
    )


def _parse_generic(url: str, parsed, platform: str, max_items: int) -> ParsedVideoUrl:
    path = parsed.path or "/"
    is_feed = False
    if platform == "tiktok":
        single = bool(re.search(r"/(?:video|photo|v)/", path)) or parsed.hostname in ("vt.tiktok.com",)
        is_feed = not single
    elif platform == "facebook":
        single = "/videos/" in path or "/watch" in path or parsed.hostname in ("fb.watch",)
        is_feed = not single
    elif platform == "instagram":
        single = bool(re.search(r"/(?:reel|p|tv)/", path))
        is_feed = not single
    if single:
        return ParsedVideoUrl(
            original_url=url, normalized_url=url, platform=platform, mode="single",
            video_id=None, max_items_effective=1,
            reason=f"{platform.title()} video qua yt-dlp",
            debug_info={"path": path},
        )
    return ParsedVideoUrl(
        original_url=url, normalized_url=url, platform=platform, mode="feed",
        video_id=None, max_items_effective=max(1, min(int(max_items), MAX_DOWNLOAD_ITEMS)),
        is_feed=True, reason=f"{platform.title()} feed — lấy theo max_items",
        debug_info={"path": path},
    )


def _parse_bilibili(url: str, parsed, max_items: int) -> ParsedVideoUrl:
    path = parsed.path or "/"
    if "/space.bilibili.com" in url or "/space/" in path or "/medialist/" in path:
        return ParsedVideoUrl(
            original_url=url, normalized_url=url, platform="bilibili", mode="feed",
            video_id=None, max_items_effective=max(1, min(int(max_items), MAX_DOWNLOAD_ITEMS)),
            is_feed=True, reason="Bilibili user/space — lấy theo max_items (yt-dlp)",
            debug_info={"path": path},
        )
    vid = None
    m = re.search(r"/video/(BV[0-9A-Za-z]+)", path)
    if m:
        vid = m.group(1)
    return ParsedVideoUrl(
        original_url=url, normalized_url=url, platform="bilibili", mode="single",
        video_id=vid, max_items_effective=1,
        reason="Bilibili video qua yt-dlp (noplaylist=True)",
        debug_info={"path": path, "bv": vid or ""},
    )


def parse_video_url(url: str, max_items: int = 1) -> ParsedVideoUrl:
    url = _validate(url)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    max_items = max(1, min(int(max_items), MAX_DOWNLOAD_ITEMS))

    if _match_host(host, DOUYIN_HOSTS):
        if host == "v.douyin.com":
            return ParsedVideoUrl(
                original_url=url, normalized_url=url, platform="douyin", mode="resolve",
                video_id=None, max_items_effective=1,
                reason="Douyin short link — resolve redirect khi tải",
                debug_info={"host": host},
            )
        return _parse_douyin(url, parsed, max_items)

    if _match_host(host, YOUTUBE_HOSTS):
        return _parse_youtube(url, parsed, max_items)
    if _match_host(host, TIKTOK_HOSTS):
        return _parse_generic(url, parsed, "tiktok", max_items)
    if _match_host(host, FACEBOOK_HOSTS):
        return _parse_generic(url, parsed, "facebook", max_items)
    if _match_host(host, INSTAGRAM_HOSTS):
        return _parse_generic(url, parsed, "instagram", max_items)
    if _match_host(host, BILIBILI_HOSTS):
        return _parse_bilibili(url, parsed, max_items)

    raise URLError(
        f"Domain không được hỗ trợ: {host!r}. Hỗ trợ: douyin.com, v.douyin.com, "
        "youtube.com, youtu.be, tiktok.com, vt.tiktok.com, facebook.com, fb.watch, "
        "instagram.com, bilibili.com, m.bilibili.com, b23.tv."
    )


async def resolve_douyin_short(url: str, timeout: float = 20.0) -> tuple[str, str]:
    """Theo redirect của v.douyin.com (aiohttp, không cookie). Trả (final_url, reason)."""
    import aiohttp
    current = url
    reason = ""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            for _ in range(6):
                try:
                    async with session.get(current, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                        loc = resp.headers.get("Location")
                        if resp.status in (301, 302, 303, 307, 308) and loc:
                            current = loc if loc.startswith("http") else urljoin(current, loc)
                            continue
                        final = str(resp.url)
                        if "video/" in final or "modal_id=" in final:
                            reason = "Resolve ok — đã tìm thấy video cụ thể"
                        else:
                            reason = "Resolve được redirect nhưng không phải trang video cụ thể"
                        return final, reason
                except Exception as e:
                    reason = f"Resolve lỗi: {type(e).__name__}: {e}"
                    break
        return current, reason or "Quá nhiều redirect (6)"
    except ImportError as e:
        return url, f"aiohttp chưa cài: {e}"


def _normalize_after_resolve(final_url: str) -> str:
    """Chuẩn hóa URL sau redirect: modal_id=... → /video/<id>."""
    if not final_url:
        return final_url
    try:
        p = urlparse(final_url)
        if "douyin.com" in (p.hostname or "").lower():
            modal = parse_qs(p.query).get("modal_id", [None])[0]
            if modal:
                return f"https://www.douyin.com/video/{modal}"
            if "/video/" in p.path:
                vid = p.path.split("/video/")[-1].split("?")[0].strip("/")
                if vid:
                    return f"https://www.douyin.com/video/{vid}"
    except Exception:
        pass
    return final_url