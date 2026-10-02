from __future__ import annotations

import html

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_TEXT_CHARS = 120_000
MAX_PDF_PAGES = 100
MAX_IMAGE_PIXELS = 120_000_000
OCR_IMAGE_PIXELS = 20_000_000
OCR_TIMEOUT = 120
CONVERSION_TIMEOUT = 300


class UserInputError(ValueError):
    """A rejection whose message is written for the person who sent the input."""


def authorized(
    user_id: int | None,
    chat_id: int | None,
    users: list[int],
    group_chat_id: int | None,
) -> bool:
    return user_id in users and chat_id is not None and chat_id == group_chat_id


def check_upload(size: int | None) -> None:
    if size is None or size < 1 or size > MAX_UPLOAD_BYTES:
        raise UserInputError("Attachments must be between 1 byte and 100 MiB.")


def check_text(text: str) -> None:
    if len(text) > MAX_TEXT_CHARS:
        raise UserInputError("Expense text is too long (maximum 120,000 characters).")


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def csv_value(value: object) -> object:
    if isinstance(value, str) and value.lstrip().startswith(
        ("=", "+", "-", "@", "\t", "\r")
    ):
        return "'" + value
    return value


def receipt_path(data_dir, stored_path: str):
    from pathlib import Path

    archive = (data_dir / "files").resolve()
    stored = Path(stored_path)
    candidate = archive / stored.name if stored.is_absolute() else data_dir / stored
    resolved = candidate.resolve()
    if not resolved.is_relative_to(archive):
        raise ValueError("Receipt path is outside the archive")
    return resolved
