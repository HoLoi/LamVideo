import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="zh-CN",
        )
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        await context.add_init_script("window.addEventListener('DOMContentLoaded', () => localStorage.setItem('douyin_web_hide_guide', '1'))")

        intercepted = []

        async def handle_response(response):
            url = response.url
            if "douyin.com" in url and any(x in url for x in ["/aweme/", "/api/", "/web/"]):
                ct = response.headers.get("content-type", "")
                if "json" in ct:
                    try:
                        data = await response.json()
                        intercepted.append({"url": url, "keys": list(data.keys())[:10]})
                    except Exception:
                        pass

        page = await context.new_page()
        page.on("response", handle_response)

        await page.goto("https://www.douyin.com/?recommend=1&from_nav=1", wait_until="domcontentloaded", timeout=30000)
        await page.evaluate("localStorage.setItem('douyin_web_hide_guide', '1')")
        await page.wait_for_timeout(5000)

        html = await page.content()
        with open("/tmp/douyin_debug.html", "w") as f:
            f.write(html)

        print(f"HTML saved: {len(html)} chars")
        print(f"\nAPI calls intercepted: {len(intercepted)}")
        for item in intercepted:
            print(f"  {item['url'][:100]}")
            print(f"    keys: {item['keys']}")

        await browser.close()

asyncio.run(main())
