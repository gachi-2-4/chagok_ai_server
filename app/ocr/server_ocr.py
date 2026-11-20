"""서버 OCR (선택 설치: requirements-ocr.txt).

경량 모델(PaddleOCR) + 긴 변 2,000px 축소. 설치되지 않았으면 available() 이 False 입니다.
"""

import io
import logging
from functools import lru_cache

from app.ocr.models import OcrBlock, OcrPage

logger = logging.getLogger("chagok.ocr")

MAX_SIDE = 2000


@lru_cache
def _engine():
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except ImportError:
        return None
    try:
        return PaddleOCR(lang="korean")
    except Exception as exc:  # noqa: BLE001
        logger.warning("PaddleOCR init failed: %s", exc)
        return None


def available() -> bool:
    return _engine() is not None


def _resize(image):
    w, h = image.size
    scale = MAX_SIDE / max(w, h)
    if scale < 1:
        image = image.resize((int(w * scale), int(h * scale)))
    return image


def _box_to_bbox(points, width: int, height: int) -> list[float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    return [round(x0 / width, 4), round(y0 / height, 4), round((x1 - x0) / width, 4), round((y1 - y0) / height, 4)]


def ocr_image(image_bytes: bytes, page_number: int) -> OcrPage | None:
    engine = _engine()
    if engine is None:
        return None
    import numpy as np
    from PIL import Image

    try:
        import pillow_heif  # type: ignore

        pillow_heif.register_heif_opener()
    except ImportError:
        pass

    image = _resize(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
    width, height = image.size
    array = np.array(image)

    blocks: list[OcrBlock] = []
    if hasattr(engine, "predict"):  # PaddleOCR 3.x
        for res in engine.predict(array):
            data = res.json.get("res", res.json) if hasattr(res, "json") else res
            for text, score, poly in zip(data.get("rec_texts", []), data.get("rec_scores", []), data.get("rec_polys", [])):
                blocks.append(OcrBlock(text=text, bbox=_box_to_bbox(poly, width, height), confidence=float(score)))
    else:  # PaddleOCR 2.x
        for line in (engine.ocr(array, cls=True) or [[]])[0] or []:
            points, (text, score) = line
            blocks.append(OcrBlock(text=text, bbox=_box_to_bbox(points, width, height), confidence=float(score)))

    text = "\n".join(b.text for b in blocks)
    return OcrPage(page=page_number, text=text, blocks=blocks)
