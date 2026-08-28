import os
import re
import sys
import csv
import asyncio
import time
import io
import shutil
import json
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi import UploadFile, File
from pydantic import BaseModel
from pydub import AudioSegment

import aiohttp
import aiofiles
from huggingface_hub import HfApi

from config import OUTPUT_DIR, PIPER_DIR, MAX_TEXT_LENGTH
from tts_quality_checker import evaluate_segment_quality
from tts_engine import (
    PiperEngine, TaskManager,
    chunk_text_sentences, merge_audio_segments,
    normalize_vietnamese, invalidate_normalizer_cache, clean_text,
    _chunk_hybrid, get_chunk_config,
)


def _worker_count(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


PIPER_EXECUTOR = ThreadPoolExecutor(max_workers=_worker_count("PIPER_WORKERS", min(4, os.cpu_count() or 1)))
AUDIO_IO_EXECUTOR = ThreadPoolExecutor(max_workers=_worker_count("AUDIO_IO_WORKERS", 2))


def _synthesize_one(piper_engine, text: str, voice_id: str, speed: float = 1.0, normalize_audio: bool = True):
    audio = piper_engine.synthesize(text, voice_id, speed=speed)
    if normalize_audio:
        target = -20
        if audio.dBFS != float('-inf'):
            change = target - audio.dBFS
            audio = audio.apply_gain(change)
    return audio


PAUSE_FILE = Path(__file__).resolve().parent / "custom_dict" / "_pause.json"
PAUSE_DEFAULTS = {"enabled": True, "pauses": {".": 0.4, ",": 0.2, ";": 0.3, ":": 0.3, "?": 0.4, "!": 0.4, "linebreak": 0.6}}


def _merge_pause_config(cfg: dict) -> dict:
    enabled = bool(cfg.get("enabled", True))
    pauses = dict(PAUSE_DEFAULTS["pauses"])
    pauses.update(cfg.get("pauses", {}))
    valid_keys = {".", ",", ";", ":", "?", "!", "linebreak"}
    validated_pauses = {k: max(0, float(v)) for k, v in pauses.items() if k in valid_keys}
    return {"enabled": enabled, "pauses": validated_pauses}


def _load_pause_config() -> dict:
    if PAUSE_FILE.exists():
        try:
            cfg = json.loads(PAUSE_FILE.read_text(encoding="utf-8"))
            if isinstance(cfg, dict) and "pauses" in cfg:
                pauses = cfg["pauses"]
                for k, v in PAUSE_DEFAULTS["pauses"].items():
                    if k not in pauses:
                        pauses[k] = v
                return _merge_pause_config(cfg)
        except Exception:
            pass
    return dict(PAUSE_DEFAULTS)


def _save_pause_config(cfg: dict):
    PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    merged = _merge_pause_config(cfg)
    PAUSE_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")


CUSTOM_PAUSE_RE = re.compile(r'\[(\d+(?:\.\d+)?)\s*s\]', re.IGNORECASE)

def normalize_with_pause_protection(text: str) -> str:
    parts = CUSTOM_PAUSE_RE.split(text)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            if part.strip():
                result.append(normalize_vietnamese(part))
            else:
                result.append(part)
        else:
            result.append(f'[{part}s]')
    return ''.join(result)


def synthesize_with_pauses(piper_engine, text: str, voice_id: str, pause_cfg: dict, speed: float = 1.0, normalize_audio: bool = True):
    def _synth(t, vid):
        return _synthesize_one(piper_engine, t, vid, speed=speed, normalize_audio=normalize_audio)

    marker_parts = CUSTOM_PAUSE_RE.split(text)
    result = AudioSegment.silent(duration=0)
    for mi, mp in enumerate(marker_parts):
        if mi % 2 == 0:
            t = mp.strip()
            if not t:
                continue
            pauses = pause_cfg.get("pauses", {})
            _PAUSE_CHARS = {".", ",", ";", ":", "?", "!"}
            chars = "".join(re.escape(c) for c in pauses if c in _PAUSE_CHARS and pauses.get(c, 0) > 0)
            pause_re = re.compile(f"([{chars}])") if chars and pause_cfg.get("enabled", True) else None
            if not pause_re:
                result += _synth(t, voice_id)
            else:
                parts = [p for p in pause_re.split(t) if p.strip()]
                if not parts:
                    result += _synth(t, voice_id)
                else:
                    merged = []
                    for p in parts:
                        if p in pauses and merged and merged[-1] in pauses and merged[-1] == p:
                            continue
                        merged.append(p)
                    j = 0
                    while j < len(merged):
                        part = merged[j]
                        if part in pauses and pauses[part] > 0:
                            result += AudioSegment.silent(duration=int(pauses[part] * 1000))
                            j += 1
                            continue
                        if part.strip():
                            result += _synth(part, voice_id)
                        j += 1
                        if j < len(merged) and merged[j] in pauses and pauses[merged[j]] > 0:
                            result += AudioSegment.silent(duration=int(pauses[merged[j]] * 1000))
                            j += 1
        else:
            try:
                result += AudioSegment.silent(duration=int(float(mp) * 1000))
            except ValueError:
                pass
    if len(result) == 0:
        return _synth(text.strip() or " ", voice_id)
    return result


piper_engine = PiperEngine()
task_manager = TaskManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="CapCap TTS (CPU)", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def task_dir(task_id: str) -> Path:
    d = OUTPUT_DIR / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


class TTSRequest(BaseModel):
    text: str
    voice_mode: str = "low"
    voice_id: str = "banmai"
    output_format: str = "mp3"
    normalize: bool = False
    clean: bool = False
    normalize_audio: bool = True
    speed: float = 1.0
    split_segments: bool = True
    split_mode: str = "default"


class ChunkRegenRequest(BaseModel):
    task_id: str
    chunk_index: int
    text: str | None = None
    voice_id: str | None = None


class MergeRequest(BaseModel):
    task_id: str
    output_format: str = "mp3"


class PauseConfigBody(BaseModel):
    config: dict


class DictEntry(BaseModel):
    key: str
    value: str


@app.get("/tts/voices")
async def list_voices():
    preset = piper_engine.list_voices(include_rate=True)
    return {"low": preset, "medium": [], "high": []}


@app.get("/tts/voice_audio/{engine}/{voice_id}")
async def voice_audio(engine: str, voice_id: str):
    raise HTTPException(400, "Voice preview only available for Piper voices")


@app.post("/tts/preview")
async def preview_tts(req: TTSRequest):
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "Text is empty")

    if req.clean:
        text = clean_text(text)
    if req.normalize:
        text = normalize_with_pause_protection(text)

    preview_text = text[:100]
    sentences = chunk_text_sentences(preview_text)
    preview_text = sentences[0] if sentences else preview_text[:80]

    voice_id = req.voice_id or "banmai"

    try:
        pause_cfg = _load_pause_config()
        loop = asyncio.get_running_loop()
        def _do():
            return synthesize_with_pauses(piper_engine, preview_text, voice_id, pause_cfg, speed=req.speed)
        audio = await loop.run_in_executor(PIPER_EXECUTOR, _do)
    except ValueError as e:
        print(f"[tts:preview] LỖI voice: {e}")
        raise HTTPException(400, str(e))

    preview_dir = OUTPUT_DIR / "_preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_filename = f"preview_{int(time.time())}.wav"
    preview_path = preview_dir / preview_filename
    audio.export(str(preview_path), format="wav")
    return {
        "audio_url": f"/tts/download_file?path=_preview/{preview_filename}",
        "duration": round(len(audio) / 1000, 2),
    }


@app.post("/tts/generate")
async def generate_tts(req: TTSRequest):
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "Text is empty")
    if len(text) > MAX_TEXT_LENGTH:
        raise HTTPException(400, f"Text exceeds {MAX_TEXT_LENGTH} characters")
    if req.voice_id and not piper_engine.has_voice(req.voice_id):
        msg = f"Voice '{req.voice_id}' not found — hãy chọn voice đã cài (xem /tts/voices) hoặc tải voice trong Resources tab."
        print(f"[tts:generate] LỖI voice: {msg}")
        raise HTTPException(400, msg)

    task_id = await task_manager.create(
        text=text, voice_mode=req.voice_mode, voice_id=req.voice_id,
        output_format=req.output_format or "mp3", normalize=req.normalize,
        clean=req.clean, normalize_audio=req.normalize_audio, speed=req.speed,
        split_segments=req.split_segments, split_mode=req.split_mode if req.split_segments else "default",
    )

    asyncio.create_task(_run_generation(task_id))
    return {"task_id": task_id}


async def _run_generation(task_id: str):
    task = await task_manager.get(task_id)
    if not task:
        return
    text = task["text"]
    voice_id = task["voice_id"]
    output_format = task["output_format"]
    do_normalize = task.get("normalize")
    do_clean = task.get("clean")
    split_mode = task.get("split_mode", "default")

    try:
        await task_manager.update(task_id, status="processing", progress=0, stage="splitting")
        if do_clean:
            text = clean_text(text)

        cfg = get_chunk_config("low")
        if split_mode == "sentence":
            chunks = _chunk_hybrid(text, min_chars=10, target_chars=10, max_chars=cfg["hard_max"], hard_max_chars=cfg["hard_max"])
            is_new_para = [False] * len(chunks)
        elif split_mode == "paragraph":
            chunks = _chunk_hybrid(text, min_chars=cfg["min_chars"], target_chars=cfg["max_chars"], max_chars=cfg["hard_max"], hard_max_chars=cfg["hard_max"])
            is_new_para = [True] * len(chunks)
        else:
            chunks = _chunk_hybrid(text, min_chars=cfg["min_chars"], target_chars=cfg["target_chars"], max_chars=cfg["max_chars"], hard_max_chars=cfg["hard_max"])
            is_new_para = []
            raw_paragraphs = re.split(r'\n\s*\n', text.strip())
            for p in raw_paragraphs:
                p = p.strip()
                if not p:
                    continue
                sents = [s.strip() for s in re.split(r'(?<=[.!?])\s+', p) if s.strip()]
                if sents:
                    is_new_para.append(True)
                    is_new_para.extend([False] * (len(sents) - 1))
            is_new_para = is_new_para[:len(chunks)] if len(is_new_para) >= len(chunks) else is_new_para + [False] * (len(chunks) - len(is_new_para))

        if not chunks:
            await task_manager.update(task_id, status="error", error="No text to process")
            return

        if do_normalize:
            gen_texts = [normalize_with_pause_protection(c) for c in chunks]
        else:
            gen_texts = list(chunks)

        voice_id = task["voice_id"]

        chunks_data = []
        for i in range(len(chunks)):
            chunks_data.append({
                "index": i, "text": chunks[i], "gen_text": gen_texts[i],
                "new_paragraph": is_new_para[i] if i < len(is_new_para) else False,
                "status": "pending", "audio_path": None, "error": None,
                "voice_id": voice_id,
            })
        orig_texts = chunks
        await task_manager.set_chunks(task_id, chunks_data)

        await task_manager.update(task_id, status="processing", progress=5, stage="generating")
        loop = asyncio.get_running_loop()
        pause_cfg = _load_pause_config()

        spd = task.get("speed", 1.0)
        norm_audio = task.get("normalize_audio", True)

        def _synth_one(i: int):
            seg = synthesize_with_pauses(piper_engine, gen_texts[i], voice_id, pause_cfg,
                speed=spd, normalize_audio=norm_audio)
            lb_pause = pause_cfg.get("pauses", {}).get("linebreak", 0)
            if i > 0 and chunks_data[i].get("new_paragraph") and lb_pause > 0:
                seg = AudioSegment.silent(duration=int(lb_pause * 1000)) + seg
            chunk_filename = f"chunk_{i}.wav"
            seg.export(str(task_dir(task_id) / chunk_filename), format="wav")
            quality = evaluate_segment_quality(orig_texts[i], None, None, seg)
            return i, chunk_filename, round(len(seg) / 1000, 3), quality

        for i in range(len(orig_texts)):
            await task_manager.update_chunk(task_id, i, status="processing")
        tasks = [loop.run_in_executor(PIPER_EXECUTOR, _synth_one, i) for i in range(len(orig_texts))]
        for coro in asyncio.as_completed(tasks):
            i, chunk_filename, chunk_dur, quality = await coro
            await task_manager.set_chunk_audio_with_quality(task_id, i,
                f"/tts/download_file?path={task_id}/{chunk_filename}", duration=chunk_dur, quality=quality)
            await task_manager.recalc_progress(task_id)

        await _save_segments_meta(task_id)
        await task_manager.update(task_id, status="chunks_done", progress=85, stage="chunks_done")

    except Exception as e:
        print(f"[tts:{task_id}] LỖI: {e!r}")
        print(traceback.format_exc())
        td = task_dir(task_id)
        if td.exists():
            for f in list(td.iterdir()):
                if f.name.startswith("chunk_"):
                    f.unlink()
        await task_manager.update(task_id, status="error", error=str(e) or repr(e))


@app.get("/tts/status/{task_id}")
async def get_status(task_id: str):
    task = await task_manager.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return {
        "task_id": task["task_id"], "status": task["status"],
        "progress": task["progress"], "stage": task["stage"],
        "audio_url": task.get("audio_url"), "duration": task.get("duration"),
        "error": task.get("error"),
        "chunks": [
            {
                "index": c["index"], "text": c["text"],
                "gen_text": c.get("gen_text", c["text"]),
                "status": c["status"], "audio_url": c["audio_path"],
                "duration": c.get("duration", 0), "error": c.get("error"),
                "warning": c.get("warning", False),
                "issues": c.get("issues", []),
                "can_export": c.get("can_export", True),
                "should_recommend_retry": c.get("should_recommend_retry", False),
                "voice_id": c.get("voice_id", task.get("voice_id")),
            }
            for c in task.get("chunks", [])
        ],
    }


@app.post("/tts/regenerate_chunk")
async def regenerate_chunk(req: ChunkRegenRequest):
    task = await task_manager.get(req.task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if req.chunk_index < 0 or req.chunk_index >= len(task["chunks"]):
        raise HTTPException(400, "Invalid chunk index")

    chunk_text = req.text if req.text else task["chunks"][req.chunk_index]["text"]
    chunk_gen_text = chunk_text
    if task.get("normalize"):
        chunk_gen_text = normalize_with_pause_protection(chunk_text)
    voice_id = req.voice_id or task["voice_id"]
    if not piper_engine.has_voice(voice_id):
        raise HTTPException(400, f"Voice '{voice_id}' not found — hãy chọn voice đã cài hoặc tải voice trong Resources tab.")

    await task_manager.update_chunk(req.task_id, req.chunk_index, status="processing", text=chunk_text, gen_text=chunk_gen_text, voice_id=voice_id)

    try:
        pause_cfg = _load_pause_config()
        spd = task.get("speed", 1.0)
        na = task.get("normalize_audio", True)
        loop = asyncio.get_running_loop()
        def _do(t=chunk_gen_text, vid=voice_id, pc=pause_cfg, s=spd, n_audio=na):
            return synthesize_with_pauses(piper_engine, t, vid, pc, speed=s, normalize_audio=n_audio)
        seg = await loop.run_in_executor(PIPER_EXECUTOR, _do)

        chunk_filename = f"chunk_{req.chunk_index}.wav"
        chunk_path = task_dir(req.task_id) / chunk_filename
        seg.export(str(chunk_path), format="wav")
        chunk_dur = round(len(seg) / 1000, 3)
        quality = await loop.run_in_executor(None, evaluate_segment_quality, chunk_text, None, None, seg)
        await task_manager.set_chunk_audio_with_quality(req.task_id, req.chunk_index, f"/tts/download_file?path={req.task_id}/{chunk_filename}", duration=chunk_dur, quality=quality)
        await task_manager.recalc_progress(req.task_id)

        qs = quality["status"]
        return {"chunk_index": req.chunk_index, "status": "done" if qs != "failed" else "error", "audio_url": f"/tts/download_file?path={req.task_id}/{chunk_filename}", "quality_status": qs, "issues": quality["issues"], "voice_id": voice_id}
    except Exception as e:
        await task_manager.set_chunk_error(req.task_id, req.chunk_index, str(e))
        raise HTTPException(500, str(e))


@app.post("/tts/merge")
async def merge_chunks(req: MergeRequest):
    task = await task_manager.get(req.task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    chunks = task["chunks"]
    if not chunks:
        raise HTTPException(400, "No chunks to merge")

    for c in chunks:
        if c["status"] != "done":
            raise HTTPException(400, f"Chunk {c['index']} is not done yet")
        if not c.get("can_export", True):
            raise HTTPException(400, f"Chunk {c['index']} failed quality check and cannot be exported")

    output_format = req.output_format or "mp3"
    loop = asyncio.get_running_loop()
    td = task_dir(req.task_id)
    total_dur = 0

    chunk_paths = []
    for c in chunks:
        path = c["audio_path"].split("?path=")[-1]
        chunk_rel = path.split("/", 1)[-1] if "/" in path else path
        chunk_paths.append(str(td / chunk_rel))
    segments = await asyncio.gather(*[
        loop.run_in_executor(AUDIO_IO_EXECUTOR, AudioSegment.from_file, path) for path in chunk_paths
    ])

    srt_lines = []
    for idx, (c, seg) in enumerate(zip(chunks, segments)):
        chunk_dur = c.get("duration", round(len(seg) / 1000, 3))

        start_ms = int(total_dur * 1000)
        end_ms = int((total_dur + chunk_dur) * 1000)
        def _ms2srt(ms):
            h, rem = divmod(ms, 3600000)
            m, rem = divmod(rem, 60000)
            s, ms = divmod(rem, 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
        txt = c["text"].strip().replace("\n", " ")
        srt_lines.append(f"{idx + 1}")
        srt_lines.append(f"{_ms2srt(start_ms)} --> {_ms2srt(end_ms)}")
        srt_lines.append(txt)
        srt_lines.append("")
        total_dur += chunk_dur

    merged = await loop.run_in_executor(AUDIO_IO_EXECUTOR, merge_audio_segments, segments)
    output_filename = f"final.{output_format}"
    output_path = td / output_filename
    def _write_outputs():
        merged.export(str(output_path), format=output_format)
        (td / "final.srt").write_text("\n".join(srt_lines), encoding="utf-8")
    await loop.run_in_executor(AUDIO_IO_EXECUTOR, _write_outputs)

    duration = round(len(merged) / 1000, 2)

    await task_manager.update(
        req.task_id, status="done", progress=100, stage="done",
        audio_url=f"/tts/download_file?path={req.task_id}/{output_filename}",
        duration=duration, done_at=asyncio.get_running_loop().time(),
    )

    await _save_history(req.task_id)

    return {"audio_url": f"/tts/download_file?path={req.task_id}/{output_filename}", "duration": duration}


# ─── Segment metadata (for on-the-fly SRT generation) ───

async def _save_segments_meta(task_id: str):
    import json
    td = task_dir(task_id)
    task = await task_manager.get(task_id)
    if task and task.get("chunks"):
        segments = [{"text": c.get("gen_text", c.get("text", "")), "duration": c.get("duration", 0)} for c in task["chunks"]]
        (td / "segments.json").write_text(json.dumps(segments, ensure_ascii=False), encoding="utf-8")

def _load_segments_meta(task_id: str) -> list[dict]:
    import json
    meta_path = task_dir(task_id) / "segments.json"
    if not meta_path.exists():
        return []
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return []


@app.get("/tts/download_file")
async def download_file(path: str = Query(...), format: str = Query("")):
    file_path = OUTPUT_DIR / path
    if not file_path.exists():
        raise HTTPException(404, "File not found")

    target_fmt = format.lower() if format else ""
    if target_fmt == "srt":
        task_id = path.split("/")[0]
        srt_path = OUTPUT_DIR / task_id / "final.srt"
        if srt_path.exists():
            return FileResponse(str(srt_path), media_type="text/plain", headers={"Content-Disposition": 'attachment; filename="subtitles.srt"'})
        segs = _load_segments_meta(task_id)
        if segs:
            srt_lines = []
            cum_ms = 0
            for idx, s in enumerate(segs):
                dur_ms = int(s.get("duration", 0) * 1000)
                if dur_ms <= 0:
                    continue
                def _ms2srt(ms):
                    h, rem = divmod(ms, 3600000)
                    m, rem = divmod(rem, 60000)
                    s, ms = divmod(rem, 1000)
                    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
                srt_lines.append(str(idx + 1))
                srt_lines.append(f"{_ms2srt(cum_ms)} --> {_ms2srt(cum_ms + dur_ms)}")
                srt_lines.append(s.get("text", f"Segment {idx + 1}"))
                srt_lines.append("")
                cum_ms += dur_ms
            content = "\n".join(srt_lines)
            return Response(content=content, media_type="text/plain", headers={"Content-Disposition": 'attachment; filename="subtitles.srt"'})
        raise HTTPException(404, "SRT file not found")

    src_fmt = file_path.suffix.lstrip(".")
    if not target_fmt or (target_fmt == "mp3_320" and src_fmt == "mp3"):
        # Serve original file (already 320k or no conversion needed)
        media_type = "audio/mpeg" if src_fmt == "mp3" else "audio/wav"
        return FileResponse(str(file_path), media_type=media_type)

    # Format conversion
    if target_fmt not in ("mp3", "mp3_320", "wav"):
        target_fmt = "mp3"
    loop = asyncio.get_running_loop()
    audio = await loop.run_in_executor(None, AudioSegment.from_file, str(file_path))
    buf = io.BytesIO()
    if target_fmt == "mp3_320":
        await loop.run_in_executor(None, lambda: audio.export(buf, format="mp3", bitrate="320k"))
    elif target_fmt == "mp3":
        await loop.run_in_executor(None, lambda: audio.export(buf, format="mp3", bitrate="128k"))
    else:
        await loop.run_in_executor(None, lambda: audio.export(buf, format=target_fmt))
    buf.seek(0)
    media_type = "audio/mpeg" if target_fmt in ("mp3", "mp3_320") else "audio/wav"
    return Response(content=buf.read(), media_type=media_type)


@app.post("/tts/reset/{task_id}")
async def reset_task(task_id: str):
    task = await task_manager.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    td = task_dir(task_id)
    if td.exists():
        preserved = {}
        for name in ("final.mp3", "final.wav", "final.srt"):
            fp = td / name
            if fp.exists():
                preserved[name] = fp.read_bytes()
        for f in td.iterdir():
            if f.name.startswith("chunk_"):
                f.unlink()
    await task_manager.reset(task_id)
    return {"status": "reset", "preserved_files": list(preserved.keys())}


# ─── Resource Download ───

_hf_api = HfApi()
_dl_state = {}
_dl_lock = asyncio.Lock()
HF_URL = "https://huggingface.co/{repo}/resolve/main/{path}"

_PIPER_VOICES = [
    "banmai", "chieuthanh", "cuc", "duyoryx3175", "lacphi",
    "maiphuong", "manhdung", "minhkhang", "minhquang",
    "ngochuyen", "ngochuyennew", "phuongtrang", "taian2",
    "vi_VN-vais1000-medium",
]

_RESOURCE_DEFS = [
    {
        "id": "piper",
        "label": "Piper Voices (Low · CPU)",
        "repo_id": "Hacht/CapCapResource",
        "files": ["piper/voices.json"] + [f"piper/{v}{e}" for v in _PIPER_VOICES for e in [".onnx", ".onnx.json"]],
        "local_dir": str(PIPER_DIR),
    },
]

def _local_path(local_dir: Path, repo_path: str) -> Path:
    parts = Path(repo_path).parts
    if parts[0] in ("piper", "f5_voice"):
        return local_dir / Path(*parts[1:])
    return local_dir / Path(repo_path)

async def _get_file_sizes(repo_id, paths):
    loop = asyncio.get_running_loop()
    def _fetch():
        try:
            infos = _hf_api.get_paths_info(repo_id, paths)
            return {i.path: i.size for i in infos if i}
        except Exception:
            return {}
    return await loop.run_in_executor(None, _fetch)

async def _build_catalog():
    result = []
    for rdef in _RESOURCE_DEFS:
        rid = rdef["id"]
        sizes = await _get_file_sizes(rdef["repo_id"], rdef["files"])
        total_size = sum(sizes.values())
        size_mb = total_size / (1024 * 1024)
        local_dir = Path(rdef["local_dir"])
        existing = sum(1 for fp in rdef["files"] if _local_path(local_dir, fp).exists())
        async with _dl_lock:
            dl_info = _dl_state.get(rid, {"status":"none","progress":0,"current_file":"","error":""})
        result.append({
            "id": rid, "label": rdef["label"], "desc": "",
            "total_files": len(rdef["files"]), "existing_files": existing,
            "total_size_mb": round(size_mb, 1),
            "downloaded": existing == len(rdef["files"]),
            "status": dl_info["status"], "progress": dl_info["progress"],
            "current_file": dl_info["current_file"], "error": dl_info.get("error",""),
        })
    return result

async def _download_resource(rid: str):
    rdef = next((r for r in _RESOURCE_DEFS if r["id"] == rid), None)
    if not rdef:
        return
    local_dir = Path(rdef["local_dir"])
    repo_id = rdef["repo_id"]
    files = rdef["files"]
    sizes = await _get_file_sizes(repo_id, files)
    total_bytes = sum(sizes.values())
    downloaded_bytes = sum(sizes.get(fp,0) for fp in files if _local_path(local_dir, fp).exists())
    async with _dl_lock:
        _dl_state[rid] = {"status":"downloading","progress":0,"current_file":"","error":""}
    try:
        for fp in files:
            target = _local_path(local_dir, fp)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            url = HF_URL.format(repo=repo_id, path=fp)
            async with _dl_lock:
                _dl_state[rid]["current_file"] = fp
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    async with aiofiles.open(str(target), "wb") as f:
                        while True:
                            chunk = await resp.content.read(65536)
                            if not chunk:
                                break
                            await f.write(chunk)
                            if total_bytes > 0:
                                async with _dl_lock:
                                    _dl_state[rid]["progress"] = int(downloaded_bytes * 100 / total_bytes)
        async with _dl_lock:
            _dl_state[rid] = {"status":"done","progress":100,"current_file":"","error":""}
    except Exception as e:
        async with _dl_lock:
            _dl_state[rid] = {"status":"error","progress":0,"current_file":"","error":str(e)}

@app.get("/tts/resource_catalog")
async def resource_catalog():
    return await _build_catalog()

class StartDownloadRequest(BaseModel):
    resource_id: str

@app.post("/tts/start_download")
async def start_download(req: StartDownloadRequest):
    rid = req.resource_id
    rdef = next((r for r in _RESOURCE_DEFS if r["id"] == rid), None)
    if not rdef:
        raise HTTPException(400, f"Unknown resource: {rid}")
    async with _dl_lock:
        if _dl_state.get(rid, {}).get("status") == "downloading":
            return {"status":"already_downloading"}
    asyncio.create_task(_download_resource(rid))
    return {"status":"started"}

@app.get("/tts/download_progress")
async def download_progress():
    async with _dl_lock:
        return dict(_dl_state)


# ─── Model Status (CPU stub — GPU models not available) ───

@app.get("/tts/model_status")
async def model_status():
    return {
        "gpu": {"available": False, "name": "", "vram_gb": 0, "cuda_version": ""},
        "recommended_quality": ["low"],
        "f5": {"loaded":False,"loading":False,"progress":0,"message":"GPU required","error":True},
        "omnivoice": {"loaded":False,"loading":False,"progress":0,"message":"GPU required","error":True},
    }

class LoadModelRequest(BaseModel):
    model: str

@app.post("/tts/load_model")
async def load_model(req: LoadModelRequest):
    print(f"[tts:load_model] body={{model: {req.model!r}}} — backend_cpu (CPU) không hỗ trợ model GPU (F5-TTS/OmniVoice), chỉ Piper.")
    raise HTTPException(
        400,
        f"Model '{req.model}' không khả dụng trên backend CPU này — chỉ hỗ trợ Piper (tab Voices, có trong /tts/voices). "
        "Để dùng F5-TTS/OmniVoice cần chạy bản backend/ (GPU version).",
    )


# Dictionary endpoints
CUSTOM_DICT_DIR = Path(__file__).resolve().parent / "custom_dict"
CUSTOM_DICT_DIR.mkdir(parents=True, exist_ok=True)


def _read_csv(filename: str) -> list[dict]:
    path = CUSTOM_DICT_DIR / filename
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(filename: str, fieldnames: list[str], rows: list[dict]):
    path = CUSTOM_DICT_DIR / filename
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def _get_csv_fieldnames(filename: str) -> list[str]:
    if filename == "acronyms.csv":
        return ["acronym", "transliteration"]
    return ["original", "transliteration"]


def _key_col(filename: str) -> str:
    return "acronym" if filename == "acronyms.csv" else "original"


async def _list_dict(filename: str) -> list[dict]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _read_csv, filename)


async def _save_dict(filename: str, key: str, value: str) -> list[dict]:
    loop = asyncio.get_running_loop()
    key_col = _key_col(filename)
    val_col = "transliteration"

    def _do():
        rows = _read_csv(filename)
        existing = None
        for r in rows:
            if r.get(key_col, "").strip().lower() == key.strip().lower():
                existing = r
                break
        if existing:
            existing[val_col] = value.strip()
        else:
            rows.append({key_col: key.strip(), val_col: value.strip()})
        _write_csv(filename, _get_csv_fieldnames(filename), rows)
        return rows

    rows = await loop.run_in_executor(None, _do)
    invalidate_normalizer_cache()
    return rows


async def _delete_dict(filename: str, key: str) -> list[dict]:
    loop = asyncio.get_running_loop()
    key_col = _key_col(filename)

    def _do():
        rows = _read_csv(filename)
        rows = [r for r in rows if r.get(key_col, "").strip().lower() != key.strip().lower()]
        _write_csv(filename, _get_csv_fieldnames(filename), rows)
        return rows

    rows = await loop.run_in_executor(None, _do)
    invalidate_normalizer_cache()
    return rows


@app.get("/tts/dict/acronyms")
async def get_acronyms():
    rows = await _list_dict("acronyms.csv")
    return {"entries": [{"key": r["acronym"], "value": r["transliteration"]} for r in rows]}


@app.post("/tts/dict/acronyms")
async def save_acronym(entry: DictEntry):
    rows = await _save_dict("acronyms.csv", entry.key, entry.value)
    return {"entries": [{"key": r["acronym"], "value": r["transliteration"]} for r in rows]}


@app.delete("/tts/dict/acronyms")
async def delete_acronym(key: str = Query(...)):
    rows = await _delete_dict("acronyms.csv", key)
    return {"entries": [{"key": r["acronym"], "value": r["transliteration"]} for r in rows]}


@app.get("/tts/dict/words")
async def get_words():
    rows = await _list_dict("non-vietnamese-words.csv")
    return {"entries": [{"key": r["original"], "value": r["transliteration"]} for r in rows]}


@app.post("/tts/dict/words")
async def save_word(entry: DictEntry):
    rows = await _save_dict("non-vietnamese-words.csv", entry.key, entry.value)
    return {"entries": [{"key": r["original"], "value": r["transliteration"]} for r in rows]}


@app.delete("/tts/dict/words")
async def delete_word(key: str = Query(...)):
    rows = await _delete_dict("non-vietnamese-words.csv", key)
    return {"entries": [{"key": r["original"], "value": r["transliteration"]} for r in rows]}


@app.get("/tts/pause_config")
async def get_pause_config():
    return _load_pause_config()


@app.post("/tts/pause_config")
async def save_pause_config(body: PauseConfigBody):
    _save_pause_config(body.config)
    return _load_pause_config()


# History
HISTORY_FILE = Path(__file__).resolve().parent / "custom_dict" / "_history.json"
MAX_HISTORY = 30


async def _save_history(task_id: str):
    task = await task_manager.get(task_id)
    if not task or task.get("status") != "done":
        return
    import time as _time
    entry = {
        "id": task_id, "timestamp": _time.time(),
        "text": task["text"][:200], "voice_mode": task["voice_mode"],
        "voice_id": task["voice_id"], "audio_url": task.get("audio_url", ""),
        "duration": task.get("duration", 0), "output_format": task.get("output_format", "mp3"),
    }
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    history = []
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    history = [h for h in history if h.get("id") != task_id]
    history.insert(0, entry)
    history = history[:MAX_HISTORY]
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/tts/history")
async def get_history():
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


@app.delete("/tts/history/{history_id}")
async def delete_history(history_id: str):
    history = []
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    history = [h for h in history if h.get("id") != history_id]
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "deleted"}


@app.delete("/tts/history")
async def clear_history():
    HISTORY_FILE.write_text("[]", encoding="utf-8")
    return {"status": "cleared"}


# ─── Video Library API (MVP-1: Douyin) ───
# Đăng ký TRƯỚC static mount để không bị catch-all nuốt route.

import video_library as video_lib
from video_jobs import job_manager
from video_downloader import validate_url, run_download_job, URLError
from video_url_parser import parse_video_url, MAX_DOWNLOAD_ITEMS
from video_transcriber import run_transcribe_job
from video_translator import run_translate_job
from video_dubber import (
    run_dub_job, is_valid_dub_id, dub_output_dir, list_dub_outputs, dub_plan_summary,
)
from video_composer import (
    run_compose_job, validate_compose_options, is_valid_export_id, export_dir,
    list_exports, list_assets, save_logo_asset, _get_asset_path,
    render_preview_frame, is_valid_preview_id, preview_path,
)
from video_segments import merge_segments, apply_edits


class VideoDownloadRequest(BaseModel):
    url: str
    source_type: str = "auto"
    max_items: int = 1
    headless: bool = True
    download_strategy: str = "auto"


class VideoTranscribeRequest(BaseModel):
    model: str = "base"


class VideoTranslateRequest(BaseModel):
    source_lang: str = "auto"
    target_lang: str = "vi"
    provider: str = "google"


class VideoDubRequest(BaseModel):
    audio_mode: str = "mix"
    original_volume: float = 0.05
    dub_volume: float = 1.2
    max_speed: float = 1.25
    burn_subtitles: bool = False
    voice_id: str = "banmai"
    output_name: str | None = None
    normalize_audio: bool = False


class VideoSegmentItem(BaseModel):
    index: int | None = None
    start: float | None = None
    end: float | None = None
    text_original: str = ""
    text_vi: str = ""


class VideoSegmentsRequest(BaseModel):
    segments: list[VideoSegmentItem]


@app.post("/api/videos/download")
async def video_download(req: VideoDownloadRequest):
    try:
        parsed = parse_video_url(req.url, req.max_items)
    except URLError as e:
        raise HTTPException(400, str(e))
    st = (req.source_type or "auto").strip().lower()
    if st == "douyin" and parsed.platform != "douyin":
        raise HTTPException(400, f"source_type=douyin nhưng URL là nền tảng {parsed.platform}.")
    if st in ("youtube", "tiktok", "facebook", "bilibili") and parsed.platform not in (st, "unknown"):
        raise HTTPException(400, f"source_type={st} nhưng URL là nền tảng {parsed.platform}.")
    if st == "ytdlp" and parsed.platform == "douyin":
        raise HTTPException(400, "source_type=ytdlp không dùng cho Douyin (dùng douyin_crawler).")
    strategy = (req.download_strategy or "auto").strip().lower()
    if strategy not in ("auto", "douyin_crawler", "yt_dlp"):
        raise HTTPException(400, f"download_strategy không hợp lệ: {req.download_strategy!r}.")
    if strategy == "douyin_crawler" and parsed.platform != "douyin":
        raise HTTPException(400, f"douyin_crawler chỉ dùng cho link Douyin (URL là {parsed.platform}).")
    if strategy == "yt_dlp" and parsed.platform == "douyin":
        raise HTTPException(400, "yt_dlp không dùng cho link Douyin (dùng douyin_crawler).")
    if req.max_items > MAX_DOWNLOAD_ITEMS:
        raise HTTPException(400, f"max_items tối đa {MAX_DOWNLOAD_ITEMS}.")
    job_id = await job_manager.create("dl", message="Đang khởi tạo job download...")
    asyncio.create_task(
        run_download_job(job_id, req.url, req.source_type, req.max_items,
                         req.headless, download_strategy=strategy, parsed=parsed)
    )
    return {"job_id": job_id, "status": "queued", "parsed": parsed.to_dict()}


@app.get("/api/videos/download/{job_id}")
async def video_download_status(job_id: str):
    job = await job_manager.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "video_ids": job["video_ids"],
        "error": job.get("error", ""),
        "parsed": job.get("parsed"),
        "strategy": job.get("strategy"),
        "diagnostics": job.get("diagnostics"),
    }


@app.get("/api/videos")
async def video_list():
    return await video_lib.list_videos()


@app.get("/api/videos/transcribe/{job_id}")
async def video_transcribe_status(job_id: str):
    job = await job_manager.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "video_ids": job["video_ids"],
        "error": job.get("error", ""),
    }


@app.get("/api/videos/translate/{job_id}")
async def video_translate_status(job_id: str):
    job = await job_manager.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "video_ids": job["video_ids"],
        "error": job.get("error", ""),
    }


@app.get("/api/videos/{video_id}")
async def video_detail(video_id: str):
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    v = await video_lib.get_video(video_id)
    if not v:
        raise HTTPException(404, "Video not found")
    detail = dict(v)
    transcript = video_lib.get_transcript(video_id)
    transcript_vi = video_lib.get_transcript_vi(video_id)
    detail["transcript"] = transcript
    detail["transcript_text"] = (transcript or {}).get("text") or ""
    detail["transcript_path"] = v.get("transcript_path", "")
    detail["transcript_vi"] = transcript_vi
    detail["transcript_vi_text"] = (transcript_vi or {}).get("text") or ""
    detail["transcript_vi_path"] = v.get("transcript_vi_path", "")
    detail["translation_status"] = v.get("translation_status", "none")
    return detail


@app.get("/api/videos/{video_id}/segments")
async def video_segments_get(video_id: str):
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    v = await video_lib.get_video(video_id)
    if not v:
        raise HTTPException(404, "Video not found")
    return merge_segments(video_id)


@app.put("/api/videos/{video_id}/segments")
async def video_segments_put(video_id: str, req: VideoSegmentsRequest):
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    v = await video_lib.get_video(video_id)
    if not v:
        raise HTTPException(404, "Video not found")
    if not req.segments:
        raise HTTPException(400, "Body segments không được rỗng.")
    items = [s.model_dump() for s in req.segments]
    try:
        merged = apply_edits(video_id, items)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await video_lib.update_video(
        video_id,
        edited=True,
        transcript_text_len=len(merged.get("segments") or []),
        segments_edited_at=video_lib.now_iso(),
    )
    return merged


@app.put("/api/videos/{video_id}/segments/{index}")
async def video_segment_put(video_id: str, index: int, req: VideoSegmentItem):
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    v = await video_lib.get_video(video_id)
    if not v:
        raise HTTPException(404, "Video not found")
    item = req.model_dump()
    item["index"] = index
    try:
        merged = apply_edits(video_id, [item])
    except ValueError as e:
        raise HTTPException(400, str(e))
    await video_lib.update_video(
        video_id,
        edited=True,
        transcript_text_len=len(merged.get("segments") or []),
        segments_edited_at=video_lib.now_iso(),
    )
    return merged


@app.get("/api/videos/{video_id}/media")
async def video_media(video_id: str):
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    media_path = video_lib.video_dir(video_id) / "index.mp4"
    if not media_path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(str(media_path), media_type="video/mp4")


@app.post("/api/videos/{video_id}/transcribe")
async def video_transcribe(video_id: str, req: VideoTranscribeRequest):
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    v = await video_lib.get_video(video_id)
    if not v:
        raise HTTPException(404, "Video not found")
    job_id = await job_manager.create("tr", message="Đang khởi tạo job transcribe...")
    asyncio.create_task(run_transcribe_job(job_id, video_id, req.model or "base"))
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/videos/{video_id}/translate")
async def video_translate(video_id: str, req: VideoTranslateRequest):
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    v = await video_lib.get_video(video_id)
    if not v:
        raise HTTPException(404, "Video not found")
    job_id = await job_manager.create(
        "tl",
        message=f"Đang khởi tạo job dịch ({req.provider} -> {req.target_lang})...",
    )
    asyncio.create_task(
        run_translate_job(job_id, video_id, req.source_lang or "auto", req.target_lang or "vi", req.provider or "google")
    )
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/videos/{video_id}/send-to-tts")
async def video_send_to_tts(video_id: str, variant: str = Query("original", pattern="^(original|vi)$")):
    """Trả text transcript để frontend điền vào textarea TTS (frontend giữ state, server không mutate).
    variant=original: bản gốc; variant=vi: bản đã dịch sang tiếng Việt."""
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    v = await video_lib.get_video(video_id)
    if not v:
        raise HTTPException(404, "Video not found")
    if variant == "vi":
        t = video_lib.get_transcript_vi(video_id)
        text = ""
        if t and t.get("text"):
            text = t["text"]
        else:
            txt_path = video_lib.video_dir(video_id) / "transcript_vi.txt"
            if txt_path.exists():
                text = txt_path.read_text(encoding="utf-8")
        if not text.strip():
            raise HTTPException(400, "Video chưa có bản dịch tiếng Việt — bấm 'Translate to Vietnamese' trước.")
        return {"text": text, "variant": "vi"}
    t = video_lib.get_transcript(video_id)
    text = ""
    if t and t.get("text"):
        text = t["text"]
    else:
        txt_path = video_lib.video_dir(video_id) / "transcript.txt"
        if txt_path.exists():
            text = txt_path.read_text(encoding="utf-8")
    if not text.strip():
        raise HTTPException(400, "Video chưa có transcript — hãy bấm Transcribe trước.")
    return {"text": text, "variant": "original"}


@app.post("/api/videos/{video_id}/open-folder")
async def video_open_folder(video_id: str):
    """Mở Explorer tại folder video — chỉ khi folder nằm trong data/videos (guard path traversal)."""
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    d = video_lib.video_dir(video_id)
    if not d.exists():
        raise HTTPException(404, "Folder not found")
    try:
        os.startfile(str(d))  # Windows-only
    except AttributeError:
        raise HTTPException(400, "Chức năng này chỉ hỗ trợ Windows")
    except OSError as e:
        raise HTTPException(500, f"Không mở được folder: {e}")
    return {"status": "opened"}


@app.post("/api/videos/{video_id}/dub")
async def video_dub(video_id: str, req: VideoDubRequest):
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    v = await video_lib.get_video(video_id)
    if not v:
        raise HTTPException(404, "Video not found")
    audio_mode = (req.audio_mode or "mix").lower()
    if audio_mode == "duck":
        audio_mode = "mix"
    if audio_mode not in ("mix", "replace", "dub_only", "original_only"):
        raise HTTPException(400, "audio_mode phải là 'mix', 'replace', 'dub_only' hoặc 'original_only'")
    original_volume = float(req.original_volume if req.original_volume is not None else 0.05)
    if original_volume > 1.0:
        original_volume = original_volume / 100.0  # leniency: chấp nhận dạng % (0-100)
    if not (0.0 <= original_volume <= 1.0):
        raise HTTPException(400, "original_volume phải nằm trong 0..1 (hoặc 0..100 dạng %)")
    dub_volume = float(req.dub_volume if req.dub_volume is not None else 1.2)
    if not (0.0 <= dub_volume <= 2.0):
        raise HTTPException(400, "dub_volume phải nằm trong 0..2")
    max_speed = float(req.max_speed or 1.25)
    if not (1.0 <= max_speed <= 1.5):
        raise HTTPException(400, "max_speed phải nằm trong 1.0..1.5")
    job_id = await job_manager.create("db", message="Đang khởi tạo job dub...")
    asyncio.create_task(run_dub_job(
        job_id, video_id,
        audio_mode=audio_mode,
        original_volume=original_volume,
        dub_volume=dub_volume,
        max_speed=max_speed,
        burn_subtitles=bool(req.burn_subtitles),
        voice_id=req.voice_id or "banmai",
        output_name=req.output_name,
        normalize_audio=bool(req.normalize_audio),
    ))
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/videos/dub/{job_id}")
async def video_dub_status(job_id: str):
    job = await job_manager.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "video_ids": job["video_ids"],
        "error": job.get("error", ""),
    }


@app.get("/api/videos/{video_id}/dubbed-media")
async def video_dubbed_media(video_id: str):
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    media_path = video_lib.video_dir(video_id) / "dub" / "output_dubbed.mp4"
    if not media_path.exists():
        raise HTTPException(404, "Chưa có video lồng tiếng — bấm 'Create Dubbed Video' trước.")
    return FileResponse(str(media_path), media_type="video/mp4")


@app.get("/api/videos/{video_id}/dub-plan")
async def video_dub_plan(video_id: str):
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    plan_path = video_lib.video_dir(video_id) / "dub" / "dubbing_plan.json"
    if not plan_path.exists():
        raise HTTPException(404, "Chưa có dubbing plan — bấm 'Create Dubbed Video' trước.")
    return json.loads(plan_path.read_text(encoding="utf-8"))


@app.post("/api/videos/{video_id}/dub-open-folder")
async def video_dub_open_folder(video_id: str):
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    d = video_lib.video_dir(video_id) / "dub"
    if not d.exists():
        raise HTTPException(404, "Chưa có folder dub — hãy tạo video lồng tiếng trước.")
    try:
        os.startfile(str(d))  # Windows-only
    except AttributeError:
        raise HTTPException(400, "Chức năng này chỉ hỗ trợ Windows")
    except OSError as e:
        raise HTTPException(500, f"Không mở được folder: {e}")
    return {"status": "opened"}


# ─── Dub outputs management (MVP-4) ───

@app.get("/api/videos/{video_id}/dubs")
async def video_dubs_list(video_id: str):
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    v = await video_lib.get_video(video_id)
    if not v:
        raise HTTPException(404, "Video not found")
    return list_dub_outputs(video_id)


@app.get("/api/videos/{video_id}/dubs/{dub_id}")
async def video_dub_detail(video_id: str, dub_id: str):
    if not video_lib.is_valid_video_id(video_id) or not is_valid_dub_id(dub_id):
        raise HTTPException(404, "Dub not found")
    d = dub_output_dir(video_id, dub_id)
    meta_file = d / "metadata.json"
    if not meta_file.exists():
        raise HTTPException(404, "Dub not found")
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    plan_file = d / "dubbing_plan.json"
    plan = None
    if plan_file.exists():
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
    return {
        **meta,
        "plan_summary": dub_plan_summary(plan) if plan else None,
    }


@app.get("/api/videos/{video_id}/dubs/{dub_id}/media")
async def video_dub_media(video_id: str, dub_id: str):
    if not video_lib.is_valid_video_id(video_id) or not is_valid_dub_id(dub_id):
        raise HTTPException(404, "Dub not found")
    p = dub_output_dir(video_id, dub_id) / "output.mp4"
    if not p.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(str(p), media_type="video/mp4")


@app.get("/api/videos/{video_id}/dubs/{dub_id}/audio")
async def video_dub_audio(video_id: str, dub_id: str):
    if not video_lib.is_valid_video_id(video_id) or not is_valid_dub_id(dub_id):
        raise HTTPException(404, "Dub not found")
    p = dub_output_dir(video_id, dub_id) / "final_dub.wav"
    if not p.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(str(p), media_type="audio/wav")


@app.get("/api/videos/{video_id}/dubs/{dub_id}/subtitles")
async def video_dub_subtitles(video_id: str, dub_id: str):
    if not video_lib.is_valid_video_id(video_id) or not is_valid_dub_id(dub_id):
        raise HTTPException(404, "Dub not found")
    p = dub_output_dir(video_id, dub_id) / "subtitles_vi.srt"
    if not p.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(str(p), media_type="text/plain; charset=utf-8")


@app.post("/api/videos/{video_id}/dubs/{dub_id}/open-folder")
async def video_dub_open_output_folder(video_id: str, dub_id: str):
    if not video_lib.is_valid_video_id(video_id) or not is_valid_dub_id(dub_id):
        raise HTTPException(404, "Dub not found")
    d = dub_output_dir(video_id, dub_id)
    if not d.exists():
        raise HTTPException(404, "Dub not found")
    try:
        os.startfile(str(d))  # Windows-only — path đã guard nằm trong dub/outputs
    except AttributeError:
        raise HTTPException(400, "Chức năng này chỉ hỗ trợ Windows")
    except OSError as e:
        raise HTTPException(500, f"Không mở được folder: {e}")
    return {"status": "opened"}


@app.delete("/api/videos/{video_id}/dubs/{dub_id}")
async def video_dub_delete(video_id: str, dub_id: str):
    if not video_lib.is_valid_video_id(video_id) or not is_valid_dub_id(dub_id):
        raise HTTPException(404, "Dub not found")
    d = dub_output_dir(video_id, dub_id)
    if not d.exists():
        raise HTTPException(404, "Dub not found")
    shutil.rmtree(d, ignore_errors=True)
    return {"status": "deleted", "video_id": video_id, "dub_id": dub_id}


# ─── Export Composer (MVP-7) ───

class VideoComposeRequest(BaseModel):
    source: str = "original"
    dub_id: str | None = None
    aspect_ratio: str = "original"
    width: int = 1080
    height: int = 1920
    fit_mode: str = "contain"
    background_color: str = "#000000"
    subtitle_enabled: bool = False
    subtitle_position: str = "bottom"
    subtitle_y: float = 88.0
    subtitle_font_size: int = 48
    subtitle_color: str = "#FFFFFF"
    subtitle_outline_color: str = "#000000"
    subtitle_box: bool = False
    subtitle_box_color: str = "#000000"
    subtitle_box_opacity: float = 0.75
    subtitle_margin_bottom: int = 60
    subtitle_max_chars: int = 22
    old_subtitle_mask_enabled: bool = False
    old_subtitle_mask: dict | None = None
    logo_enabled: bool = False
    logo_asset_id: str | None = None
    logo_position: str = "top-right"
    logo_scale: float = 12.0
    logo_opacity: float = 0.9
    logo_margin: int = 20
    logo_x: float = 50.0
    logo_y: float = 50.0
    trim_enabled: bool = False
    trim_start: float = 0.0
    trim_end: float = 0.0
    timestamp: float = 3.0  # chỉ dùng cho compose-preview
    output_name: str | None = None


@app.post("/api/videos/{video_id}/compose")
async def video_compose(video_id: str, req: VideoComposeRequest):
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    v = await video_lib.get_video(video_id)
    if not v:
        raise HTTPException(404, "Video not found")
    opts = req.model_dump()
    _, err = validate_compose_options(opts)
    if err:
        raise HTTPException(400, err)
    job_id = await job_manager.create("cs", message="Đang khởi tạo job compose...")
    asyncio.create_task(run_compose_job(job_id, video_id, opts))
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/videos/compose/{job_id}")
async def video_compose_status(job_id: str):
    job = await job_manager.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "video_ids": job["video_ids"],
        "error": job.get("error", ""),
    }


@app.get("/api/videos/{video_id}/exports")
async def video_exports_list(video_id: str):
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    return list_exports(video_id)


@app.get("/api/videos/{video_id}/exports/{export_id}")
async def video_export_detail(video_id: str, export_id: str):
    if not video_lib.is_valid_video_id(video_id) or not is_valid_export_id(export_id):
        raise HTTPException(404, "Export not found")
    d = export_dir(video_id, export_id)
    meta_file = d / "metadata.json"
    if not meta_file.exists():
        raise HTTPException(404, "Export not found")
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    plan_file = d / "composer_plan.json"
    if plan_file.exists():
        meta["plan"] = json.loads(plan_file.read_text(encoding="utf-8"))
    return meta


@app.get("/api/videos/{video_id}/exports/{export_id}/media")
async def video_export_media(video_id: str, export_id: str):
    if not video_lib.is_valid_video_id(video_id) or not is_valid_export_id(export_id):
        raise HTTPException(404, "Export not found")
    p = export_dir(video_id, export_id) / "output_final.mp4"
    if not p.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(str(p), media_type="video/mp4")


@app.post("/api/videos/{video_id}/exports/{export_id}/open-folder")
async def video_export_open_folder(video_id: str, export_id: str):
    if not video_lib.is_valid_video_id(video_id) or not is_valid_export_id(export_id):
        raise HTTPException(404, "Export not found")
    d = export_dir(video_id, export_id)
    if not d.exists():
        raise HTTPException(404, "Export not found")
    try:
        os.startfile(str(d))  # Windows-only — path đã guard nằm trong exports
    except AttributeError:
        raise HTTPException(400, "Chức năng này chỉ hỗ trợ Windows")
    except OSError as e:
        raise HTTPException(500, f"Không mở được folder: {e}")
    return {"status": "opened"}


@app.delete("/api/videos/{video_id}/exports/{export_id}")
async def video_export_delete(video_id: str, export_id: str):
    if not video_lib.is_valid_video_id(video_id) or not is_valid_export_id(export_id):
        raise HTTPException(404, "Export not found")
    d = export_dir(video_id, export_id)
    if not d.exists():
        raise HTTPException(404, "Export not found")
    shutil.rmtree(d, ignore_errors=True)
    return {"status": "deleted", "video_id": video_id, "export_id": export_id}


@app.get("/api/videos/{video_id}/assets")
async def video_assets_list(video_id: str):
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    return list_assets(video_id)


@app.get("/api/videos/{video_id}/assets/logo/{asset_name}")
async def video_assets_logo_image(video_id: str, asset_name: str):
    """Phuc vu anh logo cho visual editor (chi doc tu assets dir da guard)."""
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    p = _get_asset_path(video_id, asset_name)
    if p is None:
        raise HTTPException(404, "Logo not found")
    ext = p.suffix.lower().lstrip(".")
    media_type = "image/png" if ext == "png" else "image/jpeg" if ext in ("jpg", "jpeg") else "image/webp"
    return FileResponse(str(p), media_type=media_type)


@app.post("/api/videos/{video_id}/compose-preview")
async def video_compose_preview(video_id: str, req: VideoComposeRequest):
    """Render 1 frame (jpg) voi cung filter graph cua compose — dung cho visual editor."""
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    v = await video_lib.get_video(video_id)
    if not v:
        raise HTTPException(404, "Video not found")
    opts = req.model_dump()
    _, err = validate_compose_options(opts)
    if err:
        raise HTTPException(400, err)
    try:
        result = await asyncio.to_thread(render_preview_frame, video_id, opts)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    return result


@app.get("/api/videos/{video_id}/compose-preview/{preview_id}")
async def video_compose_preview_image(video_id: str, preview_id: str):
    if not video_lib.is_valid_video_id(video_id) or not is_valid_preview_id(preview_id):
        raise HTTPException(404, "Preview not found")
    p = preview_path(video_id, preview_id)
    if not p.exists():
        raise HTTPException(404, "Preview not found")
    return FileResponse(str(p), media_type="image/jpeg")


@app.post("/api/videos/{video_id}/assets/logo")
async def video_assets_logo_upload(video_id: str, file: UploadFile = File(...)):
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    try:
        data = await file.read()
        name = save_logo_asset(video_id, file.filename or "", data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"asset_id": name, "name": name, "status": "ok"}


@app.delete("/api/videos/{video_id}")
async def video_delete(video_id: str):
    """Xóa video khỏi library + xóa folder data/videos/<video_id> (chứa file lớn nên xóa vật lý)."""
    if not video_lib.is_valid_video_id(video_id):
        raise HTTPException(404, "Video not found")
    v = await video_lib.get_video(video_id)
    if not v:
        raise HTTPException(404, "Video not found")
    await video_lib.delete_video(video_id, remove_files=True)
    return {"status": "deleted", "id": video_id}


# Serve frontend
# Serve frontend
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    try:
        import uvicorn
        reload_enabled = not getattr(sys, 'frozen', False)
        uvicorn.run(app, host="0.0.0.0", port=8000, reload=reload_enabled)
    except Exception as e:
        import traceback
        with open("error.log", "w") as f:
            traceback.print_exc(file=f)
        print(f"\nFATAL: {e}\nSee error.log for details.")
        os.system("pause")
