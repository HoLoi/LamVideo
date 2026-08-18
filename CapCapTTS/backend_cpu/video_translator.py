"""
Video translator service — dịch transcript (tiếng Trung/Anh/...) sang tiếng Việt.

Provider mặc định: deep-translator (GoogleTranslate — miễn phí, không cần API key,
chỉ cần Internet). Có thể bổ sung provider khác qua TRANSLATION_PROVIDERS.

Kết quả lưu trong folder video:
  transcript_vi.json  — giữ nguyên segment timings, text đã dịch
  transcript_vi.txt   — text thuần (dùng cho TTS)
"""
import asyncio
import re
import time
import traceback

from video_jobs import job_manager, format_job_error
import video_library as lib

TRANSLATION_PROVIDERS = ("google",)

# Chinese characters (CJK) used for heuristic warnings
CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


class TranslationUnavailableError(RuntimeError):
    """Provider chưa sẵn sàng (thiếu dependency hoặc không được hỗ trợ)."""


def get_translator(provider: str, source_lang: str, target_lang: str):
    """Tạo translator instance cho provider. Raise TranslationUnavailableError khi thiếu dep."""
    if provider == "google":
        try:
            from deep_translator import GoogleTranslator
        except ImportError as e:
            raise TranslationUnavailableError(
                f"Provider '{provider}' chưa sẵn sàng: deep-translator chưa cài. "
                f"Chạy: pip install deep-translator (lỗi: {e})"
            ) from e
        return GoogleTranslator(source=source_lang or "auto", target=target_lang)
    raise TranslationUnavailableError(
        f"Provider không hỗ trợ: {provider!r}. Hỗ trợ: {', '.join(TRANSLATION_PROVIDERS)}."
    )


def _split_long(text: str, max_chars: int = 2000) -> list[str]:
    """Chia văn bản dài thành các chunk theo ranh giới câu (tránh vượt giới hạn provider)."""
    if len(text) <= max_chars:
        return [text]
    parts = re.split(r"(?<=[.!?。！？])", text)
    chunks = []
    cur = ""
    for part in parts:
        if not part:
            continue
        if cur and len(cur) + len(part) > max_chars:
            chunks.append(cur)
            cur = part
        else:
            cur += part
    if cur:
        chunks.append(cur)
    return chunks


async def _translate_unit(translator, unit: str) -> str:
    """Dịch 1 đoạn văn bản với retry (3 lần, backoff). Chạy network trong thread."""
    if not unit.strip():
        return ""
    parts = _split_long(unit)
    translated_parts = []
    for part in parts:
        last_err = None
        for attempt in range(3):
            try:
                translated_parts.append(await asyncio.to_thread(translator.translate, part))
                break
            except Exception as e:
                last_err = e
                await asyncio.sleep(0.6 * (attempt + 1))
        else:
            raise RuntimeError(
                f"Không dịch được đoạn {part[:60]!r} ({type(last_err).__name__}: {last_err})"
            )
    return " ".join(p for p in translated_parts if p)


def has_significant_chinese(text: str, ratio: float = 0.3) -> bool:
    """Heuristic: văn bản chứa nhiều ký tự Trung (dùng cho cảnh báo UI, không chặn)."""
    text = (text or "").strip()
    if not text:
        return False
    cjk = len(CJK_RE.findall(text))
    return (cjk / max(1, len(text))) >= ratio


async def run_translate_job(job_id: str, video_id: str, source_lang: str = "auto", target_lang: str = "vi", provider: str = "google"):
    """Dịch transcript của 1 video trong library. Ghi transcript_vi.{json,txt} + cập nhật library."""
    video = await lib.get_video(video_id)
    if not video:
        await job_manager.finish_fail(job_id, f"Không tìm thấy video {video_id} trong library.")
        return

    transcript = lib.get_transcript(video_id)
    text = (transcript or {}).get("text") or ""
    if not text.strip():
        txt_path = lib.video_dir(video_id) / "transcript.txt"
        if txt_path.exists():
            text = txt_path.read_text(encoding="utf-8")
    if not text.strip():
        await lib.update_video(video_id, translation_status="none")
        await job_manager.finish_fail(job_id, "Video chưa có transcript — bấm Transcribe trước.")
        return

    segments = transcript.get("segments") if isinstance(transcript, dict) else None

    try:
        await job_manager.update(
            job_id,
            status="running",
            progress=5,
            message=f"Đang dịch sang '{target_lang}' bằng provider '{provider}' (external service)...",
        )
        await lib.update_video(
            video_id,
            translation_status="translating",
            translation_lang=target_lang,
            translation_provider=provider,
        )

        def _log(msg: str):
            print(f"[video_job:{job_id}] {msg}")

        _log(f"translate start: {len(text)} ký tự, {len(segments) if segments else '?'} segments, provider={provider}")
        translator = await asyncio.to_thread(get_translator, provider, source_lang, target_lang)

        if segments:
            total = len(segments)
            translated_segments = []
            for idx, seg in enumerate(segments):
                seg_text = (seg.get("text") or "").strip()
                translated = await _translate_unit(translator, seg_text) if seg_text else ""
                out = dict(seg)
                out["text"] = translated
                translated_segments.append(out)
                if (idx + 1) % 5 == 0 or idx + 1 == total:
                    await job_manager.update(
                        job_id,
                        progress=5 + int(55 * (idx + 1) / total),
                        message=f"Đang dịch... {idx + 1}/{total} đoạn",
                    )
            result = {
                "text": " ".join(s.get("text", "") for s in translated_segments if s.get("text")),
                "language": target_lang,
                "segments": translated_segments,
                "provider": provider,
            }
        else:
            lines = [ln for ln in text.split("\n") if ln.strip()]
            total = len(lines)
            translated_lines = []
            for idx, ln in enumerate(lines):
                translated_lines.append(await _translate_unit(translator, ln))
                if (idx + 1) % 5 == 0 or idx + 1 == total:
                    await job_manager.update(
                        job_id,
                        progress=5 + int(55 * (idx + 1) / total),
                        message=f"Đang dịch... {idx + 1}/{total} dòng",
                    )
            result = {
                "text": "\n".join(translated_lines),
                "language": target_lang,
                "provider": provider,
            }

        vi_text = lib.write_transcript_vi(video_id, result)
        await lib.update_video(
            video_id,
            translation_status="translated",
            transcript_vi_path=f"data/videos/{video_id}/transcript_vi.json",
            transcript_vi_text_len=len(vi_text),
            translated_at=lib.now_iso(),
        )
        _log(f"translate done: {len(vi_text)} ký tự tiếng Việt")
        await job_manager.finish_ok(job_id, [video_id], f"Đã dịch sang tiếng Việt ({len(vi_text)} ký tự).")
    except TranslationUnavailableError as e:
        await lib.update_video(video_id, translation_status="failed")
        await job_manager.finish_fail(job_id, f"Dịch thất bại: {e}")
    except Exception as e:
        print(f"[video_job:{job_id}] LỖI: {e!r}")
        print(traceback.format_exc())
        await lib.update_video(video_id, translation_status="failed", translation_error=format_job_error(e))
        err = str(e) if str(e) else repr(e)
        await job_manager.finish_fail(job_id, f"Dịch thất bại: {err}")