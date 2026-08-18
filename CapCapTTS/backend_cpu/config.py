from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def _env_path(key: str, default: str) -> Path:
    return Path(os.environ.get(key, default))


def _piper_dir() -> Path:
    """PIPER_DIR ưu tiên env, nhưng fallback về resources/piper nếu dir env rỗng/không tồn tại.

    launcher.py set PIPER_DIR=models/piper (thư mục trống) → nếu theo env tuyệt đối sẽ
    "Voice 'x' not found" dù voice đã cài ở resources/piper (config default).
    """
    p = _env_path("PIPER_DIR", str(_RESOURCE_DIR / "piper"))
    if not p.exists() or not any(p.glob("*.onnx")):
        alt = _RESOURCE_DIR / "piper"
        if alt != p and alt.exists() and any(alt.glob("*.onnx")):
            print(f"[config] PIPER_DIR {p} không có voice — dùng {alt}")
            return alt
    return p


_RESOURCE_DIR = PROJECT_ROOT / "resources"

PIPER_DIR = _piper_dir()
OUTPUT_DIR = PROJECT_ROOT / "backend_cpu" / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

FFMPEG_DIR = _env_path("FFMPEG_DIR", str(PROJECT_ROOT / "ffmpeg" / "bin"))

MAX_TEXT_LENGTH = 5000
PIPER_SAMPLE_RATE = 22050
CROSS_FADE_MS = 50

# ─── Video Library (MVP-1) ───
VIDEO_LIBRARY_DIR = _env_path("VIDEO_LIBRARY_DIR", str(PROJECT_ROOT / "data" / "videos"))
VIDEO_TMP_DIR = _env_path("VIDEO_TMP_DIR", str(PROJECT_ROOT / "data" / "tmp"))
VIDEO_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_TMP_DIR.mkdir(parents=True, exist_ok=True)
MAX_VIDEO_ITEMS = 50
