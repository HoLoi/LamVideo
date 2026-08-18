"""
Douyin crawler: Playwright intercept XHR/fetch, tìm aweme_list trong response.
"""
import json
import re
from typing import AsyncGenerator
from urllib.parse import urlparse, parse_qs

from playwright.async_api import async_playwright, Response
from rich.console import Console

console = Console()

JINGXUAN_URL = "https://www.douyin.com/jingxuan"

_BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-web-security",
]
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_STEALTH_SCRIPT = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
)


def _find_aweme_list(data) -> list:
    if isinstance(data, list):
        if data and isinstance(data[0], dict) and "aweme_id" in data[0]:
            return data
        for item in data:
            result = _find_aweme_list(item)
            if result:
                return result
    elif isinstance(data, dict):
        for key in ("aweme_list", "item_list", "aweme_info", "data"):
            if key in data:
                result = _find_aweme_list(data[key])
                if result:
                    return result
        for v in data.values():
            if isinstance(v, (dict, list)):
                result = _find_aweme_list(v)
                if result:
                    return result
    return []


def _get_download_url(video: dict) -> str | None:
    for key in ("play_addr", "play_addr_h264", "download_addr", "play_addr_lowbr"):
        addr = video.get(key) or {}
        urls = addr.get("url_list") or []
        if urls:
            url = urls[0]
            url = re.sub(r"[?&](logo_name|watermark|wm_url)[^&]*", "", url)
            return url
    return None


def _parse_aweme(item: dict) -> dict | None:
    aweme_id = str(item.get("aweme_id") or item.get("id") or "")
    if not aweme_id:
        return None
    desc = (item.get("desc") or "").strip() or f"video_{aweme_id}"
    author_name = (item.get("author") or {}).get("nickname") or "unknown"
    download_url = _get_download_url(item.get("video") or {})
    if not download_url:
        return None
    return {
        "aweme_id": aweme_id,
        "title": desc[:100],
        "author": author_name,
        "download_url": download_url,
    }


def _is_douyin_api(url: str) -> bool:
    return (
        "douyin.com" in url
        and any(x in url for x in ["/aweme/", "/api/", "/web/"])
        and url.startswith("https")
    )


async def _make_context(p, headless: bool, cookies_file: str | None):
    browser = await p.chromium.launch(headless=headless, args=_BROWSER_ARGS)
    context = await browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent=_USER_AGENT,
        locale="zh-CN",
    )
    await context.add_init_script(_STEALTH_SCRIPT)
    await context.add_init_script(
        "window.addEventListener('DOMContentLoaded', () => localStorage.setItem('douyin_web_hide_guide', '1'))"
    )
    if cookies_file:
        try:
            with open(cookies_file) as f:
                await context.add_cookies(json.load(f))
            console.print(f"[green]Loaded cookies từ {cookies_file}[/green]")
        except Exception as e:
            console.print(f"[yellow]Không load được cookies: {e}[/yellow]")
    return browser, context


async def _crawl(
    start_url: str,
    max_videos: int,
    scroll_count: int,
    headless: bool,
    cookies_file: str | None,
    stop_when_no_new: int = 6,
) -> list[dict]:
    collected: dict[str, dict] = {}

    async with async_playwright() as p:
        browser, context = await _make_context(p, headless, cookies_file)
        page = await context.new_page()

        async def handle_response(response: Response):
            if len(collected) >= max_videos or not _is_douyin_api(response.url):
                return
            if "json" not in response.headers.get("content-type", ""):
                return
            try:
                data = await response.json()
                items = _find_aweme_list(data)
                new_count = 0
                for item in items:
                    v = _parse_aweme(item)
                    if v and v["aweme_id"] not in collected:
                        collected[v["aweme_id"]] = v
                        new_count += 1
                if new_count:
                    short_url = response.url.split("?")[0].replace("https://www.douyin.com", "")
                    console.print(
                        f"[green]+{new_count} video[/green] từ [dim]{short_url}[/dim] "
                        f"(tổng: {len(collected)})"
                    )
            except Exception:
                pass

        page.on("response", handle_response)
        console.print(f"[cyan]Mở trang {start_url}...[/cyan]")
        try:
            await page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            console.print(f"[yellow]Timeout (bình thường): {e}[/yellow]")

        await page.wait_for_timeout(4000)
        await page.evaluate("""
            localStorage.setItem('douyin_web_hide_guide', '1');
            document.querySelectorAll('[id^="login-full-panel-"]').forEach(el => el.remove());
        """)

        prev, no_new = 0, 0
        for i in range(scroll_count):
            if len(collected) >= max_videos:
                break
            if no_new >= stop_when_no_new:
                console.print("[yellow]Không tìm thêm được video mới, dừng scroll.[/yellow]")
                break
            await page.evaluate("""
                document.querySelectorAll('[id^="login-full-panel-"]').forEach(el => el.remove());
                window.scrollTo(0, document.documentElement.scrollHeight);
            """)
            await page.wait_for_timeout(3500)
            console.print(f"[dim]Scroll {i+1}/{scroll_count} — {len(collected)} video[/dim]")
            if len(collected) == prev:
                no_new += 1
            else:
                no_new, prev = 0, len(collected)

        await browser.close()

    return list(collected.values())[:max_videos]


async def crawl_jingxuan(
    max_videos: int = 50,
    scroll_count: int = 15,
    headless: bool = True,
    cookies_file: str | None = None,
    start_url: str = JINGXUAN_URL,
) -> AsyncGenerator[dict, None]:
    for v in await _crawl(start_url, max_videos, scroll_count, headless, cookies_file):
        yield v


async def crawl_user_profile(
    user_url: str,
    max_videos: int = 100,
    headless: bool = True,
    cookies_file: str | None = None,
) -> AsyncGenerator[dict, None]:
    console.print(f"[cyan]Mở profile: {user_url}[/cyan]")
    for v in await _crawl(user_url, max_videos, 30, headless, cookies_file, stop_when_no_new=5):
        yield v


async def crawl_single_url(
    url: str,
    headless: bool = True,
    cookies_file: str | None = None,
) -> dict | None:
    parsed = urlparse(url)
    modal_id = parse_qs(parsed.query).get("modal_id", [None])[0]

    # Nếu URL dạng /video/{id} thì lấy id từ path
    if not modal_id and "/video/" in parsed.path:
        modal_id = parsed.path.split("/video/")[-1].split("?")[0].strip("/") or None

    collected: dict[str, dict] = {}

    async with async_playwright() as p:
        browser, context = await _make_context(p, headless, cookies_file)
        page = await context.new_page()

        async def handle_response(response: Response):
            if not _is_douyin_api(response.url):
                return
            if "json" not in response.headers.get("content-type", ""):
                return
            try:
                data = await response.json()
                detail = data.get("aweme_detail") or data.get("item_info", {})
                if detail and "aweme_id" in detail:
                    v = _parse_aweme(detail)
                    if v:
                        collected[v["aweme_id"]] = v
                        return
                for item in _find_aweme_list(data):
                    v = _parse_aweme(item)
                    if v:
                        if modal_id and v["aweme_id"] == modal_id:
                            collected[modal_id] = v
                        elif modal_id not in collected:
                            collected[v["aweme_id"]] = v
            except Exception:
                pass

        page.on("response", handle_response)
        console.print(f"[cyan]Mở {url}[/cyan]")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        await page.wait_for_timeout(5000)
        await browser.close()

    if modal_id and modal_id in collected:
        return collected[modal_id]
    return next(iter(collected.values()), None)
