from __future__ import annotations

from io import BytesIO
import math
from pathlib import Path
from typing import Callable
import zipfile

from taxedo.security import (
    check_upload, check_text, UserInputError,
    MAX_PDF_PAGES, MAX_IMAGE_PIXELS, OCR_IMAGE_PIXELS, OCR_TIMEOUT,
)

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".gif"})

SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | frozenset(
    {
        ".pdf",
        ".docx",
        ".xlsx",
        ".xls",
        ".pptx",
        ".txt",
        ".md",
        ".csv",
    }
)
MIN_PDF_TEXT_LENGTH = 20
MIN_SCANNED_IMAGE_SHARE = 0.25


def convert_document(
    data: bytes, filename: str, ocr_image: Callable[[bytes], str]
) -> str:
    check_upload(len(data))
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise UserInputError(f"Unsupported document format: {extension or 'unknown'}")

    if extension in IMAGE_EXTENSIONS:
        text = ocr_image(data)
        if not text.strip():
            raise UserInputError("No readable text was found in this image")
        check_text(text)
        return text

    _validate_format(data, extension)
    from markitdown import MarkItDown, StreamInfo

    converter = MarkItDown(enable_plugins=False)

    def to_markdown(content: bytes) -> str:
        return converter.convert_stream(
            BytesIO(content), stream_info=StreamInfo(extension=extension)
        ).text_content.strip()

    if extension == ".pdf":
        text = _convert_pdf(data, to_markdown, ocr_image)
    else:
        text = to_markdown(data)

    if not text.strip():
        raise UserInputError("No text could be extracted from this document")
    check_text(text)
    return text


def _convert_pdf(
    data: bytes,
    to_markdown: Callable[[bytes], str],
    ocr_image: Callable[[bytes], str],
) -> str:
    import pymupdf

    parts = []
    with pymupdf.open(stream=data, filetype="pdf") as document:
        if len(document) > MAX_PDF_PAGES:
            raise UserInputError(f"PDF exceeds the {MAX_PDF_PAGES}-page limit")
        for index, page in enumerate(document):
            with pymupdf.open() as single_page:
                single_page.insert_pdf(document, from_page=index, to_page=index)
                text = to_markdown(single_page.tobytes())

            if (
                len(text) < MIN_PDF_TEXT_LENGTH
                or _image_share(page) >= MIN_SCANNED_IMAGE_SHARE
            ):
                scale = min(2, math.sqrt(OCR_IMAGE_PIXELS / (page.rect.width * page.rect.height)))
                image = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
                scanned_text = ocr_image(image.tobytes("png")).strip()
                text = scanned_text or text

            if not text:
                raise UserInputError(f"No readable text on PDF page {index + 1}")
            parts.append(f"<!-- page {index + 1} -->\n{text}")
            check_text("\n\n".join(parts))

    return "\n\n".join(parts)


def _image_share(page) -> float:
    import pymupdf

    covered = sum(
        abs(pymupdf.Rect(info["bbox"]) & page.rect) for info in page.get_image_info()
    )
    return covered / abs(page.rect)


def _validate_format(data: bytes, extension: str) -> None:
    if extension == ".pdf" and not data.startswith(b"%PDF-"):
        raise UserInputError("File is not a PDF")
    if extension == ".xls" and not data.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        raise UserInputError("File is not an XLS document")
    if extension in {".docx", ".xlsx", ".pptx"}:
        required = {
            ".docx": "word/document.xml",
            ".xlsx": "xl/workbook.xml",
            ".pptx": "ppt/presentation.xml",
        }
        with zipfile.ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if (
                len(entries) > 2000
                or sum(entry.file_size for entry in entries) > 40 * 1024 * 1024
            ):
                raise UserInputError("Expanded document is too large")
            if required[extension] not in archive.namelist():
                raise UserInputError("Document content does not match its extension")
    if extension in {".txt", ".md", ".csv"}:
        if b"\x00" in data:
            raise UserInputError("Text files must not contain binary data")
        check_text(data.decode("utf-8-sig", errors="replace"))


def ocr_image(data: bytes) -> str:
    from PIL import Image, ImageOps
    import pytesseract

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    with Image.open(BytesIO(data)) as image:
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise UserInputError("Image exceeds the pixel limit")
        # Decode JPEGs at a lower resolution where possible, then bound OCR work.
        ratio = min(1, math.sqrt(OCR_IMAGE_PIXELS / (image.width * image.height)))
        target = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
        image.draft("RGB", target)
        image.thumbnail(target, Image.Resampling.LANCZOS)
        prepared = ImageOps.exif_transpose(image).convert("RGB")
        readings = []
        for mode in (3, 6):
            try:
                reading = pytesseract.image_to_string(
                    prepared, lang="deu+eng", config=f"--psm {mode}", timeout=OCR_TIMEOUT
                ).strip()
            except RuntimeError:
                continue
            if reading and reading not in readings:
                readings.append(reading)
        if not readings:
            raise UserInputError("No readable text was found in either OCR pass")
        text = readings[0] if len(readings) == 1 else "\n\n".join(
            f"--- OCR reading {index} of the SAME image (alternative, not another receipt) ---\n{reading}"
            for index, reading in enumerate(readings, 1)
        )
    check_text(text)
    return text
