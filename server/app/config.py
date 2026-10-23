import os
import socket
from pathlib import Path


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).resolve()


DATA_DIR = _env_path("PROJECTOR_DATA_DIR", str(Path(__file__).resolve().parents[2] / "data"))
WORK_DIR = _env_path("PROJECTOR_WORK_DIR", "D:/ProjectorAppData" if os.name == "nt" else "/work")
LEGACY_MEDIA_DIR = DATA_DIR / "media"
MEDIA_DIR = WORK_DIR / "media"
SOURCE_ROOT = _env_path("PROJECTOR_SOURCE_ROOT", "D:/AtmosFXVids" if os.name == "nt" else "/sources/atmosfx")
LIBRARY_DIR = WORK_DIR / "library"
PACKS_DIR = WORK_DIR / "source-packs"
GENERATION_DIR = WORK_DIR / "generation"
ORIGINALS_DIR = MEDIA_DIR / "originals"
RENDITIONS_DIR = MEDIA_DIR / "renditions"
THUMBS_DIR = MEDIA_DIR / "thumbs"
IMPORT_DIR = DATA_DIR / "import"
CALIBRATION_DIR = DATA_DIR / "calibration"
APK_DIR = DATA_DIR / "apk"
DB_PATH = DATA_DIR / "projector.db"

WEB_DIST = _env_path("PROJECTOR_WEB_DIST", str(Path(__file__).resolve().parents[2] / "web" / "dist"))
COMFY_TEMPLATES_DIR = Path(__file__).resolve().parent / "comfy" / "templates"

PORT = int(os.environ.get("PROJECTOR_PORT", "8000"))
# Host the players and phone use to reach the server. Must be the LAN IP of the
# Docker host when running in Docker, since the container can't see it.
PUBLIC_HOST = os.environ.get("PROJECTOR_PUBLIC_HOST", "")

SCHEDULE_HORIZON_MS = 15 * 60 * 1000
START_LEAD_MS = 3000

for d in (ORIGINALS_DIR, RENDITIONS_DIR, THUMBS_DIR, IMPORT_DIR, CALIBRATION_DIR, APK_DIR, LIBRARY_DIR, PACKS_DIR, GENERATION_DIR):
    d.mkdir(parents=True, exist_ok=True)


def lan_ip() -> str:
    if PUBLIC_HOST:
        return PUBLIC_HOST
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def public_url() -> str:
    return f"http://{lan_ip()}:{PORT}"
