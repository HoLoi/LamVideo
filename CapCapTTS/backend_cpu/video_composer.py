"""
Video composer (MVP-7) — Export Composer: chon canvas, fit, burn subtitle moi,
che phu de cu, them logo roi xuat video hoan chinh.

Input:
  - video goc (data/videos/<video_id>/index.mp4) hoac dub output (output.mp4)
  - subtitles: transcript_vi.json (uu tien) hoac subtitles_vi.srt cua dub
  - logo: chi doc tu data/videos/<video_id>/assets/ (KHONG doc path tuy y)

Output:
  data/videos/<video_id>/exports/<export_id>/
    output_final.mp4
    metadata.json
    composer_plan.json
    subtitles_rendered.ass   (neu burn subtitle)
    logo copy                (neu co logo)

FFmpeg safety: khong shell=True, list args, cwd = export dir (ass/logo dung ten tuong doi
nen khong gap loi escape path Windows), khong bao gio ghi de video goc / dub goc.
"""
import asyncio
import json
import re
import shutil
import subprocess
import traceback
import uuid

from datetime import datetime
from pathlib import Path

from video_jobs import job_manager, format_job_error
from video_dl.transcribe import resolve_ffmpeg
from video_dubber import sanitize_output_name, is_valid_dub_id, dub_output_dir, list_dub_outputs
import video_library as lib

EXPORT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ASPECT_PRESETS = {
    "original": None,
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}
FIT_MODES = ("contain", "cover", "blurred_background", "stretch")
SUB_POSITIONS = ("bottom", "middle", "top", "custom")
LOGO_POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right", "center", "custom")
MASK_MODES = ("blur", "box")
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
MAX_CANVAS_WH = 4096


def exports_root(video_id: str) -> Path:
    return lib.video_dir(video_id) / "exports"


def is_valid_export_id(export_id: str) -> bool:
    return bool(export_id) and bool(EXPORT_ID_RE.match(export_id))


def export_dir(video_id: str, export_id: str) -> Path:
    if not is_valid_export_id(export_id):
        raise ValueError(f"export_id khong hop le: {export_id!r}")
    root = exports_root(video_id).resolve()
    d = (root / export_id).resolve()
    if not str(d).startswith(str(root)):
        raise ValueError("Path nam ngoai exports")
    return d


def assets_dir(video_id: str) -> Path:
    d = lib.video_dir(video_id) / "assets"
    d.mkdir(parents=True, exist_ok=True)
    return d


LOGO_EXTS = {"png", "jpg", "jpeg", "webp"}


def normalize_asset_name(name: str) -> str | None:
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", (name or "").strip())
    if not name or name in (".", ".."):
        return None
    if Path(name).suffix.lower().lstrip(".") not in LOGO_EXTS:
        return None
    return name[:64]


def list_assets(video_id: str) -> list[dict]:
    d = lib.video_dir(video_id) / "assets"
    items = []
    if not d.exists():
        return items
    for f in sorted(d.iterdir()):
        if f.is_file() and f.suffix.lower().lstrip(".") in LOGO_EXTS:
            items.append({"name": f.name, "size_bytes": f.stat().st_size})
    return items


def save_logo_asset(video_id: str, filename: str, data: bytes) -> str:
    """Lu file logo vao assets dir (guard: only sanitized name inside assets)."""
    name = normalize_asset_name(filename)
    if not name:
        raise ValueError("Ten file logo khong hop le (cho phep png/jpg/jpeg/webp, <=64 ky tu).")
    if not data:
        raise ValueError("File logo rong.")
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("File logo qua lon (>8MB).")
    target = (assets_dir(video_id) / name).resolve()
    root = lib.video_dir(video_id).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError("Path nam ngoai video dir")
    target.write_bytes(data)
    return name


def _get_asset_path(video_id: str, asset_name: str) -> Path | None:
    """Chi doc logo tu assets dir. Tra ve None neu khong hop le / khong ton tai."""
    a = lib.video_dir(video_id) / "assets"
    if not (asset_name or "").strip() or "/" in asset_name or "\\" in asset_name:
        return None
    p = (a / asset_name).resolve()
    if not str(p).startswith(str(a.resolve())):
        return None
    if not p.is_file():
        return None
    return p


# ─── Validate options ───

def _pct(v, name: str, default: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        f = default
    if not (0.0 <= f <= 100.0):
        raise ValueError(f"{name} phai nam trong 0..100.")
    return f


def _f_ge0(v, name: str, default: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        f = default
    if f < 0:
        raise ValueError(f"{name} phai >= 0.")
    return f


def validate_compose_options(opts: dict) -> tuple[dict, str | None]:
    """Tra ve (opts sach, error|None). Error khong rong -> khong chap nhan."""
    try:
        source = (opts.get("source") or "original").strip().lower()
        if source not in ("original", "latest_dub", "dub_output"):
            raise ValueError("source phai la 'original' | 'latest_dub' | 'dub_output'.")
        dub_id = (opts.get("dub_id") or "").strip() or None
        if dub_id and not is_valid_dub_id(dub_id):
            raise ValueError("dub_id khong hop le.")
        if source == "dub_output" and not dub_id:
            raise ValueError("source=dub_output can dub_id.")
        if source != "dub_output":
            dub_id = None

        aspect_ratio = (opts.get("aspect_ratio") or "original").strip().lower()
        if aspect_ratio not in ASPECT_PRESETS:
            raise ValueError("aspect_ratio phai la 'original' | '9:16' | '16:9' | '1:1' | '4:5' | 'custom'.")
        width = int(opts.get("width") or 0)
        height = int(opts.get("height") or 0)
        if aspect_ratio == "custom" and not (0 < width <= MAX_CANVAS_WH and 0 < height <= MAX_CANVAS_WH):
            raise ValueError(f"custom can width/height trong 1..{MAX_CANVAS_WH}.")

        fit_mode = (opts.get("fit_mode") or "contain").strip().lower()
        if fit_mode not in FIT_MODES:
            raise ValueError("fit_mode phai la 'contain' | 'cover' | 'blurred_background' | 'stretch'.")

        background_color = (opts.get("background_color") or "#000000").strip().upper()
        if not HEX_COLOR_RE.match(background_color):
            raise ValueError("background_color phai dang #RRGGBB.")

        subtitle_enabled = bool(opts.get("subtitle_enabled"))
        subtitle_position = (opts.get("subtitle_position") or "bottom").strip().lower()
        if subtitle_position not in SUB_POSITIONS:
            raise ValueError("subtitle_position phai la 'bottom' | 'middle' | 'top' | 'custom'.")
        subtitle_y = _pct(opts.get("subtitle_y"), "subtitle_y", 88.0)
        subtitle_font_size = int(opts.get("subtitle_font_size") or 0)
        if not (8 <= subtitle_font_size <= 200):
            raise ValueError("subtitle_font_size phai nam trong 8..200.")
        subtitle_color = (opts.get("subtitle_color") or "#FFFFFF").strip().upper()
        subtitle_outline_color = (opts.get("subtitle_outline_color") or "#000000").strip().upper()
        if not HEX_COLOR_RE.match(subtitle_color) or not HEX_COLOR_RE.match(subtitle_outline_color):
            raise ValueError("subtitle colors phai dang #RRGGBB.")
        subtitle_box = bool(opts.get("subtitle_box"))
        subtitle_box_color = (opts.get("subtitle_box_color") or "#000000").strip().upper()
        if not HEX_COLOR_RE.match(subtitle_box_color):
            raise ValueError("subtitle_box_color phai dang #RRGGBB.")
        subtitle_box_opacity = float(opts.get("subtitle_box_opacity") or 0.75)
        if not (0.0 <= subtitle_box_opacity <= 1.0):
            raise ValueError("subtitle_box_opacity phai nam trong 0..1.")
        subtitle_margin_bottom = int(opts.get("subtitle_margin_bottom") or 60)
        if not (0 <= subtitle_margin_bottom <= 500):
            raise ValueError("subtitle_margin_bottom phai nam trong 0..500.")
        subtitle_max_chars = int(opts.get("subtitle_max_chars") or 22)
        if not (8 <= subtitle_max_chars <= 200):
            raise ValueError("subtitle_max_chars phai nam trong 8..200.")

        mask_enabled = bool(opts.get("old_subtitle_mask_enabled"))
        raw_mask = opts.get("old_subtitle_mask") or {}
        mask = {
            "x": _pct(raw_mask.get("x", 5.0), "mask.x", 5.0),
            "y": _pct(raw_mask.get("y", 78.0), "mask.y", 78.0),
            "w": _pct(raw_mask.get("w", 90.0), "mask.w", 90.0),
            "h": _pct(raw_mask.get("h", 14.0), "mask.h", 14.0),
            "mode": (raw_mask.get("mode") or "blur").strip().lower(),
            "blur": int(raw_mask.get("blur") or 18),
            "box_color": (raw_mask.get("box_color") or "#000000").strip().upper(),
            "box_opacity": float(raw_mask.get("box_opacity") or 0.85),
        }
        if mask["mode"] not in MASK_MODES:
            raise ValueError("old_subtitle_mask.mode phai la 'blur' | 'box'.")
        if not (0 < mask["blur"] <= 100):
            raise ValueError("old_subtitle_mask.blur phai nam trong 1..100.")
        if not HEX_COLOR_RE.match(mask["box_color"]):
            raise ValueError("old_subtitle_mask.box_color phai dang #RRGGBB.")
        if not (0.0 <= mask["box_opacity"] <= 1.0):
            raise ValueError("old_subtitle_mask.box_opacity phai nam trong 0..1.")
        if mask_enabled and (mask["x"] + mask["w"] > 100.01 or mask["y"] + mask["h"] > 100.01):
            raise ValueError("Region che phai nam trong 100% (x+w<=100, y+h<=100).")

        logo_enabled = bool(opts.get("logo_enabled"))
        logo_asset = (opts.get("logo_asset_id") or "").strip() or None
        logo_position = (opts.get("logo_position") or "top-right").strip().lower()
        if logo_position not in LOGO_POSITIONS:
            raise ValueError("logo_position khong hop le.")
        logo_scale = _pct(opts.get("logo_scale"), "logo_scale", 12.0)  # % cua canvas rong
        logo_opacity = float(opts.get("logo_opacity") if opts.get("logo_opacity") is not None else 0.9)
        if not (0.0 <= logo_opacity <= 1.0):
            raise ValueError("logo_opacity phai nam trong 0..1.")
        logo_margin = int(opts.get("logo_margin") if opts.get("logo_margin") is not None else 20)
        if not (0 <= logo_margin <= 500):
            raise ValueError("logo_margin phai nam trong 0..500.")
        logo_x = _pct(opts.get("logo_x"), "logo_x", 50.0)
        logo_y = _pct(opts.get("logo_y"), "logo_y", 50.0)
        if logo_enabled and not logo_asset:
            raise ValueError("logo_enabled can logo_asset_id.")
        if not logo_enabled:
            logo_asset = None

        trim_enabled = bool(opts.get("trim_enabled"))
        trim_start = _f_ge0(opts.get("trim_start"), "trim_start", 0.0)
        trim_end = _f_ge0(opts.get("trim_end"), "trim_end", 0.0)
        if trim_enabled and trim_end <= trim_start:
            raise ValueError("trim_end phai lon hon trim_start (va ca hai >= 0).")
        if not trim_enabled:
            trim_start, trim_end = 0.0, 0.0

        output_name = sanitize_output_name(opts.get("output_name") or "")

        return {
            "source": source,
            "dub_id": dub_id,
            "aspect_ratio": aspect_ratio,
            "width": width,
            "height": height,
            "fit_mode": fit_mode,
            "background_color": background_color,
            "subtitle_enabled": subtitle_enabled,
            "subtitle_position": subtitle_position,
            "subtitle_y": subtitle_y,
            "subtitle_font_size": subtitle_font_size,
            "subtitle_color": subtitle_color,
            "subtitle_outline_color": subtitle_outline_color,
            "subtitle_box": subtitle_box,
            "subtitle_box_color": subtitle_box_color,
            "subtitle_box_opacity": subtitle_box_opacity,
            "subtitle_margin_bottom": subtitle_margin_bottom,
            "subtitle_max_chars": subtitle_max_chars,
            "old_subtitle_mask_enabled": mask_enabled,
            "old_subtitle_mask": mask,
            "logo_enabled": logo_enabled,
            "logo_asset_id": logo_asset,
            "logo_position": logo_position,
            "logo_scale": logo_scale,
            "logo_opacity": logo_opacity,
            "logo_margin": logo_margin,
            "logo_x": logo_x,
            "logo_y": logo_y,
            "trim_enabled": trim_enabled,
            "trim_start": trim_start,
            "trim_end": trim_end,
            "output_name": output_name,
        }, None
    except ValueError as e:
        return {}, str(e)


# ─── FFprobe / helpers ───

def _run_proc(args: list, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(cwd) if cwd else None)


def _ffprobe_bin(ffmpeg: str) -> str:
    p = Path(ffmpeg)
    cand = p.with_name("ffprobe" + p.suffix)
    if cand.is_file():
        return str(cand)
    return ""


def probe_video(ffmpeg: str, path: Path) -> dict:
    """Tra ve {width, height, duration_sec, has_audio}. Fallback: parse `ffmpeg -i`."""
    ffprobe = _ffprobe_bin(ffmpeg)
    if ffprobe:
        proc = _run_proc([ffprobe, "-v", "error",
                          "-select_streams", "v:0",
                          "-show_entries", "stream=width,height",
                          "-show_entries", "format=duration",
                          "-of", "json", str(path)])
        try:
            data = json.loads(proc.stdout or "{}")
            streams = data.get("streams") or []
            fmt = data.get("format") or {}
            if streams:
                return {
                    "width": int(streams[0].get("width") or 0),
                    "height": int(streams[0].get("height") or 0),
                    "duration_sec": float(fmt.get("duration") or 0.0),
                    "has_audio": _has_audio_stream(ffmpeg, path),
                }
        except Exception:
            pass
    proc = _run_proc([ffmpeg, "-hide_banner", "-i", str(path)])
    err = proc.stderr or ""
    w = h = 0
    m = re.search(r"(\d{2,5})x(\d{2,5})", err)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
    dur = 0.0
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return {"width": w, "height": h, "duration_sec": dur,
            "has_audio": bool(re.search(r"Stream\s+#0:\d+.*Audio:", err))}


def _has_audio_stream(ffmpeg: str, path: Path) -> bool:
    return probe_video(ffmpeg, path).get("has_audio", False)


def _run_ffmpeg(ffmpeg: str, args: list, cwd: Path) -> str:
    proc = _run_proc([ffmpeg, "-y", *args], cwd)
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-3000:]
        raise RuntimeError(f"ffmpeg loi (exit {proc.returncode}):\n{tail}")
    return proc.stderr or ""


# ─── Subtitles ───

def _ass_color(hex_color: str, alpha: float = 0.0) -> str:
    """#RRGGBB + alpha (0=opaque, 1=transparent) -> ASS &HAABBGGRR."""
    hexv = hex_color.lstrip("#")
    bgr = hexv[4:6] + hexv[2:4] + hexv[0:2]
    a = max(0, min(255, int(round(alpha * 255))))
    return f"&H{a:02X}{bgr.upper()}"


def _concat_viable_lines(text: str, max_chars: int) -> str:
    out = []
    for raw in re.split(r"[\r\n]+", text):
        while len(raw) > max_chars:
            cut = max_chars
            sp = raw.rfind(" ", 0, cut)
            if sp > cut * 0.5:
                cut = sp
            out.append(raw[:cut].strip())
            raw = raw[cut:].strip()
        if raw:
            out.append(raw)
    return "\\N".join(out)


def parse_srt(content: str) -> list[dict]:
    segs = []
    blocks = re.split(r"\n\s*\n", content.strip())
    ts_re = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")

    def _sec(h, m, s, ms):
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

    for blk in blocks:
        lines = [ln.strip() for ln in blk.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        m = ts_re.search(lines[1])
        if not m:
            continue
        start = _sec(*m.groups()[:4])
        end = _sec(*m.groups()[4:])
        text = " ".join(lines[2:])
        if text:
            segs.append({"start": round(start, 3), "end": round(end, 3), "text": text})
    return segs


def load_subtitle_segments(video_id: str, source_dub_id: str | None = None) -> list[dict]:
    """Uu tien transcript_vi.json; neu khong co, dung subtitles_vi.srt (video latest hoac dub)."""
    tvi = lib.get_transcript_vi(video_id)
    segs = (tvi or {}).get("segments") if tvi else None
    if segs:
        return list(segs)
    srt_path = None
    if source_dub_id:
        cand = dub_output_dir(video_id, source_dub_id) / "subtitles_vi.srt"
        if cand.exists():
            srt_path = cand
    if srt_path is None:
        cand = lib.video_dir(video_id) / "dub" / "subtitles_vi.srt"
        if cand.exists():
            srt_path = cand
    if srt_path is None:
        return []
    return parse_srt(srt_path.read_text(encoding="utf-8"))


def build_ass(segments: list[dict], opts: dict, out_w: int, out_h: int) -> str:
    fs = opts["subtitle_font_size"]
    max_chars = opts["subtitle_max_chars"]
    pos = opts["subtitle_position"]
    box = opts["subtitle_box"]

    if box:
        border_style = 3
        outline = 0
        back_colour = _ass_color(opts["subtitle_box_color"], 1.0 - opts["subtitle_box_opacity"])
        outline_colour = _ass_color(opts["subtitle_outline_color"])
        primary = _ass_color(opts["subtitle_color"])
    else:
        border_style = 1
        outline = 2
        outline_colour = _ass_color(opts["subtitle_outline_color"])
        back_colour = _ass_color("#000000")
        primary = _ass_color(opts["subtitle_color"])

    align_map = {"bottom": 2, "middle": 5, "top": 8}
    align = align_map.get(pos, 2)
    margin_bottom = opts["subtitle_margin_bottom"]

    def _margin_v(seg_text: str) -> str:
        if pos == "top":
            return str(max(0, 40))
        if pos == "middle":
            return "0"
        if pos == "custom":
            y = opts["subtitle_y"]
            m = int(out_h * (y / 100.0 - 0.5))
            return str(m)
        return str(margin_bottom)

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {out_w}\n"
        f"PlayResY: {out_h}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,{fs},{primary},{primary},{outline_colour},{back_colour},"
        "0,0,0,0,100,100,0,0,{border_style},{outline},1,{align},40,40,{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    ).format(
        fs=fs, primary=primary, outline_colour=outline_colour, back_colour=back_colour,
        border_style=border_style, outline=outline, align=align, margin_v=_margin_v(""),
    )

    events = []
    trim_enabled = bool(opts.get("trim_enabled"))
    trim_start = max(0.0, float(opts.get("trim_start") or 0.0))
    trim_end = float(opts.get("trim_end") or 0.0)
    for i, seg in enumerate(segments):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        text = text.replace("{", " ").replace("}", " ")
        text = _concat_viable_lines(text, max_chars)
        start = max(0.0, float(seg.get("start") or 0))
        end = max(start + 0.05, float(seg.get("end") or (start + 1.0)))

        if trim_enabled:
            if end <= trim_start or start >= trim_end:
                continue
            start = max(start, trim_start) - trim_start
            end = min(end, trim_end) - trim_start

        def _ts(sec: float) -> str:
            sec = max(0.0, sec)
            ms = int(round(sec * 100))
            h, rem = divmod(ms, 360000)
            m, rem = divmod(rem, 6000)
            s, cs = divmod(rem, 100)
            return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

        events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{text}")
    return header + "\n".join(events)


# ─── Filter graph ───

def _even(n: int) -> int:
    return max(2, int(n) & ~1)


def _clamp_region(mask: dict, out_w: int, out_h: int) -> dict:
    x = _even(out_w * mask["x"] / 100.0)
    y = _even(out_h * mask["y"] / 100.0)
    w = _even(out_w * mask["w"] / 100.0)
    h = _even(out_h * mask["h"] / 100.0)
    x = min(x, out_w - 2)
    y = min(y, out_h - 2)
    w = min(w, out_w - x)
    h = min(h, out_h - y)
    return {"x": x, "y": y, "w": max(2, w), "h": max(2, h)}


def build_fit_chain(opts: dict, out_w: int, out_h: int) -> str:
    fit = opts["fit_mode"]
    if fit == "cover":
        return (f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase"
                f":force_divisible_by=2,crop={out_w}:{out_h}")
    if fit == "stretch":
        return f"scale={out_w}:{out_h}"
    if fit == "blurred_background":
        return ("split=2[bg][fg];"
                f"[bg]scale={out_w}:{out_h}:force_original_aspect_ratio=increase"
                f":force_divisible_by=2,crop={out_w}:{out_h},gblur=sigma=24[bg_blur];"
                f"[fg]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease"
                f":force_divisible_by=2[fg_fit];"
                f"[bg_blur][fg_fit]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2")
    # contain mặc định
    return (f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease"
            f":force_divisible_by=2,pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2"
            f":color={opts['background_color']}")


def build_mask_chain(prev_label: str, opts: dict, out_w: int, out_h: int) -> tuple[str, str]:
    mask = opts["old_subtitle_mask"]
    r = _clamp_region(mask, out_w, out_h)
    label = "vmask"
    if mask["mode"] == "blur":
        chain = (f"[{prev_label}]split=2[m_keep][m_roi];"
                 f"[m_roi]crop={r['w']}:{r['h']}:{r['x']}:{r['y']},"
                 f"boxblur=luma_radius={mask['blur']}:luma_power=1[m_blur];"
                 f"[m_keep][m_blur]overlay={r['x']}:{r['y']}[{label}]")
    else:
        alpha = max(0.0, min(1.0, mask["box_opacity"]))
        chain = (f"[{prev_label}]drawbox=x={r['x']}:y={r['y']}:w={r['w']}:h={r['h']}"
                 f":color={mask['box_color']}@{alpha:.2f}:t=fill[{label}]")
    return chain, label


def build_logo_chain(prev_label: str, logo_idx: int, opts: dict, out_w: int, out_h: int) -> tuple[str, str]:
    scale = opts["logo_scale"]  # % cua out_w
    label = "vlogo"
    chain = (f"[{logo_idx}:v]scale={out_w}*{scale}/100:-1:force_divisible_by=2,"
             f"format=rgba,colorchannelmixer=aa={opts['logo_opacity']:.2f}[logo];")
    pos = opts["logo_position"]
    margin = opts["logo_margin"]
    if pos == "top-left":
        x, y = f"{margin}", f"{margin}"
    elif pos == "top-right":
        x, y = f"main_w-overlay_w-{margin}", f"{margin}"
    elif pos == "bottom-left":
        x, y = f"{margin}", f"main_h-overlay_h-{margin}"
    elif pos == "bottom-right":
        x, y = f"main_w-overlay_w-{margin}", f"main_h-overlay_h-{margin}"
    elif pos == "center":
        x, y = "(main_w-overlay_w)/2", "(main_h-overlay_h)/2"
    else:  # custom
        x, y = f"main_w*{opts['logo_x']}/100", f"main_h*{opts['logo_y']}/100"
    chain += f"[{prev_label}][logo]overlay={x}:{y}[{label}]"
    return chain, label


# ─── Job ───

def _resolve_source(video_id: str, opts: dict) -> tuple[Path, str | None]:
    vdir = lib.video_dir(video_id)
    src = opts["source"]
    dub_id = opts["dub_id"]
    if src == "original":
        p = vdir / "index.mp4"
        if not p.exists():
            raise RuntimeError("Video goc khong ton tai (index.mp4 da bi xoa?).")
        return p, None
    if src == "latest_dub":
        dubs = list_dub_outputs(video_id)
        if not dubs:
            raise RuntimeError("Chua co dub output nao — hay tao dub truoc hoac chon source=original.")
        dub_id = dubs[0]["dub_id"]
        p = dub_output_dir(video_id, dub_id) / "output.mp4"
        if not p.exists():
            raise RuntimeError(f"Output cua dub '{dub_id}' khong ton tai.")
        return p, dub_id
    p = dub_output_dir(video_id, dub_id) / "output.mp4"
    if not p.exists():
        raise RuntimeError(f"Khong tim thay dub output '{dub_id}' — output.mp4 khong ton tai.")
    return p, dub_id


def _resolve_audio(ffmpeg: str, video_id: str, source_path: Path) -> tuple[dict, dict | None]:
    """Tra ve (inputs_args, audio_map|null). Neu nguon khong co audio, dung final_dub.wav."""
    has_audio = _has_audio_stream(ffmpeg, source_path)
    if has_audio:
        return {}, {"map": "0:a:0"}
    wav = lib.video_dir(video_id) / "dub" / "final_dub.wav"
    if wav.exists():
        return {"-i": str(wav)}, {"map": "1:a:0"}
    raise RuntimeError("Nguon khong co audio va khong co final_dub.wav de dung thay — hay tao dub truoc.")


async def run_compose_job(job_id: str, video_id: str, opts: dict):
    video = await lib.get_video(video_id)
    if not video:
        await job_manager.finish_fail(job_id, f"Khong tim thay video {video_id} trong library.")
        return

    clean, err = validate_compose_options(opts)
    if err:
        await job_manager.finish_fail(job_id, f"Options khong hop le: {err}")
        return

    try:
        ffmpeg_path = resolve_ffmpeg()
    except RuntimeError as e:
        await job_manager.finish_fail(job_id, f"Khong tim thay ffmpeg: {e}")
        return

    try:
        await job_manager.update(job_id, status="running", progress=5,
                                 message="Dang xac dinh nguon video...")
        source_path, source_dub_id = _resolve_source(video_id, clean)
        probe = await asyncio.to_thread(probe_video, ffmpeg_path, source_path)
        if not probe["width"] or not probe["height"]:
            raise RuntimeError("Khong probe duoc kich thuoc video nguon.")
        if clean["trim_enabled"] and probe["duration_sec"] > 0:
            clean["trim_start"] = min(clean["trim_start"], max(0.0, probe["duration_sec"] - 0.001))
            clean["trim_end"] = min(clean["trim_end"] or probe["duration_sec"], probe["duration_sec"])
            if clean["trim_end"] <= clean["trim_start"]:
                raise RuntimeError(
                    "trim_end phai lon hon trim_start (sau khi clamp theo thoi luong video goc).")
            if clean["trim_end"] - clean["trim_start"] < 0.05:
                raise RuntimeError(
                    "Vung trim qua ngan sau khi clamp theo thoi luong video goc — kiem tra trim_start/trim_end.")
        _log = lambda m: print(f"[video_job:{job_id}] {m}")
        _log(f"source={clean['source']} dub={source_dub_id} probe={probe['width']}x{probe['height']}")

        # Canvas
        if clean["aspect_ratio"] == "custom":
            out_w, out_h = _even(clean["width"]), _even(clean["height"])
        elif clean["aspect_ratio"] == "original":
            out_w, out_h = _even(probe["width"]), _even(probe["height"])
        else:
            preset_w, preset_h = ASPECT_PRESETS[clean["aspect_ratio"]]
            out_w, out_h = _even(preset_w), _even(preset_h)
        _log(f"canvas: {out_w}x{out_h} fit={clean['fit_mode']}")

        # Export dir
        name = clean["output_name"]
        base_id = name or f"exp_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
        export_id = base_id
        suffix = 2
        while (exports_root(video_id) / export_id).exists():
            export_id = f"{base_id}_{suffix}"
            suffix += 1
        out_dir = exports_root(video_id) / export_id
        out_dir.mkdir(parents=True, exist_ok=True)

        # Subtitles
        ass_path = None
        subtitle_source = None
        if clean["subtitle_enabled"]:
            await job_manager.update(job_id, progress=20, message="Dang tao subtitle (ASS)...")
            segments = load_subtitle_segments(video_id, source_dub_id)
            if not segments:
                raise RuntimeError(
                    "Video chua co transcript tieng Viet / subtitle — hay bam 'Translate to Vietnamese' "
                    "truoc, hoac bo tick 'Burn subtitle'.")
            ass_text = build_ass(segments, clean, out_w, out_h)
            ass_path = out_dir / "subtitles_rendered.ass"
            ass_path.write_text(ass_text, encoding="utf-8")
            subtitle_source = "transcript_vi.json" if lib.get_transcript_vi(video_id) else "subtitles_vi.srt"
            _log(f"ASS: {len(segments)} segments -> {ass_path.name}")

        # Logo
        logo_path = None
        if clean["logo_enabled"]:
            logo_path = _get_asset_path(video_id, clean["logo_asset_id"])
            if logo_path is None:
                raise RuntimeError(f"Logo asset '{clean['logo_asset_id']}' khong ton tai trong assets.")
            await asyncio.to_thread(shutil.copy2, str(logo_path), str(out_dir / logo_path.name))
            _log(f"logo: {logo_path.name}")

        # Filter graph
        await job_manager.update(job_id, progress=40, message="Dang build filter graph...")
        inputs = [("-i", str(source_path))]
        if logo_path:
            inputs.append(("-i", str(logo_path)))
        extra_inputs, audio_map = await asyncio.to_thread(_resolve_audio, ffmpeg_path, video_id, source_path)
        for k, v in extra_inputs.items():
            inputs.append((k, v))

        fc_parts = []
        fc_parts.append(f"[0:v]{build_fit_chain(clean, out_w, out_h)}[vfit]")
        prev = "vfit"
        if clean["old_subtitle_mask_enabled"]:
            chain, prev = build_mask_chain(prev, clean, out_w, out_h)
            fc_parts.append(chain)
        if logo_path:
            logo_idx = 1
            chain, prev = build_logo_chain(prev, logo_idx, clean, out_w, out_h)
            fc_parts.append(chain)
        if ass_path is not None:
            fc_parts.append(f"[{prev}]ass=filename=subtitles_rendered.ass[vout]")
            prev = "vout"
        fc = ";".join(fc_parts)
        _log(f"filter_complex: {fc}")

        # Encode
        await job_manager.update(job_id, progress=55, message="Dang xuat video (ffmpeg)...")
        output_video = out_dir / "output_final.mp4"
        args: list = [a for pair in inputs for a in pair]
        if clean["trim_enabled"]:
            # -ss sau inputs la output option (seek chinh xac ca video/audio) + -t gioi han do dai
            args += ["-ss", f"{clean['trim_start']:.3f}", "-t", f"{clean['trim_end'] - clean['trim_start']:.3f}"]
        args += ["-map", f"[{prev}]"]
        if audio_map:
            args += ["-map", audio_map["map"]]
        args += ["-filter_complex", fc]
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                 "-movflags", "+faststart", "-shortest"]
        args.append(output_video.name)
        try:
            await asyncio.to_thread(_run_ffmpeg, ffmpeg_path, args, out_dir)
        except RuntimeError as e:
            raise RuntimeError(f"Export video that bai: {e}") from e

        duration = max(probe["duration_sec"], 0.0)
        if clean["trim_enabled"]:
            duration = max(0.0, clean["trim_end"] - clean["trim_start"])
        elif duration <= 0:
            duration = round(float((await asyncio.to_thread(
                probe_video, ffmpeg_path, output_video)).get("duration_sec") or 0.0), 2)

        plan = {
            "export_id": export_id,
            "video_id": video_id,
            "source": clean["source"],
            "source_dub_id": source_dub_id,
            "trim": {"enabled": clean["trim_enabled"], "start": clean["trim_start"], "end": clean["trim_end"]},
            "options": clean,
            "resolved": {
                "source_path": str(source_path),
                "canvas": {"width": out_w, "height": out_h},
                "probe": probe,
            },
            "filters": {
                "fit": build_fit_chain(clean, out_w, out_h),
                "old_subtitle_mask": ({"region": _clamp_region(clean["old_subtitle_mask"], out_w, out_h)}
                                      if clean["old_subtitle_mask_enabled"] else None),
                "logo": clean["logo_asset_id"] if logo_path else None,
                "subtitle_burn": ass_path.name if ass_path else None,
            },
            "subtitle_source": subtitle_source,
            "subtitle_segments": (len(load_subtitle_segments(video_id, source_dub_id)) if ass_path else 0),
            "outputs": {
                "video": f"data/videos/{video_id}/exports/{export_id}/output_final.mp4",
                "subtitles": f"data/videos/{video_id}/exports/{export_id}/subtitles_rendered.ass",
                "logo": f"data/videos/{video_id}/exports/{export_id}/{logo_path.name}" if logo_path else None,
            },
            "created_at": lib.now_iso(),
        }
        (out_dir / "composer_plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

        metadata = {
            "export_id": export_id,
            "video_id": video_id,
            "source": clean["source"],
            "source_dub_id": source_dub_id,
            "created_at": lib.now_iso(),
            "aspect_ratio": clean["aspect_ratio"],
            "resolution": f"{out_w}x{out_h}",
            "width": out_w,
            "height": out_h,
            "fit_mode": clean["fit_mode"],
            "trim": {"enabled": clean["trim_enabled"], "start": clean["trim_start"], "end": clean["trim_end"]},
            "crop": (plan["filters"]["old_subtitle_mask"]["region"]
                     if clean["old_subtitle_mask_enabled"] else None),
            "subtitle_enabled": clean["subtitle_enabled"],
            "subtitle_position": clean["subtitle_position"],
            "subtitle_style": {
                "font_size": clean["subtitle_font_size"],
                "color": clean["subtitle_color"],
                "outline_color": clean["subtitle_outline_color"],
                "box": clean["subtitle_box"],
            },
            "old_subtitle_mask_enabled": clean["old_subtitle_mask_enabled"],
            "logo_enabled": clean["logo_enabled"],
            "logo_position": clean["logo_position"],
            "logo_scale": clean["logo_scale"],
            "logo_asset_id": clean["logo_asset_id"],
            "logo_opacity": clean["logo_opacity"],
            "output_path": f"data/videos/{video_id}/exports/{export_id}/output_final.mp4",
            "duration": round(duration, 2),
            "size_bytes": output_video.stat().st_size if output_video.exists() else 0,
            "status": "done",
        }
        (out_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

        _log(f"done: {output_video.name} {metadata['size_bytes']} bytes")
        await job_manager.finish_ok(
            job_id, [video_id],
            f"Da xuat video '{export_id}': {out_w}x{out_h} ({clean['fit_mode']}) -> exports/{export_id}/")
    except Exception as e:
        print(f"[video_job:{job_id}] LOI: {e!r}")
        print(traceback.format_exc())
        await job_manager.finish_fail(job_id, f"Compose that bai: {format_job_error(e)}")


def list_exports(video_id: str) -> list[dict]:
    items = []
    root = exports_root(video_id)
    if not root.exists():
        return items
    for d in sorted(root.iterdir()):
        if not (d.is_dir() and is_valid_export_id(d.name)):
            continue
        meta_file = d / "metadata.json"
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if isinstance(meta, dict) and meta.get("export_id"):
                items.append(meta)
        except Exception:
            continue
    items.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return items


# ─── Preview frame (MVP-8) ───

PREVIEW_ID_RE = re.compile(r"^prev_[A-Za-z0-9_]{1,64}$")


def previews_root(video_id: str) -> Path:
    return lib.video_dir(video_id) / "previews"


def is_valid_preview_id(preview_id: str) -> bool:
    return bool(preview_id) and bool(PREVIEW_ID_RE.match(preview_id))


def preview_path(video_id: str, preview_id: str) -> Path:
    if not is_valid_preview_id(preview_id):
        raise ValueError(f"preview_id khong hop le: {preview_id!r}")
    root = previews_root(video_id).resolve()
    p = (root / f"{preview_id}.jpg").resolve()
    if not str(p).startswith(str(root)):
        raise ValueError("Path nam ngoai previews")
    return p


def render_preview_frame(video_id: str, opts: dict) -> dict:
    """Render 1 frame voi cung filter graph (toan bo option compose + timestamp).

    Sync function — route phai goi qua asyncio.to_thread. Tra ve
    {preview_id, url, width, height, timestamp, image_rel, message}.
    """
    clean, err = validate_compose_options(opts)
    if err:
        raise ValueError(f"Options khong hop le: {err}")

    timestamp = _f_ge0(opts.get("timestamp"), "timestamp", 3.0)

    source_path, source_dub_id = _resolve_source(video_id, clean)
    probe = probe_video(resolve_ffmpeg(), source_path)
    if not probe["width"] or not probe["height"]:
        raise RuntimeError("Khong probe duoc kich thuoc video nguon.")
    src_dur = max(0.0, probe["duration_sec"])
    if clean["trim_enabled"] and src_dur > 0:
        clean["trim_start"] = min(clean["trim_start"], max(0.0, src_dur - 0.001))
        clean["trim_end"] = min(clean["trim_end"] or src_dur, src_dur)
        if clean["trim_end"] <= clean["trim_start"]:
            raise RuntimeError(
                "trim_end phai lon hon trim_start (sau khi clamp theo thoi luong video goc).")
        if clean["trim_end"] - clean["trim_start"] < 0.05:
            raise RuntimeError(
                "Vung trim qua ngan sau khi clamp theo thoi luong video goc — kiem tra trim_start/trim_end.")

    if clean["aspect_ratio"] == "custom":
        out_w, out_h = _even(clean["width"]), _even(clean["height"])
    elif clean["aspect_ratio"] == "original":
        out_w, out_h = _even(probe["width"]), _even(probe["height"])
    else:
        preset_w, preset_h = ASPECT_PRESETS[clean["aspect_ratio"]]
        out_w, out_h = _even(preset_w), _even(preset_h)

    if clean["trim_enabled"]:
        source_ts = clean["trim_start"] + timestamp
        lo, hi = clean["trim_start"], clean["trim_end"]
    else:
        source_ts = timestamp
        lo, hi = 0.0, src_dur
    if hi > lo:
        source_ts = min(max(source_ts, lo), hi)
    else:
        source_ts = lo

    ffmpeg_path = resolve_ffmpeg()

    preview_root = previews_root(video_id)
    preview_root.mkdir(parents=True, exist_ok=True)
    preview_id = f"prev_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
    work_dir = preview_root / preview_id
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        ass_path = None
        subtitle_source = None
        if clean["subtitle_enabled"]:
            segments = load_subtitle_segments(video_id, source_dub_id)
            if not segments:
                raise RuntimeError(
                    "Video chua co transcript tieng Viet / subtitle — hay 'Translate to Vietnamese' truoc.")
            ass_path = work_dir / "subtitles_rendered.ass"
            ass_path.write_text(build_ass(segments, clean, out_w, out_h), encoding="utf-8")
            subtitle_source = "transcript_vi.json" if lib.get_transcript_vi(video_id) else "subtitles_vi.srt"

        logo_path = None
        if clean["logo_enabled"]:
            logo_path = _get_asset_path(video_id, clean["logo_asset_id"])
            if logo_path is None:
                raise RuntimeError(f"Logo asset '{clean['logo_asset_id']}' khong ton tai trong assets.")
            shutil.copy2(str(logo_path), str(work_dir / logo_path.name))

        inputs = [("-ss", f"{source_ts:.3f}"), ("-i", str(source_path))]
        if logo_path:
            inputs.append(("-i", str(logo_path)))

        fc_parts = [f"[0:v]{build_fit_chain(clean, out_w, out_h)}[vfit]"]
        prev = "vfit"
        if clean["old_subtitle_mask_enabled"]:
            chain, prev = build_mask_chain(prev, clean, out_w, out_h)
            fc_parts.append(chain)
        if logo_path:
            chain, prev = build_logo_chain(prev, 1, clean, out_w, out_h)
            fc_parts.append(chain)
        if ass_path is not None:
            fc_parts.append(f"[{prev}]ass=filename=subtitles_rendered.ass[vout]")
            prev = "vout"
        fc = ";".join(fc_parts)

        args = [a for pair in inputs for a in pair]
        args += ["-filter_complex", fc]
        args += ["-map", f"[{prev}]", "-frames:v", "1", "-q:v", "2",
                 "-f", "image2", "preview.jpg"]
        _run_ffmpeg(ffmpeg_path, args, work_dir)

        metadata = {
            "preview_id": preview_id,
            "video_id": video_id,
            "source": clean["source"],
            "source_dub_id": source_dub_id,
            "timestamp": round(source_ts, 3),
            "trim": {"enabled": clean["trim_enabled"], "start": clean["trim_start"], "end": clean["trim_end"]},
            "width": out_w,
            "height": out_h,
            "fit_mode": clean["fit_mode"],
            "old_subtitle_mask_enabled": clean["old_subtitle_mask_enabled"],
            "logo_enabled": clean["logo_enabled"],
            "subtitle_enabled": clean["subtitle_enabled"],
            "image": f"data/videos/{video_id}/previews/{preview_id}.jpg",
            "created_at": lib.now_iso(),
        }
        (preview_root / f"{preview_id}.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        (work_dir / "preview.jpg").replace(preview_root / f"{preview_id}.jpg")
        return {
            "preview_id": preview_id,
            "url": f"/api/videos/{video_id}/compose-preview/{preview_id}",
            "width": out_w,
            "height": out_h,
            "timestamp": round(source_ts, 3),
            "image": metadata["image"],
            "subtitle_source": subtitle_source,
            "message": f"Preview {out_w}x{out_h} tai t={source_ts:.1f}s.",
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)