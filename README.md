<p align="center">
  <img src="CapCapTTS/frontend/capcap.svg" alt="LamVideo" width="104">
</p>

<h1 align="center">LamVideo</h1>

<p align="center">
  TTS tiếng Việt local · Tải video · Phiên âm · Dịch · Lồng tiếng · Xuất video
</p>

LamVideo là monorepo cho một web app chạy hoàn toàn trên máy cá nhân, phục vụ hai nhóm công việc:

- **Tạo giọng đọc tiếng Việt** bằng Piper, có chia đoạn, kiểm tra chất lượng từng đoạn, ghép audio và xuất phụ đề.
- **Xử lý video** từ lúc tải về, phiên âm, dịch sang tiếng Việt, lồng tiếng, che phụ đề cũ, thêm phụ đề mới và xuất video hoàn chỉnh.

Backend mặc định là `CapCapTTS/backend_cpu` (CPU, Piper). TTS và xử lý media chạy local; **chỉ tải video, tải model và dịch máy cần Internet**. Không cần API key cho TTS hay dịch Google.

> **Lưu ý về phiên bản:** bản CPU chỉ hỗ trợ Piper. F5-TTS và OmniVoice nằm trong `CapCapTTS/backend/` (bản GPU) và không hoạt động trên backend CPU. Voice cloning cũng chỉ có ở bản GPU; xem hướng dẫn riêng trong [CapCapTTS/README.md](CapCapTTS/README.md).

## Mục lục

- [Thành phần trong repo](#thành-phần-trong-repo)
- [Tính năng](#tính-năng)
- [Kiến trúc](#kiến-trúc)
- [Yêu cầu hệ thống](#yêu-cầu-hệ-thống)
- [Cài đặt](#cài-đặt)
- [Cấu hình](#cấu-hình)
- [Chạy ứng dụng](#chạy-ứng-dụng)
- [Hướng dẫn sử dụng](#hướng-dẫn-sử-dụng)
- [API chính](#api-chính)
- [Dữ liệu được tạo ra](#dữ-liệu-được-tạo-ra)
- [Giới hạn hiện tại](#giới-hạn-hiện-tại)
- [Xử lý sự cố](#xử-lý-sự-cố)
- [Bảo mật và quyền riêng tư](#bảo-mật-và-quyền-riêng-tư)
- [Giấy phép và ghi công](#giấy-phép-và-ghi-công)

## Thành phần trong repo

| Thư mục | Vai trò |
|---|---|
| `CapCapTTS/` | Web app chính: FastAPI backend, frontend SPA, launcher và tài nguyên Piper. Xem thêm [CapCapTTS/README.md](CapCapTTS/README.md). |
| `CapCapTTS/backend_cpu/` | Backend CPU (Piper), đồng thời chứa toàn bộ pipeline video đã tích hợp. |
| `CapCapTTS/backend/` | Biến thể GPU của CapCapTTS (Piper + F5-TTS + OmniVoice). Không phải backend mặc định. |
| `CapCapTTS/frontend/` | Giao diện HTML/CSS/JavaScript thuần, không cần bước build. |
| `CapCapTTS/resources/piper/` | Cấu hình giọng Piper (`.onnx.json`) và `voices.json`. File `.onnx` không được commit. |
| `ai-video-downloader-main/` | Bộ Claude Code skills độc lập (`/video-download`, `/voice-over`, `/douyin-crawler`). Đây là nguồn gốc của package `video_dl` được vendored vào app. |
| `VIDEO_INTEGRATION_PLAN.md` | Ghi chú lịch sử của quá trình tích hợp video. Nội dung có thể đã lỗi thời so với code hiện tại. |

## Tính năng

### Tạo giọng đọc (TTS)

- **14 giọng Piper tiếng Việt** với thông tin giới tính, mô tả và preview.
- Nhập trực tiếp, dán văn bản hoặc kéo thả tệp `.txt` / `.md`.
- **Tối đa 5.000 ký tự mỗi lần tạo**, tự động lưu bản nháp vào trình duyệt.
- **Hàng đợi tối đa 20 tệp**: cấu hình riêng cho từng tệp, áp dụng cấu hình cho tất cả, xử lý lần lượt.
- Ba chế độ chia đoạn: mặc định (đoạn + câu), theo câu, theo đoạn.
- Tùy chọn chuẩn hóa tiếng Việt, tự động làm sạch văn bản và chuẩn hóa âm lượng.
- Điều chỉnh tốc độ, âm lượng; chèn khoảng nghỉ bằng marker dạng `[1s]`.
- **Kiểm tra chất lượng từng đoạn**: phát hiện audio quá ngắn, im lặng bất thường, âm lượng thấp, clipping; cho phép tạo lại đoạn lỗi.
- Nghe lại, tải từng đoạn WAV, tải tất cả dưới dạng ZIP hoặc ghép thành một tệp.
- Xuất **MP3 128 kbps, MP3 320 kbps, WAV và SRT**.
- Từ điển phát âm cho từ viết tắt và từ nước ngoài.
- Cấu hình khoảng nghỉ sau dấu câu; lịch sử 30 lần tạo gần nhất.
- Giao diện tối, có thể bật/tắt và ghi nhớ lựa chọn.

### Xử lý video

- **Tải video** từ Douyin, YouTube, TikTok, Facebook, Instagram, Bilibili và các trang khác mà `yt-dlp` hỗ trợ.
- Douyin dùng crawler Playwright cho link video, link rút gọn, feed `jingxuan` và trang người dùng.
- Giới hạn **tối đa 20 video mỗi job** đối với feed/playlist.
- Tùy chọn hiện trình duyệt khi gặp captcha hoặc yêu cầu đăng nhập.
- **Thư viện video** cục bộ: lưu metadata, thumbnail, trạng thái xử lý; mở thư mục hoặc xóa video.
- **Phiên âm** bằng `faster-whisper` trên CPU với các model `tiny`, `base`, `small`, `medium`, `large`, `large-v3-turbo`.
- **Dịch sang tiếng Việt** qua Google Translate (thư viện `deep-translator`), không cần API key.
- **Chỉnh sửa từng đoạn** của bản gốc và bản dịch: thời gian bắt đầu/kết thúc và nội dung.
- **Lồng tiếng tự động** bằng Piper:
  - Trộn với audio gốc, thay thế audio gốc, hoặc chỉ giữ audio lồng tiếng.
  - Điều chỉnh âm lượng audio gốc và audio lồng tiếng.
  - Giới hạn tốc độ TTS để khớp timeline (tối đa 1,5×).
  - Chuẩn hóa âm lượng, tùy chọn burn phụ đề tiếng Việt.
  - Lưu nhiều phiên bản lồng tiếng cho cùng một video.
- **Chỉnh video (compose)** bằng FFmpeg:
  - Chuẩn hóa khung hình, fit `contain` trên canvas 1080×1920.
  - Tối đa 10 vùng che phụ đề cũ, kiểu làm mờ (blur) hoặc hộp che (box).
  - Thêm phụ đề ASS với vị trí, cỡ chữ, màu chữ, viền, nền và khoảng cách tùy chỉnh.
  - Xem trước khung hình và xuất video từ nguồn gốc hoặc từ bản đã lồng tiếng.

## Kiến trúc

```text
Trình duyệt (SPA: HTML/CSS/JS)
          │  HTTP + JSON
          ▼
FastAPI  (CapCapTTS/backend_cpu/main.py)
          │
          ├─ TTS ......... Piper + vietnormalizer + pydub + FFmpeg
          ├─ Download .... Playwright crawler (Douyin) / yt-dlp
          ├─ Transcribe .. faster-whisper (CPU, int8)
          ├─ Translate ... deep-translator (Google)
          ├─ Dub ......... Piper + FFmpeg
          └─ Compose ..... FFmpeg: fit → masks → phụ đề ASS
```

- FastAPI phục vụ API dưới `/tts/*` và `/api/videos/*`, đồng thời mount thư mục `frontend/` tại `/`.
- Các tác vụ dài (tạo audio, tải video, phiên âm, dịch, lồng tiếng, compose) chạy nền và được theo dõi qua job/task ID.
- Job của video được lưu **trong bộ nhớ**; khởi động lại server sẽ mất trạng thái job đang chạy.
- Không có cơ sở dữ liệu: thư viện video dùng `library.json`, từ điển dùng CSV, lịch sử TTS dùng JSON.

## Yêu cầu hệ thống

| Thành phần | Yêu cầu |
|---|---|
| Hệ điều hành | Windows 10/11 là môi trường được hỗ trợ chính. Linux/macOS có thể chạy nhưng cần tự điều chỉnh đường dẫn FFmpeg và script khởi chạy. |
| Python | 3.10 trở lên; khuyến nghị 3.11 hoặc 3.12. |
| FFmpeg | Bắt buộc. Cần cả `ffmpeg` và `ffprobe`. |
| RAM | Tối thiểu khoảng 4 GB cho TTS và Whisper `base`; model Whisper lớn hơn cần nhiều RAM hơn. |
| GPU | Không bắt buộc với backend CPU. |
| Internet | Chỉ cần khi tải video hoặc dịch máy. TTS, phiên âm, lồng tiếng và compose chạy offline. |
| Trình duyệt | Chromium do Playwright quản lý, dùng cho crawler Douyin. |

## Cài đặt

### 1. Clone repo

```bash
git clone https://github.com/HoLoi/LamVideo.git
cd LamVideo
```

### 2. Tạo môi trường Python

**Windows (PowerShell):**

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r CapCapTTS\backend_cpu\requirements.txt
```

Nếu PowerShell chặn script kích hoạt, dùng Command Prompt:

```bat
py -3.11 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r CapCapTTS\backend_cpu\requirements.txt
```

**Linux/macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r CapCapTTS/backend_cpu/requirements.txt
```

### 3. Cài FFmpeg

**Windows:**

```powershell
winget install Gyan.FFmpeg
```

Mở terminal mới sau khi cài, sau đó kiểm tra:

```powershell
ffmpeg -version
ffprobe -version
```

> **Quan trọng:** `tts_engine.py` dùng trực tiếp `FFMPEG_DIR\ffmpeg.exe` và `FFMPEG_DIR\ffprobe.exe`, không chỉ tìm trong `PATH`. Chọn một trong hai cách sau:
>
> 1. Chép `ffmpeg.exe` và `ffprobe.exe` vào `CapCapTTS/ffmpeg/bin/` (đường dẫn mặc định).
> 2. Đặt `FFMPEG_DIR` trỏ tới thư mục chứa hai file này **trước khi khởi động app**:

```powershell
$env:FFMPEG_DIR = (Split-Path (Get-Command ffmpeg).Source)
$env:FFMPEG_PATH = (Get-Command ffmpeg).Source
$env:PATH = "$env:FFMPEG_DIR;$env:PATH"
```

`FFMPEG_PATH` là tùy chọn cho pipeline video; nếu không đặt, ứng dụng tự tìm trong `PATH` rồi tới Playwright bundle.

**Linux/macOS:**

```bash
sudo apt install ffmpeg   # Debian/Ubuntu
brew install ffmpeg       # macOS
```

Backend hiện giả định tên file `ffmpeg.exe`/`ffprobe.exe` của Windows. Trên Linux/macOS, tạo symlink trong `FFMPEG_DIR` hoặc chỉnh đường dẫn trong `tts_engine.py`.

### 4. Cài Chromium cho Playwright

```bash
python -m playwright install chromium
```

Bước này cần thiết cho crawler Douyin. Các nền tảng dùng `yt-dlp` không cần Chromium.

### 5. Tải model giọng Piper

Repo **không chứa file trọng số `.onnx`** (đã bị `.gitignore` loại trừ). Chỉ có file cấu hình `.onnx.json` và `voices.json`.

**Cách khuyến nghị:** chạy ứng dụng, mở **Resources → Download → Piper Voices**. Ứng dụng tải khoảng 848 MB model vào `PIPER_DIR` và bỏ qua các file đã có. Khi dùng launcher, `PIPER_DIR` mặc định là `CapCapTTS/models/piper`; khi chạy trực tiếp backend, mặc định là `CapCapTTS/resources/piper`.

**Hoặc tải thủ công:** tải các cặp `*.onnx` + `*.onnx.json` và `voices.json` từ:

- <https://huggingface.co/Hacht/CapCapResource/tree/main/piper>

Đặt tất cả vào `CapCapTTS/resources/piper/`. Đây là thư mục fallback, nên cách này dùng được cho cả launcher và khi chạy trực tiếp backend.

Mỗi giọng cần đủ hai file cùng tên, ví dụ:

```text
banmai.onnx
banmai.onnx.json
```

Có thể đổi thư mục chứa giọng bằng biến môi trường `PIPER_DIR`. Nếu `PIPER_DIR` không tồn tại hoặc không có file `.onnx`, ứng dụng tự fallback về `CapCapTTS/resources/piper/`.

## Cấu hình

Các biến môi trường hữu ích:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `PIPER_DIR` | `CapCapTTS/resources/piper` | Thư mục chứa model Piper. Launcher đặt thành `CapCapTTS/models/piper` và tự fallback về `resources/piper` nếu thư mục đó chưa có `.onnx`. |
| `FFMPEG_DIR` | `CapCapTTS/ffmpeg/bin` | Thư mục chứa `ffmpeg`/`ffprobe` cho TTS và xử lý audio. |
| `FFMPEG_PATH` | Không đặt | Đường dẫn `ffmpeg` hoặc thư mục `bin`; ưu tiên khi xử lý video. |
| `VIDEO_LIBRARY_DIR` | `CapCapTTS/data/videos` | Nơi lưu thư viện video. |
| `VIDEO_TMP_DIR` | `CapCapTTS/data/tmp` | Thư mục tạm khi tải video. |
| `PIPER_WORKERS` | `min(4, số CPU)` | Số luồng tổng hợp Piper. |
| `AUDIO_IO_WORKERS` | `2` | Số luồng đọc/ghi và ghép audio. |

Các giá trị cố định trong code:

- `MAX_TEXT_LENGTH = 5000` ký tự cho mỗi lần tạo TTS.
- `MAX_DOWNLOAD_ITEMS = 20` video cho mỗi job tải.
- `MAX_HISTORY = 30` mục lịch sử TTS.
- Tối đa 10 vùng che phụ đề trong compose.

## Chạy ứng dụng

### Cách 1: dùng launcher

```bash
python CapCapTTS/launcher.py
```

Launcher chọn `backend_cpu` nếu có, khởi động Uvicorn tại `0.0.0.0:8000` và tự mở trình duyệt.

### Cách 2: chạy trực tiếp backend

```bash
cd CapCapTTS/backend_cpu
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Sau đó mở <http://localhost:8000>. Dùng `--reload` khi phát triển giao diện/backend.

> Không mở trực tiếp `index.html` bằng giao thức `file://`; ứng dụng cần được phục vụ qua backend.

## Hướng dẫn sử dụng

### Tạo giọng đọc

1. Dán văn bản hoặc kéo thả tệp `.txt` / `.md` vào khung nhập.
2. Chọn giọng Piper, có thể bật **Vietnamese normalize**, **Auto clean text** và **Normalize volume**.
3. Bấm **Preview** để nghe thử một câu, hoặc **Generate** để tạo toàn bộ.
4. Theo dõi tiến trình; mỗi đoạn có trạng thái chất lượng riêng. Đoạn lỗi có thể tạo lại hoặc đổi giọng.
5. Bấm **Merge & Download All** để ghép và tải kết quả, hoặc tải từng đoạn WAV / toàn bộ ZIP.

Các công cụ hỗ trợ:

- **Dict**: thêm cách phát âm cho từ viết tắt và từ nước ngoài.
- **Pause**: chỉnh khoảng lặng sau dấu câu và khoảng nghỉ giữa các dòng.
- **History**: nghe lại và tải các lần tạo trước.
- **Resources**: tải giọng Piper và kiểm tra trạng thái model.

### Xử lý video

1. Mở card **Download Video**, dán link và chọn nguồn (hoặc để **Auto**).
2. Với link feed/playlist, đặt số lượng video tối đa (1–20). Giữ **headless** nếu không cần xem trình duyệt.
3. Bấm **Download**; video xuất hiện trong **Video Library**.
4. Chọn video để mở workspace 5 bước:
   - **Phụ đề & bản dịch**: chạy Transcribe, dịch sang tiếng Việt, sửa từng đoạn.
   - **Lồng tiếng**: chọn giọng, chế độ audio, âm lượng, tốc độ tối đa; tạo và xem trước bản lồng tiếng.
   - **Chỉnh video**: bật che phụ đề cũ, thêm phụ đề tiếng Việt, xem trước và xuất.
   - **Video đã tạo**: quản lý các bản lồng tiếng và bản export.
   - **Xuất video**: chạy lại compose trên nguồn đang chọn và mở thư mục kết quả.

## API chính

Tài liệu OpenAPI tự động có tại <http://localhost:8000/docs>.

| Nhóm | Endpoint tiêu biểu | Mô tả |
|---|---|---|
| TTS | `GET /tts/voices` | Danh sách giọng Piper. |
| TTS | `POST /tts/preview` | Tạo audio nghe thử. |
| TTS | `POST /tts/generate` | Bắt đầu tạo audio; trả về `task_id`. |
| TTS | `GET /tts/status/{task_id}` | Tiến trình và trạng thái từng đoạn. |
| TTS | `POST /tts/regenerate_chunk` | Tạo lại một đoạn. |
| TTS | `POST /tts/merge` | Ghép các đoạn thành file cuối. |
| TTS | `GET /tts/download_file` | Tải MP3/WAV/SRT. |
| TTS | `GET/POST/DELETE /tts/dict/*` | Quản lý từ điển phát âm. |
| TTS | `GET/POST /tts/pause_config` | Cấu hình khoảng nghỉ. |
| TTS | `GET/DELETE /tts/history` | Lịch sử tạo audio. |
| Video | `POST /api/videos/download` | Bắt đầu job tải video. |
| Video | `GET /api/videos/download/{job_id}` | Theo dõi job tải. |
| Video | `GET /api/videos` | Danh sách video trong thư viện. |
| Video | `POST /api/videos/{id}/transcribe` | Bắt đầu phiên âm. |
| Video | `POST /api/videos/{id}/translate` | Bắt đầu dịch sang tiếng Việt. |
| Video | `GET /api/videos/{id}/segments` | Đọc các đoạn phụ đề gốc và bản dịch. |
| Video | `PUT /api/videos/{id}/segments/{index}` | Sửa thời gian/nội dung một đoạn. |
| Video | `POST /api/videos/{id}/send-to-tts` | Đưa transcript sang khung TTS. |
| Video | `POST /api/videos/{id}/dub` | Bắt đầu lồng tiếng. |
| Video | `GET /api/videos/dub/{job_id}` | Theo dõi job lồng tiếng. |
| Video | `POST /api/videos/{id}/compose` | Bắt đầu compose video. |
| Video | `GET /api/videos/compose/{job_id}` | Theo dõi job compose. |
| Video | `GET /api/videos/{id}/exports` | Danh sách bản export. |

Các job video dùng chung dạng phản hồi:

```json
{
  "job_id": "dl_...",
  "status": "running",
  "progress": 42,
  "message": "Đang tải (yt-dlp, youtube)...",
  "video_ids": [],
  "error": ""
}
```

## Dữ liệu được tạo ra

```text
CapCapTTS/
├─ backend_cpu/
│  ├─ outputs/<task_id>/          # Audio TTS: chunk_*.wav, final.mp3, final.srt
│  └─ custom_dict/                # Từ điển, _pause.json, _history.json
├─ data/
│  ├─ tmp/<job_id>/               # Staging khi tải video
│  └─ videos/
│     └─ <video_id>/
│        ├─ index.mp4
│        ├─ metadata.json
│        ├─ transcript.json
│        ├─ transcript.txt
│        ├─ transcript_vi.json
│        ├─ transcript_vi.txt
│        ├─ dub/
│        │  ├─ segments/
│        │  └─ outputs/<dub_id>/  # output.mp4, final_dub.wav, .srt, plan, metadata
│        └─ exports/<export_id>/  # output.mp4, composer_plan.json
└─ resources/piper/               # Model giọng Piper
```

Toàn bộ video, audio, ảnh và file trọng số `.onnx` đều nằm trong `.gitignore`. Các file cấu hình `.onnx.json` và `voices.json` vẫn được track. Không commit dữ liệu runtime.

## Giới hạn hiện tại

- **Backend CPU chỉ có Piper**; không có F5-TTS, OmniVoice hoặc voice cloning.
- **Dịch máy chỉ hỗ trợ Google** qua `deep-translator`; cần Internet và có thể bị giới hạn tần suất.
- **Không dùng cookie/tài khoản**: chỉ tải được video công khai. Video riêng tư hoặc yêu cầu đăng nhập sẽ thất bại.
- **Không tách giọng nói khỏi nhạc nền**. Chế độ mix chỉ giảm âm lượng toàn bộ audio gốc, không thể giữ nhạc nền mà bỏ giọng gốc.
- **Không có xác thực người dùng**. Server dành cho một người dùng hoặc mạng nội bộ tin cậy.
- Job video lưu trong bộ nhớ; khởi động lại server sẽ làm mất trạng thái các job đang chạy.
- Compose hiện hỗ trợ fit khung hình, vùng che và phụ đề ASS; các trường logo/trim có trong model request nhưng **chưa được bật trên giao diện của bản đang chạy**.
- Chưa có Docker, CI hoặc bộ test tự động trong repo.

## Xử lý sự cố

| Triệu chứng | Cách xử lý |
|---|---|
| `ffmpeg not found` hoặc không xuất được MP3 | Cài FFmpeg; đặt `FFMPEG_DIR` trỏ tới thư mục chứa `ffmpeg.exe`/`ffprobe.exe` (hoặc chép hai file vào `CapCapTTS/ffmpeg/bin/`), đặt `FFMPEG_PATH`, rồi mở lại terminal. |
| `Voice '...' not found` | Kiểm tra đã có cặp `.onnx` + `.onnx.json` trong `PIPER_DIR`; tên file phải khớp nhau. |
| Không tải được Douyin | Chạy `python -m playwright install chromium`; thử bỏ tick **headless**; kiểm tra video công khai. |
| YouTube báo 403 | Cập nhật `yt-dlp` (`pip install -U yt-dlp`) và thử lại. |
| Thiếu `faster-whisper` / `yt-dlp` / `deep-translator` | Chạy lại `pip install -r CapCapTTS/backend_cpu/requirements.txt`. |
| Dịch lỗi hoặc treo | Kiểm tra Internet; Google Translate có thể giới hạn tạm thời, thử lại sau. |
| Whisper hết RAM hoặc chạy quá lâu | Dùng model nhỏ hơn (`tiny`, `base`, `small`) hoặc video ngắn hơn. |
| Mở `index.html` bị báo lỗi | Không dùng `file://`; chạy server và mở `http://localhost:8000`. |
| Port 8000 đang bận | Chạy Uvicorn với `--port 8001` và mở đúng cổng mới. |
| Giao diện không thấy giọng | Mở **Resources → Download** để tải model Piper, sau đó tải lại trang. |

## Bảo mật và quyền riêng tư

- TTS, phiên âm, lồng tiếng và compose xử lý **cục bộ**; văn bản và media không được gửi tới dịch vụ TTS bên ngoài.
- **Tải video** gửi URL tới nền tảng tương ứng.
- **Dịch máy** gửi nội dung transcript tới Google Translate.
- Server không có đăng nhập và CORS đang mở cho mọi origin. Không mở cổng ra Internet công khai.
- Launcher mặc định bind `0.0.0.0`. Nếu chỉ dùng trên máy cá nhân, chạy Uvicorn với `--host 127.0.0.1` hoặc giới hạn bằng firewall.
- Không đưa API key, cookie, model weights hoặc dữ liệu video cá nhân vào Git.

## Giấy phép và ghi công

- `CapCapTTS/` phát hành theo **Apache License 2.0** — xem [CapCapTTS/LICENSE](CapCapTTS/LICENSE).
- `ai-video-downloader-main/` phát hành theo **MIT License** — xem [ai-video-downloader-main/LICENSE](ai-video-downloader-main/LICENSE).
- Repo gốc không có file giấy phép riêng; khi tái sử dụng, tuân theo giấy phép của từng thành phần.

Các dự án và tài nguyên được sử dụng:

- [Piper](https://github.com/rhasspy/piper) — TTS local.
- [vietnormalizer](https://github.com/nghimestudio/vietnormalizer) — chuẩn hóa tiếng Việt.
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — phiên âm.
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — tải video.
- [Playwright](https://playwright.dev/) — crawler Douyin.
- [deep-translator](https://github.com/nidhaloff/deep-translator) — dịch qua Google Translate.
- [CapCapResource](https://huggingface.co/Hacht/CapCapResource) — model giọng Piper.
- [F5-TTS-Vietnamese](https://github.com/nguyenthienhy/F5-TTS-Vietnamese) và [OmniVoice](https://huggingface.co/kjanh/KhanhTTS-OmniVoice) — các engine chất lượng cao cho bản GPU.
