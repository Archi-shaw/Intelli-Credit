"""OCR provider abstraction.

* ``tesseract`` – uses the Tesseract binary through pytesseract when it is installed (Docker image).
* ``rapidocr``  – pure-pip ONNX OCR engine bundled with its models; used when Tesseract is absent.
* ``none``      – OCR disabled; scanned pages are reported as unreadable instead of silently skipped.
"""
from __future__ import annotations

import io
import logging
import shutil
import threading
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger("intellicredit.ocr")


@dataclass
class OCRResult:
    text: str
    confidence: float  # 0..1 mean word/line confidence
    engine: str


class OCREngine:
    name = "base"

    def image_to_text(self, image_bytes: bytes) -> OCRResult:  # pragma: no cover - interface
        raise NotImplementedError


class TesseractEngine(OCREngine):
    name = "tesseract"

    def __init__(self, lang: str = "eng"):
        import pytesseract  # noqa: F401

        self.lang = lang

    @staticmethod
    def available() -> bool:
        return shutil.which("tesseract") is not None

    def image_to_text(self, image_bytes: bytes) -> OCRResult:
        import pytesseract
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))
        data = pytesseract.image_to_data(img, lang=self.lang, config="--psm 6", output_type=pytesseract.Output.DICT)
        lines: dict[tuple, list[tuple[int, str]]] = {}
        confs = []
        for i, word in enumerate(data["text"]):
            if not word.strip():
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines.setdefault(key, []).append((data["left"][i], word))
            try:
                c = float(data["conf"][i])
                if c >= 0:
                    confs.append(c / 100.0)
            except (TypeError, ValueError):
                pass
        text = "\n".join("  ".join(w for _, w in sorted(ws)) for _, ws in sorted(lines.items()))
        return OCRResult(text=text, confidence=sum(confs) / len(confs) if confs else 0.0, engine=self.name)


class RapidOCREngine(OCREngine):
    name = "rapidocr"

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR

        self._engine = RapidOCR()
        self._lock = threading.Lock()

    @staticmethod
    def available() -> bool:
        try:
            import rapidocr_onnxruntime  # noqa: F401

            return True
        except Exception:  # pragma: no cover - import failure path
            return False

    def image_to_text(self, image_bytes: bytes) -> OCRResult:
        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        with self._lock:
            result, _ = self._engine(np.array(img))
        if not result:
            return OCRResult(text="", confidence=0.0, engine=self.name)
        # result: [[box(4 points), text, score], ...] → rebuild visual lines by y-centre.
        items = []
        for box, text, score in result:
            ys = [p[1] for p in box]
            xs = [p[0] for p in box]
            items.append(((min(ys) + max(ys)) / 2, min(xs), max(ys) - min(ys), text, float(score)))
        items.sort(key=lambda t: (t[0], t[1]))
        lines: list[list[tuple]] = []
        for it in items:
            if lines and abs(lines[-1][0][0] - it[0]) <= max(6.0, 0.5 * it[2]):
                lines[-1].append(it)
            else:
                lines.append([it])
        text = "\n".join("  ".join(t[3] for t in sorted(line, key=lambda t: t[1])) for line in lines)
        conf = sum(t[4] for t in items) / len(items)
        return OCRResult(text=text, confidence=conf, engine=self.name)


@lru_cache(maxsize=4)
def _build(provider: str, lang: str) -> OCREngine | None:
    try:
        if provider in ("tesseract", "auto") and TesseractEngine.available():
            return TesseractEngine(lang)
        if provider == "tesseract":
            log.warning("OCR_PROVIDER=tesseract but the tesseract binary is not installed")
            return None
        if provider in ("rapidocr", "auto") and RapidOCREngine.available():
            return RapidOCREngine()
    except Exception as exc:  # pragma: no cover - environment specific
        log.error("OCR engine initialisation failed: %s", exc)
    return None


def get_ocr_engine(provider: str = "auto", lang: str = "eng") -> OCREngine | None:
    if provider == "none":
        return None
    return _build(provider, lang)


def ocr_status(provider: str = "auto") -> dict:
    tess = TesseractEngine.available()
    rapid = RapidOCREngine.available()
    active = None
    if provider != "none":
        if provider in ("tesseract", "auto") and tess:
            active = "tesseract"
        elif provider in ("rapidocr", "auto") and rapid:
            active = "rapidocr"
    return {"configured_provider": provider, "active_engine": active, "tesseract_installed": tess, "rapidocr_installed": rapid}
