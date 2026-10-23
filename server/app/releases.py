"""Validated metadata for the APK currently offered to linked players."""
import hashlib
import json
from functools import lru_cache
from .config import APK_DIR


@lru_cache(maxsize=4)
def _digest(path: str, size: int, modified: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def current_release() -> dict:
    apk = APK_DIR / "projector.apk"
    manifest = APK_DIR / "projector-release.json"
    if not apk.is_file() or not manifest.is_file():
        raise ValueError("Publish a signed APK with scripts/Publish-Apk.ps1 first")
    data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    st = apk.stat()
    sha = _digest(str(apk), st.st_size, st.st_mtime_ns)
    if data.get("package") != "com.projector.app" or type(data.get("version_code")) is not int or data["version_code"] < 1:
        raise ValueError("Invalid APK release metadata")
    if sha != data.get("sha256") or st.st_size != data.get("size"):
        raise ValueError("APK and release metadata differ; publish the APK again")
    return {"package": data["package"], "version_code": data["version_code"],
            "version_name": data.get("version_name", ""), "sha256": sha, "size": st.st_size,
            "url": f"/download/app.apk?sha256={sha}"}
