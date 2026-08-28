"""
Video segments service (MVP-8.2) — gộp transcript gốc + bản dịch tiếng Việt theo index,
đọc/sửa từng segment, regenerate transcript.txt / transcript_vi.txt / subtitles_vi.srt.

Segment chuẩn:
{
  "index": 0, "start": 0.32, "end": 2.84,
  "text_original": "...", "text_vi": "...",
  "speaker": null,
  "status": "ok|edited|empty|too_long",
  "duration": 2.52
}
"""
import json

import video_library as lib
from video_dubber import build_subtitles_srt

MAX_SEGMENT_TEXT = 5000
MAX_SEGMENTS = 10000
TOO_LONG_RATIO = 1.6  # est_speech_duration / segment_duration vượt ngưỡng -> badge "Dài"


def est_speech_duration(text: str) -> float:
    """Ước lượng thời gian nói tiếng Việt ~3 ký tự/giây."""
    return max(0.0, len(text) / 3.0)


def segment_status(text_vi: str, duration: float, edited: bool) -> str:
    if edited:
        return "edited"
    if not text_vi:
        return "empty"
    if duration > 0 and est_speech_duration(text_vi) / duration > TOO_LONG_RATIO:
        return "too_long"
    return "ok"


def _clean_segments(raw: list | None) -> list[dict]:
    return [s for s in (raw or []) if isinstance(s, dict)]


def merge_segments(video_id: str) -> dict:
    """Gộp transcript.json + transcript_vi.json theo index. Không bao giờ crash khi thiếu file."""
    orig = lib.get_transcript(video_id) or {}
    tvi = lib.get_transcript_vi(video_id) or {}
    orig_segs = _clean_segments(orig.get("segments"))
    vi_segs = _clean_segments(tvi.get("segments"))

    count = max(len(orig_segs), len(vi_segs))
    warning = None
    if orig_segs and vi_segs and len(orig_segs) != len(vi_segs):
        warning = (
            f"Số đoạn gốc ({len(orig_segs)}) khác số đoạn dịch ({len(vi_segs)}) — "
            "đoạn thiếu sẽ để trống bản dịch."
        )

    if not orig_segs:
        # không có transcript gốc -> không hiển thị editor segment
        return {
            "video_id": video_id,
            "segments": [],
            "source": {
                "has_original": False,
                "has_vietnamese": bool(vi_segs),
            },
            "warning": warning,
        }

    out = []
    for i in range(count):
        o = orig_segs[i] if i < len(orig_segs) else {}
        v = vi_segs[i] if i < len(vi_segs) else {}
        try:
            start = float(o.get("start") if o.get("start") is not None else v.get("start") or 0)
        except (TypeError, ValueError):
            start = 0.0
        try:
            end = float(o.get("end") if o.get("end") is not None else v.get("end") or start)
        except (TypeError, ValueError):
            end = start
        if end < start:
            end = start
        text_original = (o.get("text") or "").strip()
        text_vi = (v.get("text") or "").strip()
        edited = bool(v.get("edited")) or bool(o.get("edited"))
        out.append({
            "index": i,
            "start": round(start, 3),
            "end": round(end, 3),
            "text_original": text_original,
            "text_vi": text_vi,
            "speaker": None,
            "status": segment_status(text_vi, max(0.0, end - start), edited),
            "duration": round(max(0.0, end - start), 3),
            "edited": edited,
        })

    return {
        "video_id": video_id,
        "segments": out,
        "source": {
            "has_original": bool(orig_segs),
            "has_vietnamese": bool(vi_segs),
        },
        "warning": warning,
    }


def _num(v, default=0.0) -> float:
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return default


def _validate_item(item: dict, idx: int) -> tuple[str | None, dict]:
    """Trả (error, normalized). start/end/text chỉ áp dụng nếu được cung cấp."""
    norm = dict(item)
    index = idx if idx >= 0 else item.get("index")
    if not isinstance(index, int) or index < 0:
        return f"segments[{idx}]: index phải là số nguyên >= 0", None
    if index >= MAX_SEGMENTS:
        return f"segments[{idx}]: index quá lớn ({MAX_SEGMENTS})", None
    text_original = str(item.get("text_original") or "").strip()
    text_vi = str(item.get("text_vi") or "").strip()
    if len(text_original) > MAX_SEGMENT_TEXT:
        return f"segments[{idx}]: text_original quá dài (tối đa {MAX_SEGMENT_TEXT} ký tự)", None
    if len(text_vi) > MAX_SEGMENT_TEXT:
        return f"segments[{idx}]: text_vi quá dài (tối đa {MAX_SEGMENT_TEXT} ký tự)", None

    start = _num(item.get("start"), 0.0) if item.get("start") is not None else None
    end = _num(item.get("end"), 0.0) if item.get("end") is not None else None
    if start is not None and start < 0:
        return f"segments[{idx}]: start không được âm", None
    if start is not None and end is not None and end <= start:
        return f"segments[{idx}]: end ({end}) phải lớn hơn start ({start})", None
    if start is not None and end is None:
        end = start + 0.05
    if end is not None and end < 0:
        return f"segments[{idx}]: end không được âm", None

    norm["start"] = start
    norm["end"] = end
    return None, norm


def apply_edits(video_id: str, items: list[dict]) -> dict:
    """Áp dụng sửa segment vào transcript.json / transcript_vi.json, regenerate txt + srt. Trả merged mới."""
    orig = lib.get_transcript(video_id)
    if orig is None or not _clean_segments(orig.get("segments")):
        raise ValueError("Video chưa có transcript — hãy nhận dạng lời thoại trước.")
    orig_segs = _clean_segments(orig.get("segments"))

    tvi = lib.get_transcript_vi(video_id)
    vi_segs = _clean_segments(tvi.get("segments") if tvi else None)

    normalized = []
    for idx, item in enumerate(items):
        if isinstance(item.get("index"), int) and item.get("index") >= 0:
            item_idx = item["index"]
        else:
            item_idx = idx
        err, norm = _validate_item(item, item_idx)
        if err:
            raise ValueError(err)
        if norm:
            normalized.append((item_idx, norm))

    if not normalized:
        raise ValueError("Không có segment nào để lưu.")

    max_idx = max(i for i, _ in normalized)

    # transcript.json: giữ nguyên mọi field gốc, chỉ cập nhật segment được sửa
    new_orig_segs = []
    for i in range(max(len(orig_segs), max_idx + 1)):
        seg = dict(orig_segs[i]) if i < len(orig_segs) else {"start": 0.0, "end": 0.0, "text": ""}
        seg.setdefault("start", 0.0)
        seg.setdefault("end", float(seg.get("start") or 0.0))
        seg.setdefault("text", "")
        new_orig_segs.append(seg)
    for seg, item in normalized:
        news = dict(new_orig_segs[seg])
        if item.get("start") is not None:
            news["start"] = item["start"]
            if item.get("end") is None:
                news["end"] = max(news["start"] + 0.05, news.get("end") or news["start"])
        if item.get("end") is not None:
            news["end"] = item["end"]
        news["text"] = item["text_original"]
        new_orig_segs[seg] = news

    new_orig_text = " ".join(s.get("text", "").strip() for s in new_orig_segs if s.get("text", "").strip()).strip()
    orig = dict(orig)
    orig["segments"] = new_orig_segs
    orig["text"] = new_orig_text

    # transcript_vi.json: tạo mới nếu chưa có; "edited=true" khi text thay đổi so với bản lưu
    if tvi is None:
        tvi = {"text": "", "language": "vi", "provider": "manual", "segments": []}
    tvi = dict(tvi)
    cur_vi_segs = _clean_segments(tvi.get("segments"))
    new_vi_segs = []
    for i in range(max(len(cur_vi_segs), max_idx + 1)):
        if i < len(cur_vi_segs):
            seg = dict(cur_vi_segs[i])
        elif i < len(vi_segs):
            seg = dict(vi_segs[i])
        else:
            seg = {"start": 0.0, "end": 0.0, "text": ""}
        seg.setdefault("start", 0.0)
        seg.setdefault("end", float(seg.get("start") or 0.0))
        seg.setdefault("text", "")
        if i < len(orig_segs):
            seg.setdefault("start", _num(orig_segs[i].get("start"), 0.0))
            seg.setdefault("end", _num(orig_segs[i].get("end"), _num(orig_segs[i].get("start"), 0.0)))
        new_vi_segs.append(seg)
    for seg, item in normalized:
        news = dict(new_vi_segs[seg])
        if item.get("start") is not None:
            news["start"] = item["start"]
            if item.get("end") is None:
                news["end"] = max(news["start"] + 0.05, news.get("end") or news["start"])
        if item.get("end") is not None:
            news["end"] = item["end"]
        old_text = str(news.get("text") or "")
        new_text = item["text_vi"]
        if new_text != old_text:
            news["text"] = new_text
            news["edited"] = True
        new_vi_segs[seg] = news

    # provider/edited: giữ dấu sửa 1 lần
    tvi["segments"] = new_vi_segs
    tvi["text"] = " ".join(s.get("text", "").strip() for s in new_vi_segs if s.get("text", "").strip()).strip()

    _write_files(video_id, orig, tvi)
    return merge_segments(video_id)


def _write_files(video_id: str, orig: dict, tvi: dict) -> None:
    """Ghi transcript.json/.txt, transcript_vi.json/.txt, subtitles_vi.srt (root + dub latest nếu có)."""
    d = lib.ensure_video_dir(video_id)
    (d / "transcript.json").write_text(json.dumps(orig, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "transcript.txt").write_text((orig.get("text") or "").strip(), encoding="utf-8")
    (d / "transcript_vi.json").write_text(json.dumps(tvi, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "transcript_vi.txt").write_text((tvi.get("text") or "").strip(), encoding="utf-8")

    vi_segs = _clean_segments(tvi.get("segments"))
    for sub_path in (
        d / "subtitles_vi.srt",
        d / "dub" / "subtitles_vi.srt",
    ):
        if sub_path.exists() or sub_path == d / "subtitles_vi.srt":
            sub_path.parent.mkdir(parents=True, exist_ok=True)
            sub_path.write_text(build_subtitles_srt(vi_segs), encoding="utf-8")