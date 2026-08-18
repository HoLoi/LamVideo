"""
Vbee Text-to-Speech adapter.

Config (env):
  VBEE_TOKEN        Bearer token
  VBEE_APP_ID       App ID
  VBEE_VOICE_CODE   Default voice code

Usage:
  from backend.voices.vbee import run_tts
  await run_tts(text="Xin chào", output="out.mp3")
  await run_tts(text="...", mode="sync", speed=1.3, output="out.mp3")

CLI:
  python -m backend.voices.vbee "Xin chào"
  python -m backend.voices.vbee "..." --mode sync --output out.mp3
"""

import asyncio
import os
import sys
import time
from pathlib import Path

import httpx

# ── Config ─────────────────────────────────────────────────────────────────

DEFAULT_VOICE_CODE = "s_vinhlong_male_duchuy20260513084401609_news_vc"
OUTPUT_FORMAT      = "mp3"
BITRATE            = 128
DEFAULT_SPEED      = 1.25
BASE_URL           = "https://api.vbee.vn"
SYNC_MAX_CHARS     = 300
POLL_INTERVAL      = 2.0
POLL_TIMEOUT       = 120.0


def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _headers() -> dict:
    token  = _cfg("VBEE_TOKEN")
    app_id = _cfg("VBEE_APP_ID")
    if not token or not app_id:
        raise EnvironmentError("Missing VBEE_TOKEN / VBEE_APP_ID env vars.")
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "App-Id": app_id,
    }


# ── Sync (realtime) ────────────────────────────────────────────────────────

async def _sync_tts(text: str, output: str, voice_code: str, speed: float, log) -> dict:
    log(f"[vbee/sync] {len(text)} chars → {output} (voice={voice_code}, speed={speed})")

    payload = {
        "text": text,
        "mode": "sync",
        "voiceCode": voice_code,
        "outputFormat": OUTPUT_FORMAT,
        "bitrate": BITRATE,
        "speed": speed,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{BASE_URL}/v1/tts", json=payload, headers=_headers())

    if resp.status_code != 200:
        raise RuntimeError(f"Vbee sync [{resp.status_code}]: {resp.text[:500]}")

    ct = resp.headers.get("content-type", "")
    if "application/json" in ct:
        raise RuntimeError(f"Vbee sync trả JSON thay vì audio: {resp.text[:300]}")

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_bytes(resp.content)
    log(f"[vbee/sync] saved {len(resp.content):,} bytes → {output}")
    return {"output_file": output, "bytes": len(resp.content), "mode": "sync"}


# ── Async (batch + poll) ───────────────────────────────────────────────────

async def _async_tts(text: str, output: str, voice_code: str, speed: float, log) -> dict:
    log(f"[vbee/async] {len(text)} chars → {output} (voice={voice_code}, speed={speed})")

    payload = {
        "text": text,
        "mode": "async",
        "voiceCode": voice_code,
        "outputFormat": OUTPUT_FORMAT,
        "bitrate": BITRATE,
        "speed": speed,
        "webhookUrl": "https://example.com/vbee-webhook",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{BASE_URL}/v1/tts", json=payload, headers=_headers())

    data = resp.json()
    if resp.status_code >= 300 or "requestId" not in data:
        raise RuntimeError(f"Vbee async submit [{resp.status_code}]: {data}")

    request_id = data["requestId"]
    log(f"[vbee/async] requestId={request_id}")

    # Poll
    deadline = time.monotonic() + POLL_TIMEOUT
    audio_link = None
    async with httpx.AsyncClient(timeout=30) as client:
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            r = await client.get(f"{BASE_URL}/v1/tts/requests/{request_id}", headers=_headers())
            result = r.json()
            status = result.get("status", "?")
            log(f"[vbee/async] status={status}")

            if status == "COMPLETED" and result.get("audioLink"):
                audio_link = result["audioLink"]
                break
            if status == "FAILED" or r.status_code >= 400:
                raise RuntimeError(f"Vbee async failed: {result}")

    if not audio_link:
        raise TimeoutError("Vbee async timeout")

    # Download
    log("[vbee/async] downloading audio...")
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        dl = await client.get(audio_link)
    dl.raise_for_status()

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_bytes(dl.content)
    log(f"[vbee/async] saved {len(dl.content):,} bytes → {output}")
    return {"output_file": output, "bytes": len(dl.content), "mode": "async",
            "audio_link": audio_link, "request_id": request_id}


# ── Public API ─────────────────────────────────────────────────────────────

async def run_tts(
    text: str,
    *,
    mode: str = "async",
    output: str | None = None,
    voice_code: str | None = None,
    speed: float | None = None,
    log=None,
) -> dict:
    """
    Gọi Vbee TTS API.

    Args:
        text:       Nội dung cần đọc.
        mode:       "sync" (≤300 ký tự, trả audio ngay) | "async" (poll).
        output:     Đường dẫn file output.
        voice_code: Override voice (mặc định VBEE_VOICE_CODE env).
        speed:      0.5–2.0 (mặc định 1.25).
        log:        Callable(msg) để in log.

    Returns:
        { output_file, bytes, mode, ... }
    """
    if not text or not text.strip():
        raise ValueError("text is required")

    _log      = log or (lambda _: None)
    voice     = voice_code or _cfg("VBEE_VOICE_CODE") or DEFAULT_VOICE_CODE
    spd       = max(0.5, min(2.0, float(speed))) if speed is not None else DEFAULT_SPEED
    out_file  = output or f"output_{time.time_ns()}.{OUTPUT_FORMAT}"

    # Auto-chọn sync nếu text ngắn
    if mode == "async" and len(text.strip()) <= SYNC_MAX_CHARS:
        mode = "sync"

    if mode == "sync":
        return await _sync_tts(text.strip(), out_file, voice, spd, _log)
    else:
        return await _async_tts(text.strip(), out_file, voice, spd, _log)


async def tts_to_file(
    text: str,
    output: str,
    voice_code: str | None = None,
    speed: float | None = None,
) -> None:
    """Drop-in replacement cho edge_tts trong srt_to_mp4/batch_render."""
    await run_tts(text=text, output=output, voice_code=voice_code, speed=speed)


# ── CLI ────────────────────────────────────────────────────────────────────

def _parse_cli(argv):
    opts = {"text": [], "mode": "async", "output": None, "voice": None, "speed": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--mode" and i + 1 < len(argv):
            opts["mode"] = argv[i + 1]; i += 2
        elif a in ("--output", "-o") and i + 1 < len(argv):
            opts["output"] = argv[i + 1]; i += 2
        elif a in ("--voice", "-v") and i + 1 < len(argv):
            opts["voice"] = argv[i + 1]; i += 2
        elif a == "--speed" and i + 1 < len(argv):
            opts["speed"] = float(argv[i + 1]); i += 2
        elif not a.startswith("--"):
            opts["text"].append(a); i += 1
        else:
            i += 1
    opts["text"] = " ".join(opts["text"])
    return opts


async def _cli_main():
    # Đọc stdin nếu không có text
    opts = _parse_cli(sys.argv[1:])
    text = opts["text"].strip()
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read().strip()

    if not text:
        print('Usage: python -m backend.voices.vbee "text" [--mode sync|async] [-o out.mp3]',
              file=sys.stderr)
        sys.exit(1)

    try:
        result = await run_tts(
            text=text,
            mode=opts["mode"],
            output=opts["output"],
            voice_code=opts["voice"],
            speed=opts["speed"],
            log=print,
        )
        print(f"✅ {result['output_file']} ({result['bytes']:,} bytes, mode={result['mode']})")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(_cli_main())
