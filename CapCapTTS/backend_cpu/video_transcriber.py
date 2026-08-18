"""
Video transcriber service — transcribe 1 video trong library bằng faster-whisper.
Kết quả lưu: transcript.json (đầy đủ) + transcript.txt (text thuần) trong folder video.
"""
import traceback

from video_dl import transcribe
from video_jobs import job_manager, format_job_error
import video_library as lib

WHISPER_MODELS = {"tiny", "base", "small", "medium", "large", "large-v3-turbo"}


async def run_transcribe_job(job_id: str, video_id: str, model: str):
    video = await lib.get_video(video_id)
    if not video:
        await job_manager.finish_fail(job_id, f"Không tìm thấy video {video_id} trong library.")
        return
    if model not in WHISPER_MODELS:
        await job_manager.finish_fail(job_id, f"Model không hợp lệ: {model!r}. Cho phép: {', '.join(sorted(WHISPER_MODELS))}.")
        return

    d = lib.video_dir(video_id)
    video_path = d / "index.mp4"
    if not video_path.exists():
        await job_manager.finish_fail(job_id, "File video không tồn tại (đã bị xóa?).")
        return

    try:
        await job_manager.update(job_id, status="running", progress=5, message="Đang trích xuất audio...")
        await lib.update_video(video_id, status="transcribing")

        def _log(msg: str):
            print(f"[video_job:{job_id}] {msg}")

        result = await transcribe.transcribe_video(
            video_path=str(video_path),
            model=model,
            language=None,
            aweme_id=video_id,
            log=_log,
        )

        text = lib.write_transcript_txt(video_id, result)
        await lib.update_video(
            video_id,
            status="transcribed",
            transcript_path=f"data/videos/{video_id}/transcript.json",
            transcript_language=result.get("language"),
            transcript_text_len=len(text),
        )
        await job_manager.finish_ok(job_id, [video_id], f"Transcribe xong ({len(text)} ký tự).")
    except ImportError as e:
        await lib.update_video(video_id, status="downloaded")
        await job_manager.finish_fail(job_id, f"Thiếu dependency: faster-whisper chưa cài. {e}")
    except Exception as e:
        print(f"[video_job:{job_id}] LỖI: {e!r}")
        print(traceback.format_exc())
        await lib.update_video(video_id, status="downloaded")
        await job_manager.finish_fail(job_id, f"Transcribe thất bại: {format_job_error(e)}")