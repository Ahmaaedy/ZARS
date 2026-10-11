import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, manifest_for
from core.skill_utils import safe_emit

VALID_MODES = {"full", "active_window", "region"}


def _capture_full():
    from PIL import ImageGrab
    return ImageGrab.grab()


def _capture_region(x, y, w, h):
    from PIL import ImageGrab
    return ImageGrab.grab(bbox=(x, y, x + w, y + h))


def _ocr_image(image_path, lang):
    import asyncio
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.storage import StorageFile

    async def _run():
        file = await StorageFile.get_file_from_path_async(str(image_path))
        stream = await file.open_read_async()
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()

        engine = OcrEngine.try_create_from_language(Language(lang))
        if engine is None:
            return None, f"OCR engine not available for language: {lang}"
        result = await engine.recognize_async(bitmap)
        return result.text, None

    return asyncio.run(_run())


def _clipboard_write(text):
    import pyperclip
    pyperclip.copy(text)


def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    if not ns.mode:
        safe_emit(False, error="mode is required")
    mode = ns.mode.lower().strip()
    if mode not in VALID_MODES:
        safe_emit(False, error=f"unknown mode: {mode}. valid: {', '.join(sorted(VALID_MODES))}")

    if mode == "region":
        if not ns.region:
            safe_emit(False, error="region param required for region mode (x,y,w,h)")
        try:
            parts = [int(p.strip()) for p in ns.region.split(",")]
            if len(parts) != 4:
                raise ValueError
            x, y, w, h = parts
        except (ValueError, AttributeError):
            safe_emit(False, error="region format: x,y,w,h (integers)")

    lang = ns.lang or "en-US"

    try:
        if mode == "full":
            image = _capture_full()
        elif mode == "active_window":
            safe_emit(False, error="active_window mode not yet implemented on Windows")
        else:
            image = _capture_region(x, y, w, h)
    except Exception as e:
        safe_emit(False, error=f"capture failed: {e}")

    tmp_dir = Path(tempfile.gettempdir()) / "zars_ocr"
    tmp_dir.mkdir(exist_ok=True)
    image_path = tmp_dir / f"ocr_{time.strftime('%Y%m%d_%H%M%S')}.png"
    image.save(str(image_path))

    ocr_text, err = _ocr_image(image_path, lang)
    if err:
        safe_emit(False, error=err, status="unsupported")

    if not ns.keep_image:
        try:
            image_path.unlink()
        except OSError:
            pass
        image_path = None

    if not ocr_text or not ocr_text.strip():
        safe_emit(True, {"mode": mode, "extracted_text": "", "image_path": str(image_path) if image_path else None},
                  status="empty", display_text="no text detected on screen")

    ocr_text = ocr_text.strip()

    clipboard_ok = True
    try:
        _clipboard_write(ocr_text)
    except Exception:
        clipboard_ok = False

    preview = ocr_text[:500] + "..." if len(ocr_text) > 500 else ocr_text
    data = {
        "mode": mode,
        "extracted_text": preview,
        "full_length": len(ocr_text),
        "image_path": str(image_path) if image_path else None,
        "clipboard_status": "ok" if clipboard_ok else "failed",
    }
    status = "ok" if clipboard_ok else "partial"
    display = f"extracted {len(ocr_text)} chars"
    if not clipboard_ok:
        display += " (clipboard write failed)"
    safe_emit(True, data, status=status, display_text=display)


if __name__ == "__main__":
    main()
