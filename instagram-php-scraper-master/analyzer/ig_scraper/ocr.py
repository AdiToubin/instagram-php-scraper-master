"""
OCR helpers - Python port of the Tesseract/ffmpeg calls in
stories_with_stickers.php (tesseractAvailable/runTesseract and the ffmpeg
frame-grab block). No-ops whenever TESSERACT_PATH isn't set, exactly like
the PHP version gates its whole OCR block on tesseractAvailable().

Untested against a live run in this environment - OCR is currently disabled
(TESSERACT_PATH/FFMPEG_PATH are commented out in .env), so this mirrors the
PHP logic structurally but has not been shadow-validated yet.
"""

import os
import subprocess
import tempfile
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

TESSERACT_PATH = os.getenv("TESSERACT_PATH", "")
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "")
OCR_LANGS = os.getenv("OCR_LANGS", "heb+eng")


def tesseract_available() -> bool:
    return bool(TESSERACT_PATH) and os.path.isfile(TESSERACT_PATH)


def _download_to_temp(url: str, suffix: str) -> Optional[str]:
    try:
        resp = requests.get(url, timeout=30, stream=True)
        if resp.status_code != 200:
            return None
        fd, path = tempfile.mkstemp(suffix=suffix, prefix="ig_")
        with os.fdopen(fd, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        return path
    except Exception:
        return None


def run_tesseract(image_path: str, langs: Optional[str] = None) -> Optional[str]:
    if not tesseract_available():
        return None
    out_base = image_path + ".out"
    try:
        subprocess.run(
            [TESSERACT_PATH, image_path, out_base, "-l", langs or OCR_LANGS],
            capture_output=True,
            timeout=60,
        )
        with open(out_base + ".txt", "r", encoding="utf-8", errors="replace") as f:
            text = f.read().strip()
        return text or None
    except Exception:
        return None
    finally:
        for ext in (".txt",):
            try:
                os.remove(out_base + ext)
            except OSError:
                pass


def ocr_image_url(image_url: str) -> Optional[str]:
    """Mirrors the imageUrl branch of stories_with_stickers.php's OCR block."""
    if not tesseract_available():
        return None
    img_path = _download_to_temp(image_url, ".jpg")
    if not img_path:
        return None
    try:
        return run_tesseract(img_path)
    finally:
        try:
            os.remove(img_path)
        except OSError:
            pass


def extract_video_frame_text(video_url: str, duration_ms: int) -> Optional[str]:
    """Mirrors the videoUrl branch: downloads the video, grabs one frame via
    ffmpeg at the same offset PHP computes (min(duration/2, 45s)), OCRs it."""
    if not tesseract_available() or not FFMPEG_PATH:
        return None
    video_path = _download_to_temp(video_url, ".mp4")
    if not video_path:
        return None
    frame_path = video_path + ".jpg"
    sec = max(1, min(int((duration_ms or 60000) / 1000 / 2), 45))
    try:
        subprocess.run(
            [FFMPEG_PATH, "-y", "-ss", str(sec), "-i", video_path, "-frames:v", "1", frame_path],
            capture_output=True,
            timeout=60,
        )
        if os.path.isfile(frame_path):
            return run_tesseract(frame_path)
        return None
    except Exception:
        return None
    finally:
        for p in (video_path, frame_path):
            try:
                os.remove(p)
            except OSError:
                pass
