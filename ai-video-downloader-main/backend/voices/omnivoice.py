"""
OmniVoice (local self-hosted) Text-to-Speech adapter — voice cloning.

Model chạy local, clone giọng từ ref_audio + ref_text.
API: POST http://<host>/v1/tts/upload (multipart form-data)

Config (env hoặc truyền trực tiếp):
  OMNIVOICE_BASE_URL   (mặc định http://192.168.1.61:8002)
  OMNIVOICE_VOICE      (default voiceCode nếu không truyền)
  OMNIVOICE_R2_BASE_URL (mặc định https://video.mangox.dev/voice)

Library voices: backend/voices/lib/<category>/<slug>/sample.mp3 + voices.json

Usage:
  from backend.voices.omnivoice import run_tts, list_voices

  # Dùng voice trong thư viện
  await run_tts(text="Xin chào", voice_code="tuan-anh-news", output="out.mp3")

  # Dùng ref audio tuỳ biến
  await run_tts(text="...", ref_audio="mau.mp3", ref_text="Lời trong file mẫu", output="out.mp3")

  # Với speed để fit SRT timing
  await run_tts(text="...", voice_code="tuan-anh-news", speed=1.3, output="out.mp3")

CLI:
  python -m backend.voices.omnivoice "Xin chào" --voice tuan-anh-news -o out.mp3
  python -m backend.voices.omnivoice --list
"""

import csv
import io
import json
import os
import sys
from pathlib import Path

import httpx

# ── Paths ──────────────────────────────────────────────────────────────────

_HERE = Path(__file__).parent
LIB_ROOT = _HERE / "lib"
PROJECT_ROOT = _HERE.parent.parent
CSV_REGISTRY = PROJECT_ROOT / "voice" / "omnivoice.csv"

DEFAULT_BASE_URL = "http://192.168.1.61:8002"
DEFAULT_R2_BASE = "https://video.mangox.dev/voice"


def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# ── Registry / Library ─────────────────────────────────────────────────────

def _load_csv_registry() -> list[dict]:
    if not CSV_REGISTRY.exists():
        return []
    try:
        with open(CSV_REGISTRY, encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _load_library() -> list[dict]:
    p = LIB_ROOT / "voices.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("voices", [])
    except Exception:
        return []


def list_voices() -> list[dict]:
    """Trả về danh sách voice từ registry CSV hoặc thư viện local."""
    reg = _load_csv_registry()
    if reg:
        return [{"code": v["voice_id"], "voice_id": v["voice_id"], "name": v.get("name", "")} for v in reg]
    return [{"code": v["dir"], "voice_id": v.get("voiceId", ""), "name": v.get("name", "")} for v in _load_library()]


def resolve_voice(voice_code: str) -> dict:
    """
    Tìm voice theo voice_code.
    Trả về dict: { ref_audio_url?, ref_audio?, ref_text, voice }
    """
    code = voice_code.strip()
    if not code:
        raise ValueError("voice_code là bắt buộc.")

    # 1) Registry CSV (R2)
    reg = _load_csv_registry()
    row = next((v for v in reg if v["voice_id"] == code), None)
    if row:
        ref_text = row.get("transcript", "").strip()
        if not ref_text:
            raise ValueError(f'Voice "{code}" thiếu transcript trong omnivoice.csv.')
        base = _cfg("OMNIVOICE_R2_BASE_URL", DEFAULT_R2_BASE).rstrip("/")
        return {"ref_audio_url": f"{base}/{code}", "ref_text": ref_text, "voice": {"dir": code, "name": row.get("name", code)}}

    # 2) Thư viện local
    lib = _load_library()
    slug = lambda v: v["dir"].split("/")[-1]
    voice = (
        next((v for v in lib if v["dir"] == code), None)
        or next((v for v in lib if v.get("voiceId") == code), None)
        or next((v for v in lib if slug(v) == code), None)
    )
    if not voice:
        samples = ", ".join(v["voice_id"] for v in reg[:5]) if reg else "(registry trống)"
        raise ValueError(f'Không tìm thấy voice "{code}". Xem --list. Ví dụ: {samples}')

    ref_audio = LIB_ROOT / voice["dir"] / "sample.mp3"
    if not ref_audio.exists():
        raise FileNotFoundError(f'Voice "{voice["dir"]}" thiếu ref audio: {ref_audio}')
    ref_text = (voice.get("transcript") or "").strip()
    if not ref_text:
        raise ValueError(f'Voice "{voice["dir"]}" thiếu transcript.')
    return {"ref_audio": str(ref_audio), "ref_text": ref_text, "voice": voice}


# ── Core TTS ───────────────────────────────────────────────────────────────

async def run_tts(
    text: str,
    *,
    voice_code: str | None = None,
    ref_audio: str | None = None,       # đường dẫn file local (override)
    ref_audio_url: str | None = None,   # URL R2 (override)
    ref_text: str | None = None,        # transcript của ref (override)
    format: str = "mp3",
    speed: float | None = None,         # 0.5–2.0
    output: str | None = None,
    log=None,
) -> dict:
    """
    Gọi OmniVoice TTS API và lưu kết quả ra file.

    Args:
        text:         Nội dung cần đọc.
        voice_code:   Voice trong thư viện (dir / voiceId / slug).
        ref_audio:    Override: file local ref audio.
        ref_audio_url: Override: URL ref audio (tải về trước khi gửi).
        ref_text:     Override: transcript của ref audio.
        format:       mp3 | wav.
        speed:        Tốc độ đọc 0.5–2.0. Không gửi duration để speed có tác dụng.
        output:       Đường dẫn file output (mặc định output_<ts>.mp3).
        log:          Callable(msg) để in log.

    Returns:
        { output_file, bytes, voice }
    """
    if not text or not text.strip():
        raise ValueError("text is required")

    _log = log or (lambda _: None)
    base_url = _cfg("OMNIVOICE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")

    # Resolve ref audio + ref text
    resolved_audio_path: str | None = ref_audio
    resolved_audio_url: str | None = ref_audio_url
    resolved_text: str | None = ref_text
    voice_label = "custom-ref"

    if not (resolved_audio_path or resolved_audio_url) or not resolved_text:
        code = voice_code or _cfg("OMNIVOICE_VOICE")
        if not code:
            raise ValueError("Cần voice_code hoặc cặp ref_audio + ref_text. Xem list_voices().")
        r = resolve_voice(code)
        resolved_audio_url = resolved_audio_url or r.get("ref_audio_url")
        resolved_audio_path = resolved_audio_path or r.get("ref_audio")
        resolved_text = resolved_text or r["ref_text"]
        voice_label = r["voice"]["dir"]

    # Nạp buffer ref audio
    if resolved_audio_url:
        audio_name = resolved_audio_url.split("/")[-1]
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(resolved_audio_url)
        resp.raise_for_status()
        audio_buf = resp.content
    else:
        audio_path = Path(resolved_audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Ref audio không tồn tại: {audio_path}")
        audio_name = audio_path.name
        audio_buf = audio_path.read_bytes()

    # Speed: clamp [0.5, 2.0]
    speed_val: float | None = None
    if speed is not None:
        speed_val = max(0.5, min(2.0, float(speed)))

    output_file = output or f"output_{__import__('time').time_ns()}.{format}"
    mime = "audio/wav" if audio_name.lower().endswith(".wav") else "audio/mpeg"

    _log(
        f"[omnivoice] {len(text.strip())} chars → {output_file} "
        f"(voice={voice_label}, ref={audio_name}"
        f"{', @R2' if resolved_audio_url else ''}"
        f"{f', speed={speed_val}' if speed_val else ''}, server={base_url})"
    )

    # Gửi multipart
    # ⚠️ KHÔNG gửi `duration` — có duration thì server bỏ qua speed
    files = {"ref_audio": (audio_name, io.BytesIO(audio_buf), mime)}
    data = {
        "text": text.strip(),
        "ref_text": resolved_text,
        "format": format,
        "num_step": "32",
    }
    if speed_val is not None:
        data["speed"] = str(speed_val)

    url = f"{base_url}/v1/tts/upload"
    async with httpx.AsyncClient(timeout=600) as client:
        try:
            resp = await client.post(url, data=data, files=files)
        except httpx.ConnectError as e:
            raise ConnectionError(f"Không kết nối được OmniVoice ({url}): {e}") from e

    if not resp.is_success:
        raise RuntimeError(f"OmniVoice TTS [{resp.status_code}]: {resp.text[:500]}")

    out = Path(output_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(resp.content)
    _log(f"[omnivoice] saved {len(resp.content):,} bytes → {output_file}")

    return {"output_file": output_file, "bytes": len(resp.content), "voice": voice_label}


# ── Convenience wrapper cho srt_to_mp4 (thay edge-tts) ────────────────────

async def tts_to_file(
    text: str,
    output: str,
    voice_code: str | None = None,
    speed: float | None = None,
) -> None:
    """Drop-in replacement cho edge_tts.Communicate().save() trong srt_to_mp4."""
    await run_tts(text=text, voice_code=voice_code, speed=speed, output=output)


# ── CLI ────────────────────────────────────────────────────────────────────

def _parse_cli(argv: list[str]) -> dict:
    opts: dict = {"text": [], "voice": None, "output": None, "speed": None,
                  "ref_audio": None, "ref_text": None, "list": False}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--voice", "-v") and i + 1 < len(argv):
            opts["voice"] = argv[i + 1]; i += 2
        elif a in ("--output", "-o") and i + 1 < len(argv):
            opts["output"] = argv[i + 1]; i += 2
        elif a == "--speed" and i + 1 < len(argv):
            opts["speed"] = float(argv[i + 1]); i += 2
        elif a == "--ref-audio" and i + 1 < len(argv):
            opts["ref_audio"] = argv[i + 1]; i += 2
        elif a == "--ref-text" and i + 1 < len(argv):
            opts["ref_text"] = argv[i + 1]; i += 2
        elif a == "--list":
            opts["list"] = True; i += 1
        elif not a.startswith("--"):
            opts["text"].append(a); i += 1
        else:
            i += 1
    opts["text"] = " ".join(opts["text"])
    return opts


async def _cli_main():
    opts = _parse_cli(sys.argv[1:])

    if opts["list"]:
        voices = list_voices()
        print(f"OmniVoice library — {len(voices)} voices:")
        for v in voices:
            print(f"  {v['code']:<36} {v['name']}  [{v['voice_id']}]")
        return

    text = opts["text"].strip()
    if not text:
        print(
            'Usage: python -m backend.voices.omnivoice "text" [--voice <code>] [-o out.mp3]\n'
            '       python -m backend.voices.omnivoice "text" --ref-audio mau.mp3 --ref-text "..." -o out.mp3\n'
            "       python -m backend.voices.omnivoice --list",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        result = await run_tts(
            text=text,
            voice_code=opts["voice"],
            ref_audio=opts["ref_audio"],
            ref_text=opts["ref_text"],
            output=opts["output"],
            speed=opts["speed"],
            log=print,
        )
        print(f"✅ {result['output_file']} ({result['bytes']:,} bytes)")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    import asyncio
    asyncio.run(_cli_main())
