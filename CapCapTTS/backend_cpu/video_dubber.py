"""
Video dubber service — lồng tiếng Việt vào video theo transcript_vi.json.

Output layout (trong data/videos/<video_id>/dub/):
  segments/segment_NNNN.wav         — audio TTS từng segment (đã pad/stretch)
  outputs/<dub_id>/                 — mỗi lần dub 1 output riêng (MVP-4):
    output.mp4
    final_dub.wav
    subtitles_vi.srt
    dubbing_plan.json
    metadata.json
  output_dubbed.mp4                 — latest (backward-compat MVP-3)
  final_dub.wav, subtitles_vi.srt, dubbing_plan.json — latest

audio_mode:
  mix           — trộn audio gốc (original_volume) + audio lồng (dub_volume), alimiter chống clip
  replace       — bỏ audio gốc, chỉ dùng audio lồng tiếng
  dub_only      — video muted + final_dub.wav (chỉ xuất audio lồng tiếng)
  original_only — debug: video gốc với original_volume
  duck          — alias cũ của mix
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

from pydub import AudioSegment

from video_jobs import job_manager, format_job_error
from video_dl.transcribe import resolve_ffmpeg
from tts_engine import PiperEngine
import video_library as lib

MAX_SPEED_LIMIT = 1.5
MIN_SPEED = 1.0
DEFAULT_VOICE = "banmai"
PAD_TOLERANCE_MS = 40
DUB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_engine: PiperEngine | None = None


def _get_engine() -> PiperEngine:
    global _engine
    if _engine is None:
        _engine = PiperEngine()
    return _engine


def dub_dir(video_id: str) -> Path:
    return lib.video_dir(video_id) / "dub"


def segment_dir(video_id: str) -> Path:
    return dub_dir(video_id) / "segments"


def dub_outputs_dir(video_id: str) -> Path:
    return dub_dir(video_id) / "outputs"


def is_valid_dub_id(dub_id: str) -> bool:
    return bool(dub_id) and bool(DUB_ID_RE.match(dub_id))


def dub_output_dir(video_id: str, dub_id: str) -> Path:
    if not is_valid_dub_id(dub_id):
        raise ValueError(f"dub_id không hợp lệ: {dub_id!r}")
    out_root = dub_outputs_dir(video_id).resolve()
    d = (out_root / dub_id).resolve()
    if not str(d).startswith(str(out_root)):
        raise ValueError("Path nằm ngoài dub outputs")
    return d


def sanitize_output_name(name: str) -> str | None:
    name = re.sub(r"[^A-Za-z0-9_-]", "_", (name or "").strip())[:48]
    return name or None


def list_dub_outputs(video_id: str) -> list[dict]:
    """Đọc toàn bộ metadata.json trong dub/outputs/*, mới nhất trước."""
    items = []
    out_root = dub_outputs_dir(video_id)
    if not out_root.exists():
        return items
    for d in sorted(out_root.iterdir()):
        if not (d.is_dir() and is_valid_dub_id(d.name)):
            continue
        meta_file = d / "metadata.json"
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if isinstance(meta, dict) and meta.get("dub_id"):
                items.append(meta)
        except Exception:
            continue
    items.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return items


def dub_plan_summary(plan: dict) -> dict:
    segs = plan.get("segments") or []
    ok = padded = overshoot = warnings = 0
    for e in segs:
        if e.get("warning"):
            warnings += 1
        if e.get("status") == "ok":
            ok += 1
        elif e.get("status") == "padded":
            padded += 1
        elif e.get("status") == "warning":
            overshoot += 1
    return {
        "segment_count": len(segs),
        "ok_count": ok,
        "padded_count": padded,
        "overshoot_count": overshoot,
        "warnings_count": warnings,
        "timeline_duration_sec": plan.get("timeline_duration_sec", 0),
    }


def _probe_duration(ffmpeg: str, video_path: Path) -> float | None:
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(video_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr or "")
        if m:
            h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            return h * 3600 + mi * 60 + s
    except Exception:
        pass
    return None


def _has_audio_stream(ffmpeg: str, video_path: Path) -> bool:
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(video_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        return bool(re.search(r"Stream\s+#0:\d+.*Audio:", proc.stderr or ""))
    except Exception:
        return False


def _run_ffmpeg(ffmpeg: str, args: list, cwd: Path) -> str:
    proc = subprocess.run(
        [ffmpeg, "-y", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", cwd=str(cwd),
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-3000:]
        raise RuntimeError(f"ffmpeg lỗi (exit {proc.returncode}):\n{tail}")
    return proc.stderr or ""


def _stretch_wav(ffmpeg: str, src: Path, dst: Path, speed: float) -> None:
    _run_ffmpeg(ffmpeg, [
        "-i", str(src),
        "-filter:a", f"atempo={speed:.4f}",
        "-sample_fmt", "s16",
        "-ac", "1",
        str(dst),
    ], src.parent)


def _srt_timestamp(sec: float) -> str:
    sec = max(0.0, sec)
    ms = int(round(sec * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def build_subtitles_srt(segments: list[dict]) -> str:
    blocks = []
    for i, seg in enumerate(segments, start=1):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or start)
        blocks.append(
            f"{i}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}\n"
        )
    return "\n".join(blocks)


def _normalize_to_db(audio: AudioSegment, target_db: float = -20.0) -> AudioSegment:
    if audio.dBFS != float("-inf"):
        change = target_db - audio.dBFS
        if abs(change) > 0.5:
            audio = audio.apply_gain(change)
    return audio


def _sync_to_latest(dub_root: Path, dub_id: str) -> None:
    """Copy output mới nhất sang các file latest (backward-compat MVP-3)."""
    src = dub_root / "outputs" / dub_id
    latest = {
        "output.mp4": dub_root / "output_dubbed.mp4",
        "final_dub.wav": dub_root / "final_dub.wav",
        "subtitles_vi.srt": dub_root / "subtitles_vi.srt",
        "dubbing_plan.json": dub_root / "dubbing_plan.json",
    }
    for name, dst in latest.items():
        s = src / name
        if s.exists():
            shutil.copy2(str(s), str(dst))


async def run_dub_job(
    job_id: str,
    video_id: str,
    audio_mode: str = "mix",
    original_volume: float = 0.05,
    dub_volume: float = 1.2,
    max_speed: float = 1.25,
    burn_subtitles: bool = False,
    voice_id: str = DEFAULT_VOICE,
    output_name: str | None = None,
    normalize_audio: bool = False,
):
    video = await lib.get_video(video_id)
    if not video:
        await job_manager.finish_fail(job_id, f"Không tìm thấy video {video_id} trong library.")
        return

    d = lib.video_dir(video_id)
    video_path = d / "index.mp4"
    if not video_path.exists():
        await job_manager.finish_fail(job_id, "File video không tồn tại (đã bị xóa?).")
        return

    tvi = lib.get_transcript_vi(video_id)
    segments = (tvi or {}).get("segments") if tvi else None
    if not segments:
        await job_manager.finish_fail(job_id, "Video chưa có transcript tiếng Việt (dùng transcript_vi.json) — hãy bấm 'Translate to Vietnamese' trước.")
        return

    audio_mode = (audio_mode or "mix").lower()
    if audio_mode == "duck":
        audio_mode = "mix"
    if audio_mode not in ("mix", "replace", "dub_only", "original_only"):
        await job_manager.finish_fail(job_id, f"audio_mode không hợp lệ: {audio_mode!r}. Cho phép: mix, replace, dub_only, original_only.")
        return
    original_volume = max(0.0, min(1.0, float(original_volume if original_volume is not None else 0.05)))
    dub_volume = max(0.0, min(2.0, float(dub_volume if dub_volume is not None else 1.2)))
    max_speed = max(MIN_SPEED, min(MAX_SPEED_LIMIT, float(max_speed or 1.25)))
    # MVP-6: original_volume <= 0.01 được coi là mute original (tránh còn tiếng gốc)
    original_muted = original_volume <= 0.01
    if original_muted:
        original_volume = 0.0

    engine = _get_engine()
    if not engine.has_voice(voice_id):
        await job_manager.finish_fail(job_id, f"Voice '{voice_id}' không tồn tại trong {engine._models_dir}.")
        return

    out_dir = dub_dir(video_id)
    seg_dir = segment_dir(video_id)
    seg_dir.mkdir(parents=True, exist_ok=True)

    try:
        await lib.update_video(video_id, dub_status="dubbing")
        await job_manager.update(job_id, status="running", progress=5,
                                 message=f"Đang tổng hợp audio {len(segments)} segment (voice {voice_id})...")

        def _log(msg: str):
            print(f"[video_job:{job_id}] {msg}")

        _log(f"dub start: {len(segments)} segments, mode={audio_mode}, orig={original_volume}, dub={dub_volume}, max_speed={max_speed}, burn={burn_subtitles}")

        plan_entries: list[dict] = []
        last_end = 0.0
        placed: list[tuple[int, dict, AudioSegment]] = []

        for idx, seg in enumerate(segments):
            start = round(float(seg.get("start") or 0), 3)
            end = round(float(seg.get("end") or start), 3)
            target_ms = max(0.0, (end - start) * 1000)
            text = (seg.get("text") or "").strip()
            last_end = max(last_end, end)

            entry = {
                "index": idx,
                "start": start,
                "end": end,
                "target_duration": round(target_ms / 1000, 3),
                "generated_duration": 0.0,
                "speed_applied": 1.0,
                "status": "ok",
                "warning": None,
                "file": f"dub/segments/segment_{idx:04d}.wav",
            }
            if not text:
                entry.update(status="skipped", warning="empty text")
                plan_entries.append(entry)
                continue
            if target_ms <= 0:
                entry.update(status="skipped", warning="invalid timing (end <= start)")
                plan_entries.append(entry)
                continue

            try:
                audio = await asyncio.to_thread(engine.synthesize, text, voice_id)
                audio = _normalize_to_db(audio)
                gen_ms = float(len(audio))
                entry["generated_duration"] = round(gen_ms / 1000, 3)
                speed_applied = 1.0
                over = gen_ms - target_ms

                if over > PAD_TOLERANCE_MS:
                    needed = gen_ms / max(target_ms, 1.0)
                    speed = round(min(max_speed, needed), 4)
                    if speed > 1.0:
                        tmp_wav = seg_dir / f"segment_{idx:04d}_tmp.wav"
                        stretched_wav = seg_dir / f"segment_{idx:04d}_stretched.wav"
                        try:
                            audio.export(tmp_wav, format="wav")
                            _stretch_wav(resolve_ffmpeg(), tmp_wav, stretched_wav, speed)
                            audio = AudioSegment.from_file(stretched_wav)
                        finally:
                            tmp_wav.unlink(missing_ok=True)
                            stretched_wav.unlink(missing_ok=True)
                        speed_applied = speed
                        gen_ms = float(len(audio))
                        entry["generated_duration"] = round(gen_ms / 1000, 3)
                        entry["speed_applied"] = speed_applied
                        over = gen_ms - target_ms
                        if over > PAD_TOLERANCE_MS:
                            entry["status"] = "warning"
                            entry["warning"] = (
                                f"overshoot vẫn dài hơn khung {round(over)}ms sau khi stretch "
                                f"(speed bị chặn ở {max_speed}x)"
                                if speed >= max_speed
                                else f"overshoot {round(over)}ms (speed {speed_applied}x)"
                            )
                    else:
                        entry["status"] = "warning"
                        entry["warning"] = f"dài hơn khung {round(over)}ms nhưng speed {needed:.2f}x vượt giới hạn {max_speed}x"
                        entry["speed_applied"] = speed_applied
                elif over < -PAD_TOLERANCE_MS:
                    pad = int(round(-over))
                    audio = audio + AudioSegment.silent(duration=pad)
                    entry["status"] = "padded"
                    entry["warning"] = f"thêm {pad}ms silence cho đủ khung"
                    entry["generated_duration"] = round(len(audio) / 1000, 3)

                wav = seg_dir / f"segment_{idx:04d}.wav"
                audio.export(wav, format="wav")
                placed.append((idx, entry, audio))
                plan_entries.append(entry)
            except Exception as e:
                print(f"[video_job:{job_id}] segment {idx} LỖI: {e!r} {traceback.format_exc()}")
                entry.update(status="warning", warning=f"TTS segment thất bại: {format_job_error(e)}")
                plan_entries.append(entry)

            if (idx + 1) % 5 == 0 or idx + 1 == len(segments):
                await job_manager.update(
                    job_id,
                    progress=5 + int(50 * (idx + 1) / max(1, len(segments))),
                    message=f"TTS segment {idx + 1}/{len(segments)}...",
                )

        _log(f"synthesized {len(placed)}/{len(segments)} segments")

        try:
            ffmpeg_path = resolve_ffmpeg()
        except RuntimeError as e:
            raise RuntimeError(f"Không tìm thấy ffmpeg: {e}") from e

        # ─── Dub ID ───
        name = sanitize_output_name(output_name or "")
        base_id = name or f"dub_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
        dub_id = base_id
        suffix = 2
        while (dub_outputs_dir(video_id) / dub_id).exists():
            dub_id = f"{base_id}_{suffix}"
            suffix += 1
        new_dir = dub_outputs_dir(video_id) / dub_id
        new_dir.mkdir(parents=True, exist_ok=True)
        _log(f"output dir: {new_dir}")

        # ─── Timeline ───
        await job_manager.update(job_id, progress=62, message="Đang ghép audio trên timeline...")
        duration_sec = lib.get_duration(video_id) or _probe_duration(ffmpeg_path, video_path) or 0.0
        total_ms = int(max(duration_sec * 1000, (last_end + 0.4) * 1000))
        track = AudioSegment.silent(duration=total_ms, frame_rate=22050)
        for idx, entry, audio in placed:
            track = track.overlay(audio, position=int(entry["start"] * 1000))
        final_out_wav = new_dir / "final_dub.wav"
        track.export(final_out_wav, format="wav")
        if normalize_audio:
            await job_manager.update(job_id, progress=66, message="Đang normalize loudness (loudnorm)...")
            norm_wav = new_dir / "final_dub_loudnorm.wav"
            try:
                _run_ffmpeg(ffmpeg_path, [
                    "-i", str(final_out_wav),
                    "-filter:a", "loudnorm=I=-16:TP=-1.5:LRA=11",
                    "-ar", "44100",
                    "-ac", "1",
                    "-sample_fmt", "s16",
                    str(norm_wav),
                ], new_dir)
                norm_wav.replace(final_out_wav)
                _log("loudnorm áp dụng cho final_dub.wav")
            except RuntimeError as e:
                _log(f"loudnorm thất bại (giữ bản gốc): {e}")
                norm_wav.unlink(missing_ok=True)
        _log(f"timeline: {len(track) / 1000:.2f}s -> {final_out_wav.name}")

        # ─── Subtitles ───
        srt_path = new_dir / "subtitles_vi.srt"
        srt_path.write_text(build_subtitles_srt(segments), encoding="utf-8")

        # ─── Export video ───
        await job_manager.update(job_id, progress=72, message=f"Đang export video (mode {audio_mode})...")
        output_video = new_dir / "output.mp4"
        args: list = ["-i", str(video_path), "-i", str(final_out_wav)]

        has_audio = _has_audio_stream(ffmpeg_path, video_path)
        if audio_mode == "dub_only":
            args += ["-map", "0:v:0", "-an"]
        elif audio_mode == "original_only":
            if has_audio:
                args += ["-filter_complex", f"[0:a]volume={original_volume:.4f}[aout]", "-map", "0:v:0", "-map", "[aout]"]
            else:
                args += ["-map", "0:v:0", "-an"]
        elif audio_mode == "replace":
            args += ["-map", "0:v:0", "-map", "1:a:0"]
        else:  # mix
            if not has_audio or original_volume <= 0:
                args += ["-map", "0:v:0", "-map", "1:a:0"]
            else:
                fc = (
                    f"[0:a]volume={original_volume:.4f}[orig];"
                    f"[1:a]volume={dub_volume:.4f}[dub];"
                    f"[orig][dub]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95[aout]"
                )
                args += ["-filter_complex", fc, "-map", "0:v:0", "-map", "[aout]"]

        if burn_subtitles:
            args += ["-vf", "subtitles=subtitles_vi.srt"]
        if burn_subtitles:
            args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
        else:
            args += ["-c:v", "copy"]
        if "-an" not in args:
            args += ["-c:a", "aac", "-b:a", "192k"]
        args.append(output_video.name)

        try:
            _run_ffmpeg(ffmpeg_path, args, new_dir)
        except RuntimeError as e:
            raise RuntimeError(f"Export video thất bại: {e}") from e

        # ─── Plan + metadata ───
        plan = {
            "video_id": video_id,
            "dub_id": dub_id,
            "created_at": lib.now_iso(),
            "voice_id": voice_id,
            "audio_mode": audio_mode,
            "original_volume": round(original_volume, 4),
            "original_muted": bool(original_muted),
            "dub_volume": round(dub_volume, 4),
            "max_speed": max_speed,
            "burn_subtitles": bool(burn_subtitles),
            "normalize_audio": bool(normalize_audio),
            "timeline_duration_sec": round(total_ms / 1000, 3),
            "segments": plan_entries,
            "outputs": {
                "dub_audio": f"dub/outputs/{dub_id}/final_dub.wav",
                "video": f"dub/outputs/{dub_id}/output.mp4",
                "subtitles": f"dub/outputs/{dub_id}/subtitles_vi.srt",
                "latest": {
                    "video": "dub/output_dubbed.mp4",
                    "dub_audio": "dub/final_dub.wav",
                },
            },
        }
        (new_dir / "dubbing_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        summary = dub_plan_summary(plan)
        metadata = {
            "dub_id": dub_id,
            "video_id": video_id,
            "created_at": lib.now_iso(),
            "audio_mode": audio_mode,
            "original_volume": round(original_volume, 4),
            "original_muted": bool(original_muted),
            "dub_volume": round(dub_volume, 4),
            "max_speed": max_speed,
            "burn_subtitles": bool(burn_subtitles),
            "normalize_audio": bool(normalize_audio),
            "voice_id": voice_id,
            "status": "done",
            "output_path": f"data/videos/{video_id}/dub/outputs/{dub_id}/output.mp4",
            "duration": round(total_ms / 1000, 3),
            "size_bytes": output_video.stat().st_size if output_video.exists() else 0,
            **summary,
        }
        (new_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

        _sync_to_latest(out_dir, dub_id)

        await lib.update_video(
            video_id,
            dub_status="done",
            dub_latest_id=dub_id,
            dub_output_path=f"data/videos/{video_id}/dub/output_dubbed.mp4",
            dub_plan_path=f"data/videos/{video_id}/dub/dubbing_plan.json",
            dub_voice_id=voice_id,
            dub_mode=audio_mode,
            dubbed_at=lib.now_iso(),
        )
        _log(f"done: {output_video.name} ({len(plan_entries)} segments, warnings={summary['warnings_count']})")
        await job_manager.finish_ok(
            job_id, [video_id],
            f"Đã tạo dub '{dub_id}': {len(plan_entries)} segment, {summary['warnings_count']} cảnh báo -> dub/outputs/{dub_id}/",
        )
    except Exception as e:
        print(f"[video_job:{job_id}] LỖI: {e!r}")
        print(traceback.format_exc())
        err = str(e) if str(e) else repr(e)
        await lib.update_video(video_id, dub_status="failed", dub_error=format_job_error(e))
        await job_manager.finish_fail(job_id, f"Dub thất bại: {err}")