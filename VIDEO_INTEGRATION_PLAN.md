# Kế hoạch tích hợp Video Downloader vào CapCapTTS

> **Trạng thái:** PHÂN TÍCH / THIẾT KẾ — chưa implement. Mọi thông tin đều dẫn nguồn từ source code local thực tế. Những gì chưa kiểm chứng được ghi rõ **"Chưa xác minh"**.

---

## 1. Executive Summary

- **CapCapTTS** là web app TTS (Vietnamese Text-to-Speech) 100% local: FastAPI serve static frontend tại `http://localhost:8000`. Có 2 backend: `backend/` (GPU: Piper + F5-TTS + OmniVoice) và `backend_cpu/` (CPU: Piper). `launcher.py` ưu tiên `backend_cpu`. Frontend là SPA thuần JS (`frontend/app.js` gọi `/tts/*` qua `API_BASE = ''`).
- **ai-video-downloader-main** là CLI tool (click) tải video Douyin không watermark (Playwright intercept API), transcribe bằng faster-whisper, quản lý qua `index.csv`. **Trong Python code thực tế KHÔNG có** code dịch ngôn ngữ và KHÔNG có code yt-dlp (yt-dlp chỉ xuất hiện trong tài liệu `.claude/skills/*/SKILL.md`, không phải code; tuy nhiên package `yt_dlp` 2026.7.4 **đã cài sẵn** trong `.venv` của repo này).
- **Phương án MVP:** chép (vendoring) 4 module `crawler.py / downloader.py / transcribe.py / store.py` từ ai-video-downloader-main vào CapCapTTS dưới dạng package mới `backend_cpu/video_dl/`, thêm tầng "video library + job manager" và bộ API `/videos/*` theo đúng style FastAPI hiện có (async, pydantic, background task + polling như `/tts/generate`).
- Frontend thêm 3 vùng UI: card **Download Video**, grid **Video Library**, panel **Selected Video** — dùng đúng design tokens trong `frontend/style.css` (theme sáng nhạt `#f7f3eb`, accent `#5b7a6a`), KHÔNG đụng đến luồng TTS hiện tại.
- **Translation sang tiếng Việt:** KHÔNG có trong code của cả 2 repo → nằm ngoài MVP-1 (đề xuất MVP-2 với NLLB/opus-mt qua HuggingFace, ghi thêm dependency mới).

---

## 2. Repo Structure Observed

### 2.1 CapCapTTS (`E:\D\LamVideo\CapCapTTS\`)

```
CapCapTTS/
├── launcher.py              # Chọn backend_cpu nếu tồn tại, chạy uvicorn port 8000
├── run_api_cpu.bat          # cd backend_cpu; python -m uvicorn main:app --port 8000 --reload
│                            #   + set PATH thêm ffmpeg winget (Gyan.FFmpeg 9.0)
├── run_api.bat
├── backend/                 # GPU version (Piper + F5 + OmniVoice)
│   ├── main.py              # FastAPI: /tts/* + clone voice + resources (1297+ dòng)
│   ├── tts_engine.py        # PiperEngine, F5Engine, OmniVoiceEngine, TaskManager
│   ├── tts_quality_checker.py
│   ├── config.py            # FFMPEG_DIR mặc định PROJECT_ROOT/ffmpeg/bin
│   ├── requirements.txt
│   └── custom_dict/
├── backend_cpu/             # CPU version (Piper only) — LAUNCHER ƯU TIÊN
│   ├── main.py              # FastAPI: /tts/* (957 dòng)
│   ├── tts_engine.py        # PiperEngine, TaskManager (in-memory)
│   ├── config.py            # OUTPUT_DIR = backend_cpu/outputs
│   ├── requirements.txt
│   ├── custom_dict/         # acronyms.csv, non-vietnamese-words.csv, _history.json, _pause.json
│   └── outputs/             # task_id/chunk_*.wav, final.mp3, final.srt, segments.json
├── frontend/
│   ├── index.html           # SPA (857 dòng): input text, voice mode, segments, final audio bar, modals
│   ├── app.js               # (2366 dòng) fetch tất cả API /tts/*, API_BASE = ''
│   ├── style.css            # (2173 dòng) design tokens (light #f7f3eb + dark mode)
│   └── jszip.min.js, capcap.*, fonts/
└── screenshot.png
```

**Xác nhận:** CapCapTTS là web TTS app (không phải CapCap desktop). Không có ffmpeg folder trong repo (`E:\D\LamVideo\CapCapTTS\ffmpeg\bin\ffmpeg.exe` = **False**); máy dùng ffmpeg từ winget (xem `run_api_cpu.bat:20`).

### 2.2 ai-video-downloader-main (`E:\D\LamVideo\ai-video-downloader-main\`)

```
ai-video-downloader-main/
├── index.csv                # index downloads: aweme_id, author, title, status, local_path, download_url, downloaded_at, whisper-caption, transcribed_at (11 row success + 1 đã transcribe)
├── downloads/jingxuan/      # <author>__<title>__<aweme_id>/  chứa index.mp4 + metadata.json (+ 1 folder có transcript.json)
├── voice/                   # elevenlabs.csv, omnivoice.csv, openai.csv, vbee.csv (registry giọng)
├── .claude/skills/          # SKILL.md docs (video-download = yt-dlp, voice-over = dịch+TTS, douyin-crawler) — CHỈ LÀ INSTRUCTION CHO AGENT, KHÔNG PHẢI CODE CHẠY ĐƯỢC
└── backend/
    ├── __main__.py          # load_dotenv(.env) → cli()
    ├── cli.py               # click CLI: jingxuan / user / dl / transcribe (166 dòng)
    ├── crawler.py           # Playwright intercept XHR → aweme list (257 dòng)
    ├── downloader.py        # aiohttp tải mp4, retry, rich progress (168 dòng)
    ├── transcribe.py        # ffmpeg trích audio + faster-whisper CPU int8 (110 dòng)
    ├── store.py             # index.csv CRUD thread-safe (83 dòng)
    ├── voices/              # vbee.py (cần VBEE_TOKEN/APP_ID), omnivoice.py (server LAN 192.168.1.61:8002)
    ├── requirements.txt     # playwright, aiohttp, aiofiles, rich, click, faster-whisper
    ├── pyproject.toml       # (poetry, python ^3.11 — nhưng .venv thực tế là 3.10.11)
    └── .venv/               # Python 3.10.11 — ĐÃ CÀI: playwright 1.62, faster-whisper 1.2.1, yt_dlp 2026.7.4, aiohttp 3.14.3, aiofiles, rich, click, ctranslate2
```

**Xác nhận môi trường máy:**
- Playwright Chromium đã download sẵn: `%LOCALAPPDATA%\ms-playwright\chromium-1234` (khớp playwright 1.62).
- Python của CapCapTTS (`C:\Users\hophu\AppData\Local\Programs\Python\Python310`) đã có fastapi/uvicorn/pydub/aiohttp/aiofiles/rich/click; **CHƯA có** playwright, faster-whisper, yt-dlp (kiểm chứng lúc 2026-08-17).
- 2 repo đều dùng Python 3.10 → tương thích.

---

## 3. Current CapCapTTS API/UI Flow

### 3.1 API hiện tại (đều prefix `/tts`, định nghĩa trong `backend_cpu/main.py`)

| Endpoint | Hàm | Vai trò |
|---|---|---|
| `GET /tts/voices` | `list_voices` (main.py:214) | Danh sách giọng theo tier low/medium/high |
| `POST /tts/preview` | `preview_tts` (:225) | Preview ngắn ~100 ký tự → wav trong `outputs/_preview/` |
| `POST /tts/generate` | `generate_tts` (:262) | Tạo task_id, chạy `_run_generation` background (asyncio.create_task) |
| `GET /tts/status/{task_id}` | `get_status` (:379) | Poll tiến độ: status/progress/stage/chunks[] |
| `POST /tts/regenerate_chunk` | (:406) | Generate lại 1 chunk |
| `POST /tts/merge` | `merge_chunks` (:446) | Ghép chunks → final.mp3 + final.srt, ghi history |
| `GET /tts/download_file?path=` | (:536) | Serve/convert audio (mp3 128/320, wav, srt on-the-fly) |
| `POST /tts/reset/{task_id}` | (:593) | Reset task, xóa chunk |
| `GET /tts/resource_catalog`, `POST /tts/start_download`, `GET /tts/download_progress` | (:712-734) | Download model từ HuggingFace với progress state `_dl_state` in-memory + asyncio.Lock |
| `GET /tts/model_status`, `POST /tts/load_model` | (:739-753) | CPU stub: GPU required |
| `GET/POST/DELETE /tts/dict/*`, `/tts/pause_config`, `/tts/history` | | Dictionary, pause, history (JSON file `custom_dict/_history.json`, max 30) |

- **TaskManager** (`backend_cpu/tts_engine.py:305`) lưu task **in-memory** (`self._tasks: dict[str, dict]`), có `create/update/get/set_chunks/update_chunk/set_chunk_audio_with_quality/recalc_progress/reset`. Output audio ghi vào `backend_cpu/outputs/{task_id}/` (chunk_*.wav, final.mp3, final.srt, segments.json).
- **Pattern background job hiện có (tái sử dụng cho video download):** `asyncio.create_task()` + state dict + `asyncio.Lock` + endpoint poll — đúng pattern `_dl_state`/`_download_resource` (main.py:614-734). Video download nên bắt chước pattern này, không phải pattern TaskManager (TaskManager gắn với TTS pipeline).

### 3.2 UI Flow hiện tại (frontend)

- `app.js:1` `const API_BASE = ''` → cùng origin.
- Luồng: nhập/paste text vào `#textInput` → chọn voice tier/id → `POST /tts/generate` → poll `/tts/status/{task_id}` → segments hiện `#chunkList` → `Merge & Download All` → final audio bar (`#finalAudioBar`) với player/waveform/export.
- UI hiện có: header (Dict/Pause/History/Resources/Guide/Reset/Theme buttons), `.main-layout` 2 cột (`left-panel`: input card, voice card, progress; `right-panel`: chunk panel), modals (dict, pause, voice select, history, voice lab, resources, help).
- **Design tokens** (style.css:4-28): `--bg-primary:#f7f3eb`, `--bg-secondary:#ede7d9`, `--card-bg:#faf8f3`, `--card-border:#d4c9b8`, `--text-primary:#3d3529`, `--accent-primary:#5b7a6a`, `--success:#6b8e6b`, `--error:#b85c5c`, `--warning:#c4a35a`, radius 6/10/14, có dark mode `[data-theme=dark]`. Card class `.card`, buttons `.btn .btn-primary .btn-secondary .btn-icon .btn-sm`, modal `.modal-overlay .modal`.
- **Nơi serve frontend:** `backend_cpu/main.py:944` `app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True))` — mọi route mới phải đăng ký TRƯỚC mount này (FastAPI match theo thứ tự registration).

### 3.3 Nơi lưu output/history/resources

- Audio: `backend_cpu/outputs/{task_id}/`
- History: `backend_cpu/custom_dict/_history.json`
- Dictionaries: `backend_cpu/custom_dict/*.csv`
- Models: `resources/piper/` (config.py:9-11)

---

## 4. ai-video-downloader Reusable Components

| Repo | Module/File | Function/Class | Chức năng | Tái dùng trực tiếp? | Ghi chú |
|---|---|---|---|---|---|
| ai-video-downloader | `backend/crawler.py` | `crawl_single_url(url, headless, cookies_file)` (line 205) | Crawl 1 video Douyin từ link detail/video id → `{aweme_id,title,author,download_url}` | ✅ Có (copy, đổi import) | Playwright intercept API response. Dùng `_make_context` tạo context mới, KHÔNG dùng profile thật, KHÔNG cookie mặc định |
| ai-video-downloader | `backend/crawler.py` | `crawl_jingxuan(...)` (line 183), `crawl_user_profile(...)` (line 194) | Crawl feed/profile Douyin → async generator video dict | ✅ Có | Dùng `_crawl()` chung (line 110) với scroll, stop_when_no_new |
| ai-video-downloader | `backend/crawler.py` | `_parse_aweme` (line 64), `_get_download_url` (line 53), `_is_douyin_api` (line 81) | Parse JSON response → video info + URL không watermark (strip wm params) | ✅ Có | |
| ai-video-downloader | `backend/downloader.py` | `download_videos(videos, output_dir, concurrency=4)` (line 147) | Tải hàng loạt, retry 3 lần, skip nếu file >10KB, ghi `metadata.json`, upsert index | ✅ Có (cần sửa nhẹ) | **Chặn tái dùng nguyên trạng:** dùng `rich.progress.Progress` console → cần tham số callback tiến độ để UI đọc; import `from .store import upsert_download` → copy theo package mới |
| ai-video-downloader | `backend/downloader.py` | `_safe_filename(text, max_len=80)` (line 40) | Sanitize tên file (strip `\/:*?"<>|`) | ✅ Có | Quan trọng cho Unicode/Chinese title |
| ai-video-downloader | `backend/transcribe.py` | `transcribe_video(video_path, model, language, aweme_id)` (line 73) | ffmpeg trích audio 16kHz mono → faster-whisper (CPU int8, vad_filter) → `{language, language_probability, text, segments}` + ghi `transcript.json` cạnh video | ✅ Có | `_run_faster_whisper` (line 32) import lazy `faster_whisper`; model mặc định param là `large-v3-turbo` nhưng CLI truyền `base` (cli.py:150-152). **Không có translation** |
| ai-video-downloader | `backend/store.py` | `upsert_download`, `upsert_caption`, `get_row`, `get_aweme_id_from_path` | index.csv (aweme_id, author, title, status, local_path, download_url, downloaded_at, whisper-caption, transcribed_at) | ⚠️ Không dùng trực tiếp | CSV ký tự `__`/`,` dễ vỡ; thay bằng JSON library riêng (`video_library.py`). Có thể đọc file index.csv cũ để import dữ liệu đã tải |
| ai-video-downloader | `backend/__main__.py`, `backend/cli.py` | CLI entry | Không cần cho web | ❌ Không | |
| ai-video-downloader | `backend/voices/*` | vbee/omnivoice adapters | TTS cloud/LAN — **không liên quan** vì CapCapTTS đã có TTS local | ❌ Không | |
| ai-video-downloader | `.claude/skills/*/SKILL.md` | yt-dlp command, dịch thủ công | Hướng dẫn cho AI agent | ❌ Không dùng làm nguồn thực thi | **`yt-dlp` chưa có code Python wrapper trong repo** — chỉ có command mẫu trong docs. Cần viết wrapper mới (in-process `yt_dlp.YoutubeDL`, không qua shell) |
| — | — | Translation zh→vi | **KHÔNG TỒN TẠI trong code 2 repo** | ❌ | Skill voice-over dịch bằng tay qua AI agent; web app cần model dịch mới (NLLB / opus-mt-zh-vi) → MVP-2 |

**Trả lời các câu hỏi chốt:**
- Hàm download 1 video: `crawl_single_url()` (crawler.py:205) → `download_videos([video])` (downloader.py:147) — hoặc trực tiếp tải `download_url`.
- Hàm crawl Douyin feed/profile: `crawl_jingxuan()` / `crawl_user_profile()`.
- Output video lưu ở đâu: `downloads/jingxuan/{author}__{title}__{aweme_id}/index.mp4` + `metadata.json` (`{aweme_id, author, title, download_url, detail_url}`). **Không có thumbnail** và **không có info json** trong code Douyin path.
- Transcribe callable: `transcribe_video()` — ghi `transcript.json`, cập nhật index.csv.
- CapCapTTS API tạo audio: `POST /tts/generate` {text, voice_mode, voice_id, ...} → poll `/tts/status/{id}` → `/tts/merge`.
- Frontend gọi API nào: toàn bộ `/tts/*` liệt kê ở mục 3.
- History/resource manager: có history JSON (`custom_dict/_history.json`) và resource downloader (`_dl_state` pattern) — pattern job tái sử dụng được cho video download.

---

## 5. Proposed Unified User Flow

```
http://localhost:8000
│
├─ Card "Download Video"
│    nhập URL (douyin.com / youtube.com / tiktok.com ...)
│    select platform: Auto / Douyin / TikTok / YouTube
│    max videos (nếu là feed/profile Douyin)
│    checkbox "Hiện browser nếu gặp captcha" (headless=false)
│    [Download] → job background, progress realtime (status bar giống TTS)
│
├─ Card "Video Library" (grid, tự refresh sau khi download xong)
│    thumbnail / placeholder • title • platform badge • duration • created_at
│    status: downloaded / transcribed / sent_to_tts / error
│    actions: Select • Transcribe • Send to TTS • Delete
│
├─ Panel "Selected Video" (mở khi chọn 1 video)
│    <video> preview (FileResponse qua /videos/{id}/file)
│    metadata: title, author, platform, path, size, duration, aweme_id/yt id
│    transcript preview (nếu có) + nút "Đưa transcript vào TTS"
│    nút "Mở thư mục chứa video" (chỉ hiện nếu có quyền os.startfile — giới hạn local)
│
└─ TTS panel hiện tại (GIỮ NGUYÊN)
     textarea #textInput được điền tự động bởi nút "Đưa transcript vào TTS"
     mọi workflow paste tay / generate / merge không thay đổi
```

**Nguyên tắc:** mọi thao tác đều trên UI; không đụng code TTS; video library là tầng dữ liệu riêng.

---

## 6. Backend API Plan

Đặt tên theo style hiện có (`/tts/*`, pydantic model, async, background task). Toàn bộ route mới đăng ký **trước** dòng mount static (main.py:944).

### 6.1 Routes mới trong `backend_cpu/main.py` (import từ các service mới)

| Method | Route | Request body | Trả về | Ghi chú |
|---|---|---|---|---|
| `POST` | `/videos/download` | `{url, source_type, max_items=1, headless=true, cookies=None}` | `{job_id}` | Validate URL trước khi tạo job. source_type ∈ auto/douyin/tiktok/youtube |
| `GET` | `/videos/download_progress` | — | `{job_id: {status, stage, progress, current_file, items_done, items_total, error}}` | Mirror `_dl_state` (main.py:615-616) |
| `GET` | `/videos` | — | `{videos: [video_meta]}` | Đọc `data/videos/library.json` |
| `GET` | `/videos/{video_id}` | — | `video_meta` đầy đủ | Gồm transcript_path, status... |
| `GET` | `/videos/{video_id}/transcript` | — | `{text, segments, language}` | Đọc transcript.json nếu có, 404 nếu chưa transcribe |
| `POST` | `/videos/{video_id}/transcribe` | `{model="base", language=None}` | `{job_id}` | Chạy faster-whisper background; cập nhật status |
| `POST` | `/videos/{video_id}/send-to-tts` | — | `{text}` | Frontend lấy text này điền vào `#textInput` (frontend-only thao tác; API chỉ trả text) |
| `DELETE` | `/videos/{video_id}` | — | `{status: "deleted"}` | Xóa folder video + transcript + entry library |
| `GET` | `/videos/{video_id}/file` | — | `FileResponse(index.mp4)` | Serve video preview/export |
| `GET` | `/videos/{video_id}/thumbnail` | — | `FileResponse(thumb)` | Nếu có thumbnail, 404 nếu chưa |

> `POST /videos/{id}/translate` — **defer sang MVP-2** (chưa có engine dịch trong repo).

### 6.2 Job pipeline (`video_downloader.py`)

1. **Validate URL:** chỉ chấp nhận scheme `http`/`https` (regex + `urllib.parse`), host allowlist theo platform (douyin.com, tiktok.com, youtube.com/youtu.be) khi source_type cụ thể; tự động detect platform qua domain nếu `auto`. **Không bao giờ đưa URL vào shell** (không dùng `subprocess` với chuỗi URL).
2. **Douyin:** `video_dl.crawl_single_url()` (hoặc crawl feed nếu max_items>1) → `download_videos()` với callback progress.
3. **YouTube/TikTok:** wrapper mới dùng `yt_dlp.YoutubeDL` **in-process** với `progress_hooks`, `outtmpl` = thư mục library, `--write-info-json`, `--write-thumbnail`, `format` = `bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best`, merge qua ffmpeg của CapCapTTS (FFMPEG_DIR) nếu có; không cài cookies mặc định, không dùng `cookies-from-browser`.
4. **Normalize:** mọi kết quả copy/chuyển về `data/videos/{video_id}/` với cấu trúc chuẩn (mục 8); ghi `metadata.json` + entry `library.json`.
5. **Progress:** state dict in-memory (giống `_dl_state`) — chưa cần DB.

### 6.3 Nơi đặt code downloader (quyết định kiến trúc)

- **KHÔNG import trực tiếp `backend.*` từ ai-video-downloader** (xung đột tên package `backend` với CapCapTTS).
- **KHÔNG chạy subprocess `python -m backend` của repo kia** (rất dễ vỡ, khó truyền progress, chạy sai Python).
- **Giải pháp: vendoring** — copy `crawler.py, downloader.py, transcribe.py` sang package mới `backend_cpu/video_dl/`, sửa import tương đối (`from .store` → dùng library mới), bỏ `rich` console progress (thay callback), bỏ phụ thuộc click. Đảm bảo self-contained, chạy đúng env của CapCapTTS, chủ động vá lỗi sau này.

---

## 7. Frontend UI Plan

Giữ nguyên cấu trúc SPA; thêm một section mới **trên** `.main-layout` (index.html:81) hoặc tab riêng `Video` trong header — đề xuất section collapsible giữa header và main-layout để không phá layout 2 cột hiện tại.

### A. Card "Download Video"
- `<input id="videoUrl">` (placeholder: "Paste link Douyin / TikTok / YouTube...")
- `<select id="videoPlatform">`: Auto / Douyin / TikTok / YouTube
- `<input type="number" id="videoMaxItems" min=1 max=50 value=1>` (ẩn nếu single)
- `<label><input type="checkbox" id="videoHeadless" checked> Ẩn browser (bỏ tick nếu gặp captcha)</label>`
- `<button id="videoDownloadBtn" class="btn btn-primary">Download</button>`
- `<div id="videoJobStatus">` — thanh tiến độ dùng class sẵn `.progress-bar-bg/.progress-bar-fill` (index.html:249-252)

### B. Card "Video Library"
- Grid 4 cột (responsive), mỗi item là card nhỏ: thumbnail (nếu có, `GET /videos/{id}/thumbnail`) hoặc placeholder icon platform; title (ellipsis 2 dòng); badges platform + status (`downloaded`/`transcribed`/`sent_to_tts`/`error`); duration; created_at.
- Nút: **Select** (mở panel C), **Transcribe**, **Send to TTS**, **Delete** (xác nhận confirm()).
- Empty state: "Chưa có video nào — dán link ở trên để bắt đầu".

### C. Panel "Selected Video" (slide-in hoặc modal, dùng class `.modal`)
- `<video controls src="/videos/{id}/file">` (HTML5 preview)
- Metadata grid: title, author, platform, path (tương đối), size MB, duration, id.
- Transcript area (readonly `<textarea>` + nút Copy).
- Nút: **"Đưa transcript vào TTS"** → `fetch /videos/{id}/send-to-tts` → `textInput.value = text; updateCharCount();` + toast "Đã đưa text vào khung TTS" → **không tự động generate** (để user chọn giọng như thường).
- Nút **"Mở thư mục"** — chỉ dùng `window.open('/videos/{id}/folder')` nếu backend expose; backend gọi `os.startfile` (Windows) — cân nhắc bật/tắt bằng config (local app, chấp nhận được, nhưng để tùy chọn `ALLOW_OPEN_FOLDER`).

### D. TTS panel — KHÔNG THAY ĐỔI
Chỉ thêm hint nhỏ nếu có text từ video: không đụng state/flow generate hiện có.

### JS structure (`app.js`)
Thêm khối `// ─── Video Library ───` cuối file: `videoState`, `loadVideoLibrary()`, `startVideoDownload()`, `pollVideoJobs()`, `selectVideo(id)`, `transcribeVideo(id)`, `sendTranscriptToTTS(id)`, `deleteVideo(id)`, `renderVideoGrid()`, `renderVideoDetail()` — theo đúng style hiện tại (querySelector theo id, fetch + async/await, cập nhật DOM trực tiếp).

### CSS (`style.css`)
Thêm khối `/* ─── Video Section ─── */` dùng 100% biến sẵn có (`--card-bg`, `--card-border`, `--accent-primary`, `--success`, `--error`, `--radius-*`); badge màu dùng `--success-light`/`--error-light`... để khớp theme sáng nhạt + dark mode tự động.

---

## 8. Data Model and Storage Layout

Thư mục mới (tạo trong `backend_cpu/config.py`):

```
CapCapTTS/data/videos/
├── library.json                 # index toàn bộ video (JSON, thay cho index.csv)
└── {video_id}/
    ├── index.mp4                # video (đổi tên từ file gốc để chuẩn hóa)
    ├── index.info.json          # metadata yt-dlp (chỉ YouTube/TikTok path)
    ├── thumbnail.jpg|webp       # nếu tải được (yt-dlp --write-thumbnail) hoặc trích từ ffmpeg
    ├── metadata.json            # thông tin crawl/download (douyin: aweme_id, author, title, download_url, detail_url)
    ├── transcript.json          # {language, language_probability, text, segments[]}
    ├── transcript.txt           # text thuần (tiện gửi vào TTS)
    └── tts_outputs/             # (tương lai) audio TTS từ transcript
```

`video_id` = `aweme_id` (Douyin) hoặc yt-dlp `id` (YouTube/TikTok) — unique.

**`library.json`** — mỗi entry:
```json
{
  "id": "7674256338892989608",
  "url": "https://www.douyin.com/video/...",
  "platform": "douyin",
  "title": "饭店的暑假工，检查仪容仪表！",
  "author": "周小闹",
  "video_path": "data/videos/7674256338892989608/index.mp4",
  "thumbnail_path": null,
  "transcript_path": null,
  "created_at": "2026-08-17T20:28:15",
  "duration": 42.5,
  "status": "downloaded",          // downloaded | transcribed | sent_to_tts | error
  "source_repo": "ai-video-downloader-main (vendored video_dl)",
  "function_used": "crawl_single_url + download_videos"
}
```

- **duration:** đọc bằng `ffprobe` (FFMPEG_DIR trong config.py:15) hoặc pydub; nếu không có ffmpeg → để `null` (UI hiển thị "—").
- **thumbnail:** ưu tiên yt-dlp; Douyin không có → trích frame đầu bằng ffmpeg (`-ss 0.5 -frames:v 1`), nếu không có ffmpeg → placeholder.
- **Migrate/import:** API `POST /videos/import` đọc `downloads/*/*/index.mp4 + metadata.json + transcript.json` của ai-video-downloader-main (hoặc `index.csv`) → copy vào library (mở rộng, để MVP-2).

---

## 9. File-Level Implementation Plan (MVP-1)

> Tên file/service đặt theo thực tế source đã đọc. Không có thư mục `services/` hiện tại trong repo — các module hiện đặt phẳng trong `backend_cpu/`, nên service mới đặt cùng cấp.

| # | Task | File tạo/sửa | Function/Class | Thay đổi cụ thể |
|---|---|---|---|---|
| 1 | **Backup** | git snapshot / copy folder | — | Chạy `git status` trong CapCapTTS (repo đã có `.git`) hoặc backup thủ công trước khi sửa |
| 2 | Vendoring | **Tạo** `backend_cpu/video_dl/__init__.py`, `crawler.py`, `downloader.py`, `transcribe.py` | Copy từ ai-video-downloader-main `backend/` | Sửa import `from .store import ...` → bỏ hoặc thay bằng callback; bỏ `rich.console`/`rich.progress` → thêm `progress_cb` tham số; `_safe_filename`, `_get_download_url`, `_parse_aweme`, `_crawl` giữ nguyên |
| 3 | Library store | **Tạo** `backend_cpu/video_library.py` | `VideoLibrary` class: `list_videos()`, `get_video(id)`, `upsert_video(meta)`, `delete_video(id)`, `update_status(id, status)` | JSON CRUD trên `data/videos/library.json`, dùng `asyncio.Lock` (mô phỏng store.py pattern) |
| 4 | Job manager | **Tạo** `backend_cpu/video_jobs.py` | `video_jobs: dict`, `create_job()`, `update_job()`, `get_jobs()` + `asyncio.Lock` | Mirror `_dl_state` pattern (main.py:615-616) |
| 5 | Orchestrator | **Tạo** `backend_cpu/video_downloader.py` | `validate_url(url)`, `detect_platform(url)`, `download_douyin(job, url, max_items, headless)`, `download_ytdlp(job, url, platform)`, `normalize_into_library(...)` | Wrapper yt-dlp in-process (không shell); tải Douyin qua video_dl; copy về `data/videos/{id}/`; ghi metadata.json + library entry |
| 6 | Transcriber | **Tạo** `backend_cpu/video_transcriber.py` | `transcribe_video(video_id, model, language)` | Gọi `video_dl.transcribe_video()` (sửa chỗ ghi file → đúng folder library + transcript.txt); update status |
| 7 | Config | **Sửa** `backend_cpu/config.py` | thêm `VIDEO_LIBRARY_DIR`, `VIDEO_DOWNLOAD_CONCURRENCY=2`, `ALLOW_OPEN_FOLDER=true`, `YTDLP_FORMAT` | env-var override như pattern `_env_path` hiện có |
| 8 | Routes | **Sửa** `backend_cpu/main.py` | thêm 10 route `/videos/*` (mục 6) | Import các service; đăng ký TRƯỚC `app.mount("/")` (line 944) |
| 9 | Dependencies | **Sửa** `backend_cpu/requirements.txt` | thêm `playwright>=1.44`, `faster-whisper>=1.0.0`, `yt-dlp>=2026.1.1` | Không cài global — ghi vào requirements; cài trong env CapCapTTS (python 3.10) |
| 10 | UI HTML | **Sửa** `frontend/index.html` | thêm section Video (3 card + job status) | Đặt giữa `.header` và `.main-layout`; dùng class sẵn có |
| 11 | UI JS | **Sửa** `frontend/app.js` | khối `// ─── Video Library ───` (mục 7) | Thêm vào cuối file, không sửa hàm TTS |
| 12 | UI CSS | **Sửa** `frontend/style.css` | khối `/* ─── Video Section ─── */` | Chỉ dùng CSS vars sẵn có |
| 13 | Docs | **Sửa** `README.md` (CapCapTTS) | mục mới "Video Library" | Hướng dẫn cài dep mới + chạy |
| 14 | GPU parity | (defer) `backend/` | — | Đồng bộ sang GPU backend khi MVP-1 ổn định |

**Thứ tự implement:** (2→9) backend trước → (10-12) UI → test mock → test thật (với sự đồng ý) → KHÔNG sửa code TTS hiện có.

---

## 10. Risk and Mitigation

| # | Rủi ro | Phân tích thực tế | Giải pháp |
|---|---|---|---|
| 1 | **Douyin captcha/login** | `crawler.py` đã có stealth script + xóa login panel (`page.evaluate` crawler.py:155-158, 167-170); bản thân repo đã tải được 11 video thật (index.csv) nên flow hiện hoạt động | Giữ `headless=false` optional (checkbox "Hiện browser"); không tự đăng nhập; nếu vẫn chặn → hiện message gợi ý bật browser |
| 2 | **Playwright trên Windows** | Chromium 1234 đã cài tại `%LOCALAPPDATA%\ms-playwright`; playwright 1.62 khớp; CapCapTTS env thiếu package → cần cài | Thêm vào requirements; cài qua `pip install -r`; `--no-sandbox` args vô hại trên Windows |
| 3 | **ffmpeg PATH** | CapCapTTS không bundle ffmpeg; máy có ffmpeg winget (run_api_cpu.bat:20) và playwright bundle ffmpeg (`%LOCALAPPDATA%\ms-playwright\ffmpeg-1011`) | Config `FFMPEG_DIR`; fallback tìm `ffmpeg` trong PATH + playwright bundle; yt-dlp merge dùng ffmpeg được chỉ định (`ffmpeg_location`) |
| 4 | **Unicode path (Chinese/Vietnamese)** | Tên folder Douyin đầy ký tự Trung (đã thấy trong `downloads/jingxuan/`) — `_safe_filename` (downloader.py:40) chỉ strip ký tự cấm; Windows NTFS + Python 3.10 Unicode OK; nguy cơ độ dài path | Giữ `_safe_filename` + giới hạn 80 ký tự; folder trong library chỉ dùng `{video_id}` (ngắn, ASCII) — tránh hẳn vấn đề |
| 5 | **URL validation / command injection** | yt-dlp phải gọi qua shell mới dễ bị injection | **Bắt buộc:** validate scheme http/https + regex domain; dùng `yt_dlp.YoutubeDL(opts)` in-process, URL truyền qua Python API — **không bao giờ** `subprocess`/`os.system` với URL. Đây là lý do KHÔNG copy command mẫu từ SKILL.md |
| 6 | **Browser cookies/profile** | `_make_context` (crawler.py:89) chỉ load cookie nếu có `cookies_file` param; mặc định None; không dùng profile thật | Giữ mặc định không cookie; endpoint `/videos/download` nhận `cookies` là path tùy chọn (chỉ admin local), UI mặc định ẩn; không dùng `cookies-from-browser` |
| 7 | **Download nhiều video treo UI** | FastAPI async + `asyncio.create_task` background như TTS pattern | Job manager + poll `/videos/download_progress`; frontend poll 1s; không chặn event loop (aiohttp/yt-dlp chạy executor) |
| 8 | **Progress realtime** | downloader gốc dùng rich console | Thêm `progress_cb(bytes_done, bytes_total, current_file)`; yt-dlp `progress_hooks`; tổng hợp vào job state |
| 9 | **File lớn đầy ổ** | Video 20-180MB (đã thấy) | Kiểm tra `shutil.disk_usage` trước download; xóa file lỗi (downloader đã unlink khi fail, downloader.py:130); API Delete; message lỗi rõ |
| 10 | **TTS server CPU chậm** | Piper CPU nhanh; transcribe CPU (faster-whisper int8) chậm với model lớn | Mặc định model `base`/`small`; cho chọn model trong UI; chạy job riêng (không chặn TTS); hiện stage "extracting audio / transcribing" |
| 11 | **Dependency conflict 2 repo** | Downloader .venv có sẵn mọi thứ nhưng CapCapTTS env thiếu 3 package; không xung đột version đáng kể với fastapi/uvicorn/aiohttp đã có | Thêm chính xác 3 dòng vào `backend_cpu/requirements.txt`; không cài global; không dùng .venv của repo kia |
| 12 | **Route "/" mount** | `app.mount("/", StaticFiles)` (main.py:944) nuốt mọi path | Đăng ký `/videos/*` trước mount (FastAPI match theo thứ tự) |
| 13 | **Translation không có sẵn** | Không code dịch trong 2 repo | MVP-1: chỉ transcribe; MVP-2: thêm engine dịch (NLLB/opus-mt qua transformers, ghi rõ requirements mới) — UI để nút translate disabled với tooltip "Sắp có" |

---

## 11. MVP Scope

**Trong scope (MVP-1):**
1. Backend: `video_dl/` (vendored), `video_library.py`, `video_jobs.py`, `video_downloader.py`, `video_transcriber.py`, 10 routes `/videos/*`, config mở rộng, requirements +3.
2. Frontend: card Download + library grid + selected video panel + send transcript to TTS (điền `#textInput`, không auto-generate).
3. Nền tảng: Douyin (crawler thật) + YouTube/TikTok (yt-dlp wrapper mới).
4. Transcribe: faster-whisper CPU, model mặc định `base`, chọn được.
5. Delete video; preview video; thumbnail (yt-dlp / ffmpeg nếu có).

**Ngoài scope:**
- Dịch sang tiếng Việt (MVP-2 — cần engine mới + requirements mới).
- TTS tự động cả video (đưa transcript vào TTS là thủ công có kiểm soát, như flow yêu cầu).
- Import dữ liệu cũ từ `downloads/` của ai-video-downloader (MVP-2).
- GPU backend parity (backend/) (sau MVP-1).
- Voice-clone liên kết video ↔ giọng (tương lai).

**Test strategy:** test download bằng file mẫu/mock (URL giả không gọi mạng) → test Douyin crawl dry-run (`--dry-run` tương đương) → chỉ download thật khi user đồng ý. Không chạy download thật trong giai đoạn phân tích (đã tuân thủ).

---

## 12. Future Enhancements

1. **Translation zh→vi (và en→vi):** thêm `transformers` + NLLB-200-distilled-600M hoặc `opus-mt-zh-vi` (CPU khả thi), endpoint `POST /videos/{id}/translate` → `transcript-vi.json`, hiển thị song song.
2. **Auto dub video:** transcript → TTS (giọng CapCap hiện có) → `ffmpeg` ghép `output_vi.mp4` (pattern từ SKILL.md nhưng viết thành code an toàn, không shell).
3. **Import từ ai-video-downloader-main:** quét `downloads/**/index.mp4` + `index.csv` → import vào library.
4. **GPU backend parity** cho `/videos/*`.
5. **Thumbnail cho Douyin:** crawl `video.cover`/`video.origin_cover` trong aweme JSON (crawler.py hiện không parse cover — sửa `_parse_aweme`).
6. **Xóa video theo lịch / dọn outputs TTS cũ.**
7. **Chọn giọng TTS khi gửi transcript** (kết hợp voice_id hiện tại).

---

## 13. Final Recommendation

- **Kiến trúc:** vendoring 4 module downloader vào `backend_cpu/video_dl/` + tầng service riêng + API `/videos/*` + UI section mới — phù hợp nhất với cấu trúc thực tế, an toàn (không shell, không cookie thật, không login), không phá TTS hiện có, đúng style code hiện tại (async + pydantic + background task + poll).
- **Điểm cần user quyết định trước khi implement:**
  1. Cài thêm 3 dependency (`playwright`, `faster-whisper`, `yt-dlp`) vào environment Python 3.10 của CapCapTTS — **không cài global**.
  2. Thư mục library riêng `CapCapTTS/data/videos/` (khuyến nghị) **hay** dùng thẳng `downloads/` của ai-video-downloader-main.
  3. Nút "Mở thư mục" (os.startfile) có bật không.
  4. Translation nằm ngoài MVP-1 — xác nhận.

- **Không cần** viết lại gì ở `backend/` (GPU) và `frontend` TTS flow ở giai đoạn này.
