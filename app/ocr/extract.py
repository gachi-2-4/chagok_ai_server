"""파일 → 페이지별 텍스트.

우선순위
1. 텍스트가 들어있는 PDF: 텍스트 레이어를 바로 읽음 (OCR 없음)
2. APP 기기 OCR 결과(ocrResult)가 있으면 사용, 신뢰도가 낮은 페이지만 서버에서 다시 읽음
3. 서버 OCR (설치된 경우)
"""

import json
import logging

from app.core.errors import AppError
from app.ocr import server_ocr
from app.ocr.models import OcrBlock, OcrDocument, OcrPage

logger = logging.getLogger("chagok.ocr")

ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "heic"}
LOW_CONFIDENCE = 0.6
MIN_TEXT_LENGTH = 15

NOT_RECOGNIZED = ("TEXT_NOT_RECOGNIZED", "문서에서 글자를 인식하지 못했습니다. 밝은 곳에서 다시 촬영해 주세요.")


def file_extension(filename: str | None) -> str:
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[1].lower()


def validate_file(filename: str | None, size: int, max_mb: int) -> str:
    ext = file_extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise AppError(400, "INVALID_FILE", "지원하지 않는 파일 형식입니다. (pdf, jpg, png, heic)")
    if size == 0:
        raise AppError(400, "INVALID_FILE", "빈 파일입니다.")
    if size > max_mb * 1024 * 1024:
        raise AppError(413, "FILE_TOO_LARGE", f"파일은 {max_mb}MB 이하만 업로드할 수 있습니다.")
    return ext


def parse_client_ocr(raw: str | None) -> dict[int, OcrPage]:
    """APP이 보낸 ocrResult(JSON 문자열) → {page: OcrPage}."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        pages = {}
        for p in data.get("pages", []):
            number = int(p["page"])
            blocks = [
                OcrBlock(text=b.get("text", ""), bbox=list(b.get("bbox") or [0, 0, 0, 0]), confidence=float(b.get("confidence", 1.0)))
                for b in p.get("blocks", [])
            ]
            text = p.get("text") or "\n".join(b.text for b in blocks)
            pages[number] = OcrPage(page=number, text=text, blocks=blocks)
        return pages
    except (ValueError, KeyError, TypeError) as exc:
        raise AppError(400, "INVALID_REQUEST", "ocrResult 형식이 올바르지 않습니다.") from exc


def _pick(client_page: OcrPage | None, image_bytes: bytes | None, number: int) -> OcrPage | None:
    """기기 OCR 결과를 쓰되, 신뢰도가 낮으면 서버 OCR로 다시 읽습니다."""
    if client_page and client_page.text.strip() and client_page.confidence >= LOW_CONFIDENCE:
        return client_page
    if image_bytes is not None and server_ocr.available():
        server_page = server_ocr.ocr_image(image_bytes, number)
        if server_page and server_page.text.strip():
            return server_page
    return client_page


def _pdf(data: bytes, client_pages: dict[int, OcrPage], max_pages: int) -> OcrDocument:
    import pymupdf

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise AppError(400, "INVALID_FILE", "PDF 파일을 열 수 없습니다.") from exc
    if doc.page_count > max_pages:
        raise AppError(400, "INVALID_FILE", f"PDF는 최대 {max_pages}페이지까지 분석할 수 있습니다.")

    pages: list[OcrPage] = []
    for index, page in enumerate(doc, start=1):
        width, height = page.rect.width, page.rect.height
        text = page.get_text("text")
        if len(text.strip()) >= MIN_TEXT_LENGTH:
            blocks = []
            for x0, y0, x1, y1, block_text, *_ in page.get_text("blocks"):
                if block_text.strip():
                    blocks.append(
                        OcrBlock(
                            text=block_text.strip(),
                            bbox=[round(x0 / width, 4), round(y0 / height, 4), round((x1 - x0) / width, 4), round((y1 - y0) / height, 4)],
                        )
                    )
            pages.append(OcrPage(page=index, text=text, blocks=blocks))
            continue
        # 스캔 PDF 페이지
        image_bytes = page.get_pixmap(dpi=150).tobytes("png") if server_ocr.available() else None
        picked = _pick(client_pages.get(index), image_bytes, index)
        pages.append(picked or OcrPage(page=index, text=""))
    return OcrDocument(pages=pages)


def _image(data: bytes, client_pages: dict[int, OcrPage]) -> OcrDocument:
    picked = _pick(client_pages.get(1), data, 1)
    if picked is None and not server_ocr.available():
        logger.warning("image without ocrResult and server OCR is not installed")
    return OcrDocument(pages=[picked or OcrPage(page=1, text="")])


def extract_document(data: bytes, ext: str, ocr_result: str | None, max_pages: int) -> OcrDocument:
    client_pages = parse_client_ocr(ocr_result)
    document = _pdf(data, client_pages, max_pages) if ext == "pdf" else _image(data, client_pages)
    if document.is_empty:
        raise AppError(422, *NOT_RECOGNIZED)
    return document
