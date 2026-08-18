import asyncio

import click
from rich.console import Console
from rich.table import Table

from .crawler import crawl_jingxuan, crawl_user_profile, crawl_single_url
from .downloader import download_videos
from .transcribe import transcribe_video

console = Console()


def _print_video_table(videos: list[dict]) -> None:
    table = Table(title=f"Tìm thấy {len(videos)} video", show_lines=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Tác giả", style="cyan", max_width=20)
    table.add_column("Tiêu đề", max_width=50)
    table.add_column("ID", style="dim", max_width=20)
    for i, v in enumerate(videos, 1):
        table.add_row(str(i), v["author"], v["title"], v["aweme_id"])
    console.print(table)


@click.group()
def cli():
    """Douyin Video Downloader — tải video không watermark từ Douyin."""
    pass


@cli.command()
@click.option("--url", default="https://www.douyin.com/jingxuan", show_default=True)
@click.option("--max", "max_videos", default=50, show_default=True)
@click.option("--scroll", default=10, show_default=True)
@click.option("--out", "output_dir", default="downloads/jingxuan", show_default=True)
@click.option("--concurrency", default=4, show_default=True)
@click.option("--cookies", "cookies_file", default=None)
@click.option("--headless/--no-headless", default=True, show_default=True)
@click.option("--dry-run", is_flag=True, help="Chỉ liệt kê, không tải")
def jingxuan(url, max_videos, scroll, output_dir, concurrency, cookies_file, headless, dry_run):
    """Tải video từ trang Tinh Tuyển hoặc bất kỳ feed URL Douyin nào."""
    console.rule("[bold cyan]Douyin Jingxuan Downloader[/bold cyan]")

    async def run():
        videos = []
        console.print(f"[yellow]Đang crawl {url} (max={max_videos}, scroll={scroll})...[/yellow]")
        async for video in crawl_jingxuan(
            max_videos=max_videos, scroll_count=scroll,
            headless=headless, cookies_file=cookies_file, start_url=url,
        ):
            videos.append(video)
            if len(videos) >= max_videos:
                break

        if not videos:
            console.print("[red]Không tìm thấy video. Thử --no-headless.[/red]")
            return

        _print_video_table(videos)

        if dry_run:
            console.print("[yellow]Dry-run mode: không tải xuống.[/yellow]")
            return

        console.print(f"\n[green]Tải {len(videos)} video vào '{output_dir}'...[/green]")
        success, fail = await download_videos(videos, output_dir=output_dir, concurrency=concurrency)
        console.rule()
        console.print(f"[green]✓ Thành công: {success}[/green]  [red]✗ Thất bại: {fail}[/red]")

    asyncio.run(run())


@cli.command()
@click.argument("url")
@click.option("--max", "max_videos", default=100, show_default=True)
@click.option("--out", "output_dir", default="downloads/user", show_default=True)
@click.option("--concurrency", default=4, show_default=True)
@click.option("--cookies", "cookies_file", default=None)
@click.option("--headless/--no-headless", default=True, show_default=True)
@click.option("--dry-run", is_flag=True)
def user(url, max_videos, output_dir, concurrency, cookies_file, headless, dry_run):
    """Tải toàn bộ video từ profile người dùng."""
    console.rule("[bold cyan]Douyin User Downloader[/bold cyan]")

    async def run():
        videos = []
        console.print(f"[yellow]Đang crawl profile: {url}[/yellow]")
        async for video in crawl_user_profile(
            user_url=url, max_videos=max_videos,
            headless=headless, cookies_file=cookies_file,
        ):
            videos.append(video)
            if len(videos) >= max_videos:
                break

        if not videos:
            console.print("[red]Không tìm thấy video. Thử --no-headless hoặc --cookies.[/red]")
            return

        _print_video_table(videos)
        if dry_run:
            console.print("[yellow]Dry-run mode.[/yellow]")
            return

        console.print(f"\n[green]Tải {len(videos)} video vào '{output_dir}'...[/green]")
        success, fail = await download_videos(videos, output_dir=output_dir, concurrency=concurrency)
        console.rule()
        console.print(f"[green]✓ Thành công: {success}[/green]  [red]✗ Thất bại: {fail}[/red]")

    asyncio.run(run())


@cli.command()
@click.argument("url")
@click.option("--out", "output_dir", default="downloads/jingxuan", show_default=True)
@click.option("--cookies", "cookies_file", default=None)
@click.option("--headless/--no-headless", default=True, show_default=True)
@click.option("--dry-run", is_flag=True)
def dl(url, output_dir, cookies_file, headless, dry_run):
    """Tải 1 video từ link detail Douyin."""
    console.rule("[bold cyan]Douyin Single Download[/bold cyan]")

    async def run():
        console.print(f"[yellow]Crawl video từ: {url}[/yellow]")
        video = await crawl_single_url(url=url, headless=headless, cookies_file=cookies_file)

        if not video:
            console.print("[red]Không lấy được thông tin video. Thử --no-headless.[/red]")
            return

        console.print(f"[green]Tìm thấy:[/green] [{video['author']}] {video['title'][:60]}")

        if dry_run:
            console.print(f"[dim]URL: {video['download_url'][:80]}...[/dim]")
            return

        success, _ = await download_videos([video], output_dir=output_dir)
        if success:
            console.print(f"[green]✓ Đã tải xong vào '{output_dir}'[/green]")
        else:
            console.print("[red]✗ Tải thất bại.[/red]")

    asyncio.run(run())


@cli.command()
@click.argument("video_path")
@click.option(
    "--model", default="base", show_default=True,
    type=click.Choice(["tiny", "base", "small", "medium", "large", "large-v3-turbo"], case_sensitive=False),
)
@click.option("--language", default=None, help="zh, en, vi... để trống để auto-detect")
@click.option("--id", "aweme_id", default=None)
def transcribe(video_path, model, language, aweme_id):
    """Transcribe video bằng Whisper và cập nhật index.csv."""
    console.rule("[bold cyan]Douyin Transcribe[/bold cyan]")

    async def run():
        try:
            await transcribe_video(video_path=video_path, model=model, language=language, aweme_id=aweme_id)
        except (FileNotFoundError, RuntimeError) as e:
            console.print(f"[red]{e}[/red]")
        except ImportError:
            console.print("[red]Chưa cài faster-whisper.[/red]\nChạy: [yellow]pip install faster-whisper[/yellow]")

    asyncio.run(run())
