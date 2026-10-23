"""
Video composer — FFmpeg compositing: multiple mask regions + subtitle burn.
Filter order: fit -> masks -> logo -> subtitle (masks BEFORE subtitle).

Frontend passes segment list for subtitle preview (max 2 lines per segment).
"""
import asyncio
import json
import re
import subprocess
import traceback
import uuid
from pathlib import Path

from video_jobs import job_manager, format_job_error
from video_dl.transcribe import resolve_ffmpeg
import video_library as lib

MASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
MAX_MASKS = 10


def _hex_to_ass_color(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return "&H00FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def validate_compose_options(opts: dict) -> tuple[dict, dict | None]:
    clean = {
        "source": opts.get("source", "original"),
        "dub_id": opts.get("dub_id"),
        "aspect_ratio": opts.get("aspect_ratio", "original"),
        "width": int(opts.get("width", 1080)),
        "height": int(opts.get("height", 1920)),
        "fit_mode": opts.get("fit_mode", "contain"),
        "background_color": opts.get("background_color", "#000000"),
        "subtitle_enabled": bool(opts.get("subtitle_enabled", False)),
        "subtitle_position": opts.get("subtitle_position", "bottom"),
        "subtitle_font_size": int(opts.get("subtitle_font_size", 48)),
        "subtitle_color": opts.get("subtitle_color", "#FFFFFF"),
        "subtitle_outline_color": opts.get("subtitle_outline_color", "#000000"),
        "subtitle_outline_width": int(opts.get("subtitle_outline_width", 2)),
        "subtitle_box": bool(opts.get("subtitle_box", False)),
        "subtitle_box_color": opts.get("subtitle_box_color", "#000000"),
        "subtitle_box_opacity": float(opts.get("subtitle_box_opacity", 0.75)),
        "subtitle_margin_bottom": int(opts.get("subtitle_margin_bottom", 60)),
        "subtitle_max_chars": int(opts.get("subtitle_max_chars", 22)),
        "logo_enabled": bool(opts.get("logo_enabled", False)),
        "logo_asset_id": opts.get("logo_asset_id"),
        "logo_position": opts.get("logo_position", "top-right"),
        "logo_scale": float(opts.get("logo_scale", 12.0)),
        "logo_opacity": float(opts.get("logo_opacity", 0.9)),
        "logo_margin": int(opts.get("logo_margin", 20)),
        "logo_x": float(opts.get("logo_x", 50.0)),
        "logo_y": float(opts.get("logo_y", 50.0)),
        "trim_enabled": bool(opts.get("trim_enabled", False)),
        "trim_start": float(opts.get("trim_start", 0.0)),
        "trim_end": float(opts.get("trim_end", 0.0)),
        "output_name": opts.get("output_name"),
        "subtitle_segments": opts.get("subtitle_segments", []),
    }

    old_mask_enabled = bool(opts.get("old_subtitle_mask_enabled", False))
    old_masks_enabled = bool(opts.get("old_subtitle_masks_enabled", False))
    old_masks = opts.get("old_subtitle_masks") or []
    single_mask = opts.get("old_subtitle_mask")

    if not old_masks_enabled and old_mask_enabled and single_mask:
        if isinstance(single_mask, dict):
            m = dict(single_mask)
            m.setdefault("id", "mask_1")
            m.setdefault("enabled", True)
            old_masks = [m]
            old_masks_enabled = True

    clean["old_subtitle_masks_enabled"] = old_masks_enabled
    clean["old_subtitle_mask_enabled"] = old_masks_enabled
    clean["old_subtitle_masks"] = old_masks

    if not old_masks_enabled or not old_masks:
        clean["old_subtitle_masks"] = []
        return clean, None

    if len(old_masks) > MAX_MASKS:
        return clean, {"detail": f"Toi da {MAX_MASKS} vung che (hien co {len(old_masks)})."}

    validated = []
    for m in old_masks:
        mid = str(m.get("id", ""))
        if not MASK_ID_RE.match(mid):
            return clean, {"detail": f"Mask id khong hop le: {mid!r}"}
        try:
            x = float(m.get("x", 0))
            y = float(m.get("y", 0))
            w = float(m.get("w", 0))
            h = float(m.get("h", 0))
        except (TypeError, ValueError):
            return clean, {"detail": f"Mask '{mid}': x/y/w/h phai la so."}
        if not (0 <= x <= 100 and 0 <= y <= 100 and 0 <= w <= 100 and 0 <= h <= 100):
            return clean, {"detail": f"Mask '{mid}': x/y/w/h phai nam trong 0..100."}
        if w < 1 or h < 1:
            return clean, {"detail": f"Mask '{mid}': w/h phai >= 1."}
        if x + w > 100 or y + h > 100:
            return clean, {"detail": f"Mask '{mid}' phai nam trong 100% (x+w<=100, y+h<=100)."}
        mode = str(m.get("mode", "blur")).lower()
        if mode not in ("blur", "box"):
            mode = "blur"
        blur = max(0, min(50, int(m.get("blur", 18))))
        box_opacity = max(0.0, min(1.0, float(m.get("box_opacity", 0.65))))
        validated.append({
            "id": mid, "x": x, "y": y, "w": w, "h": h,
            "mode": mode, "blur": blur, "box_opacity": box_opacity,
            "enabled": bool(m.get("enabled", True)),
        })

    clean["old_subtitle_masks"] = validated
    return clean, None


def build_fit_chain(clean: dict, out_w: int, out_h: int) -> str:
    return (
        f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
        f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black"
    )


def build_fc_plan(clean: dict, out_w: int, out_h: int) -> dict:
    """Build plan metadata from options (no ffmpeg needed) — reflects the exact
    filter order used in run_compose_job: fit -> masks -> logo -> subtitle."""
    parts = [f"[0:v]{build_fit_chain(clean, out_w, out_h)}[vfit]"]
    prev = "vfit"
    enabled_masks = [m for m in clean.get("old_subtitle_masks", []) if m.get("enabled")]
    for i, m in enumerate(enabled_masks):
        chain, prev = build_mask_chain(prev, m, out_w, out_h, i)
        parts.append(chain)
    if clean.get("subtitle_enabled"):
        parts.append(f"[{prev}]ass=filename=subtitles_rendered.ass[vout]")
        prev = "vout"
    return {
        "filter_graph": ";".join(parts),
        "enabled_masks": enabled_masks,
        "subtitle_enabled": bool(clean.get("subtitle_enabled")),
        "logo_enabled": bool(clean.get("logo_enabled")),
    }


def build_mask_chain(prev: str, mask: dict, out_w: int, out_h: int, idx: int) -> tuple[str, str]:
    x_pct = mask["x"] / 100.0
    y_pct = mask["y"] / 100.0
    w_pct = mask["w"] / 100.0
    h_pct = mask["h"] / 100.0
    mode = mask.get("mode", "blur")
    label = f"vmask{idx}"

    if mode == "box":
        opacity = mask.get("box_opacity", 0.65)
        chain = (
            f"[{prev}]drawbox="
            f"x=iw*{x_pct:.6f}:y=ih*{y_pct:.6f}:"
            f"w=iw*{w_pct:.6f}:h=ih*{h_pct:.6f}:"
            f"color=black@{opacity:.4f}:t=fill"
            f"[{label}]"
        )
    else:
        blur_val = mask.get("blur", 18)
        chain = (
            f"[{prev}]split[bg{idx}][tmp{idx}];"
            f"[tmp{idx}]crop=iw*{w_pct:.6f}:ih*{h_pct:.6f}:"
            f"iw*{x_pct:.6f}:ih*{y_pct:.6f},"
            f"boxblur={blur_val}:{blur_val}[blr{idx}];"
            f"[bg{idx}][blr{idx}]overlay="
            f"iw*{x_pct:.6f}:ih*{y_pct:.6f}:format=auto"
            f"[{label}]"
        )

    return chain, label


def build_ass(segments: list[dict], opts: dict, out_w: int, out_h: int) -> str:
    font_size = int(opts.get("subtitle_font_size", 48))
    primary_color = _hex_to_ass_color(opts.get("subtitle_color", "#FFFFFF"))
    outline_color = _hex_to_ass_color(opts.get("subtitle_outline_color", "#000000"))
    outline_width = int(opts.get("subtitle_outline_width", 2))
    margin_bottom = int(opts.get("subtitle_margin_bottom", 60))
    back_color = _hex_to_ass_color(opts.get("subtitle_box_color", "#000000"))

    position = opts.get("subtitle_position", "bottom")
    if position == "top":
        alignment = 8
    elif position == "middle":
        alignment = 5
    else:
        alignment = 2

    if opts.get("subtitle_box", False):
        border_style = 3
        outline = 0
    else:
        border_style = 1
        outline = outline_width

    ass = (
        "[Script Info]\n"
        "Title: CapCapTTS Subtitles\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {out_w}\n"
        f"PlayResY: {out_h}\n"
        "WrapStyle: 0\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Arial,{font_size},{primary_color},&H000000FF,{outline_color},"
        f"{back_color},0,0,0,0,100,100,0,0,{border_style},{outline},1,{alignment},"
        f"10,10,{margin_bottom},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    for seg in segments:
        start = _ass_time(seg.get("start", 0))
        end = _ass_time(seg.get("end", 0))
        text = (seg.get("text", "") or "").replace("\n", "\\N")
        if text.strip():
            ass += f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n"

    return ass


def _escape_ffmpeg_path(p: str) -> str:
    return p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def resolve_compose_media(video_id: str, opts: dict):
    """Chọn nguồn media cho compose. Nếu source='dub' và có dub_id hợp lệ, dùng
    output.mp4 của dub đó; ngược lại fallback về index.mp4 gốc."""
    source = (opts.get("source") or "original").strip().lower()
    if source == "dub":
        dub_id = opts.get("dub_id")
        if dub_id:
            try:
                from video_dubber import is_valid_dub_id, dub_output_dir
                if is_valid_dub_id(dub_id):
                    p = dub_output_dir(video_id, dub_id) / "output.mp4"
                    if p:
                        return p
            except Exception:
                pass
    return lib.video_dir(video_id) / "index.mp4"


async def run_compose_job(job_id: str, video_id: str, clean_opts: dict):
    try:
        await job_manager.update(job_id, status="running", progress=5, message="Dang chuan bi compose...")

        video_path = resolve_compose_media(video_id, clean_opts)
        if not video_path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")

        try:
            ffmpeg_path = resolve_ffmpeg()
        except RuntimeError as e:
            raise RuntimeError(f"Khong tim thay ffmpeg: {e}") from e

        out_w = int(clean_opts.get("width", 1080))
        out_h = int(clean_opts.get("height", 1920))

        await job_manager.update(job_id, progress=15, message="Dang xay filter graph...")

        parts = [f"[0:v]{build_fit_chain(clean_opts, out_w, out_h)}[vfit]"]
        prev = "vfit"
        enabled_masks = [m for m in clean_opts.get("old_subtitle_masks", []) if m.get("enabled")]
        for i, m in enumerate(enabled_masks):
            chain, prev = build_mask_chain(prev, m, out_w, out_h, i)
            parts.append(chain)

        export_id = f"exp_{uuid.uuid4().hex[:12]}"
        export_dir = lib.video_dir(video_id) / "exports" / export_id
        export_dir.mkdir(parents=True, exist_ok=True)

        if clean_opts.get("subtitle_enabled"):
            segments = clean_opts.get("subtitle_segments", [])
            ass_content = build_ass(segments, clean_opts, out_w, out_h)
            ass_path = export_dir / "subtitles_rendered.ass"
            ass_path.write_text(ass_content, encoding="utf-8")
            escaped = _escape_ffmpeg_path(str(ass_path))
            parts.append(f"[{prev}]ass='{escaped}'[vout]")
            prev = "vout"

        filter_complex = ";".join(parts)

        await job_manager.update(job_id, progress=30, message="Dang export video...")

        output_video = export_dir / "output.mp4"
        args = [
            "-i", str(video_path),
            "-filter_complex", filter_complex,
            "-map", f"[{prev}]",
            "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            str(output_video),
        ]

        proc = subprocess.run(
            [ffmpeg_path, "-y", *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(export_dir),
        )

        if proc.returncode != 0:
            tail = (proc.stderr or "")[-3000:]
            raise RuntimeError(f"ffmpeg loi (exit {proc.returncode}):\n{tail}")

        await job_manager.update(job_id, progress=85, message="Dang luu composer plan...")

        plan = {
            "video_id": video_id,
            "export_id": export_id,
            "created_at": lib.now_iso(),
            "filters": {
                "filter_order": "fit -> masks -> logo -> subtitle",
                "old_subtitle_masks": enabled_masks,
                "subtitle_enabled": bool(clean_opts.get("subtitle_enabled")),
                "logo_enabled": bool(clean_opts.get("logo_enabled")),
            },
            "output": {
                "width": out_w,
                "height": out_h,
                "video": f"exports/{export_id}/output.mp4",
            },
        }
        (export_dir / "composer_plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        await lib.update_video(
            video_id,
            compose_status="done",
            compose_export_id=export_id,
            compose_output_path=f"data/videos/{video_id}/exports/{export_id}/output.mp4",
            composed_at=lib.now_iso(),
        )

        await job_manager.finish_ok(
            job_id, [video_id],
            f"Compose done: {export_id} ({out_w}x{out_h}, {len(enabled_masks)} masks)",
        )
    except Exception as e:
        print(f"[compose_job:{job_id}] LOI: {e!r}")
        print(traceback.format_exc())
        await lib.update_video(video_id, compose_status="failed", compose_error=format_job_error(e))
        await job_manager.finish_fail(job_id, f"Compose that bai: {e}")


async def render_preview_frame(video_path: Path, clean_opts: dict, time_sec: float) -> bytes | None:
    try:
        ffmpeg_path = resolve_ffmpeg()
    except RuntimeError:
        return None

    out_w = int(clean_opts.get("width", 1080))
    out_h = int(clean_opts.get("height", 1920))

    parts = [f"[0:v]{build_fit_chain(clean_opts, out_w, out_h)}[vfit]"]
    prev = "vfit"
    enabled_masks = [m for m in clean_opts.get("old_subtitle_masks", []) if m.get("enabled")]
    for i, m in enumerate(enabled_masks):
        chain, prev = build_mask_chain(prev, m, out_w, out_h, i)
        parts.append(chain)

    ass_path = None
    if clean_opts.get("subtitle_enabled"):
        segments = clean_opts.get("subtitle_segments", [])
        if segments:
            import tempfile
            ass_content = build_ass(segments, clean_opts, out_w, out_h)
            tmp = tempfile.NamedTemporaryFile(suffix=".ass", delete=False, mode="w", encoding="utf-8")
            tmp.write(ass_content)
            tmp.close()
            ass_path = Path(tmp.name)
            escaped = _escape_ffmpeg_path(str(ass_path))
            parts.append(f"[{prev}]ass='{escaped}'[vout]")
            prev = "vout"

    filter_complex = ";".join(parts)

    try:
        proc = subprocess.run(
            [
                ffmpeg_path, "-y",
                "-ss", str(time_sec),
                "-i", str(video_path),
                "-filter_complex", filter_complex,
                "-map", f"[{prev}]",
                "-frames:v", "1",
                "-f", "image2",
                "-c:v", "mjpeg",
                "-q:v", "2",
                "pipe:1",
            ],
            capture_output=True, timeout=15,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
    except Exception:
        pass
    finally:
        if ass_path:
            ass_path.unlink(missing_ok=True)

    return None
