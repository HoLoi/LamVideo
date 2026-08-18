"""
Video job manager — state in-memory, mirror pattern `_dl_state` trong main.py.
job_id có prefix: "dl_" (download) / "tr_" (transcribe).
"""
import asyncio
import sys
import time
import uuid

MAX_JOBS = 200


def format_job_error(exc: Exception) -> str:
    """Message lỗi dễ đọc cho job: 'TypeName: chi tiết' — KHÔNG bao giờ rỗng.

    NotImplementedError thường không có message (str() == '') nên dùng repr;
    kèm ghi chú thân thiện cho lỗi event loop Windows (Selector không hỗ trợ
    subprocess — Playwright hay gặp nhất).
    """
    raw = str(exc).strip() or repr(exc) or exc.__class__.__name__
    if isinstance(exc, NotImplementedError) and sys.platform == "win32":
        raw = ("Lỗi event loop Windows (SelectorEventLoop không hỗ trợ subprocess) — "
               "Playwright không thể chạy trên loop chính. Cần chạy crawler trong "
               "worker thread với Proactor. " + raw)
    return f"{type(exc).__name__}: {raw}"[:500]


class VideoJobManager:
    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def create(self, kind: str, message: str = "") -> str:
        job_id = f"{kind}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        job = {
            "job_id": job_id,
            "kind": kind,
            "status": "queued",   # queued | running | done | failed
            "progress": 0,
            "message": message,
            "video_ids": [],
            "error": "",
            "created_at": time.time(),
        }
        async with self._lock:
            self._jobs[job_id] = job
            # dọn job cũ giữ bộ nhớ ổn định
            if len(self._jobs) > MAX_JOBS:
                for k in list(self._jobs)[:-MAX_JOBS]:
                    self._jobs.pop(k, None)
        return job_id

    async def update(self, job_id: str, **fields):
        async with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.update(fields)

    async def get(self, job_id: str) -> dict | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    async def finish_ok(self, job_id: str, video_ids: list[str], message: str = "Hoàn tất"):
        await self.update(job_id, status="done", progress=100, message=message, video_ids=video_ids, error="")

    async def finish_fail(self, job_id: str, error: str):
        await self.update(job_id, status="failed", error=str(error)[:500], message=str(error)[:500])


job_manager = VideoJobManager()