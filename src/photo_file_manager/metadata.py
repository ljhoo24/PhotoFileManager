"""Read capture metadata without decoding or modifying image pixels."""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import exifread

from photo_file_manager.cr3 import read_cr3_tags
from photo_file_manager.raf import read_raf_tags

RAW_EXTENSIONS = frozenset(
    {
        ".raw",
        ".arw",
        ".srf",
        ".sr2",
        ".crw",
        ".cr2",
        ".cr3",
        ".nef",
        ".nrw",
        ".orf",
        ".ori",
        ".rw2",
        ".rwl",
        ".raf",
        ".pef",
        ".ptx",
        ".dng",
        ".3fr",
        ".fff",
        ".iiq",
        ".kdc",
        ".dcr",
        ".mrw",
        ".mos",
        ".x3f",
        ".erf",
        ".srw",
    }
)
IMAGE_EXTENSIONS = RAW_EXTENSIONS | frozenset(
    {
        ".jpg",
        ".jpeg",
        ".jpe",
        ".jfif",
        ".tif",
        ".tiff",
        ".png",
        ".heic",
        ".heif",
        ".hif",
        ".webp",
        ".avif",
        ".jxl",
        ".bmp",
        ".gif",
        ".jp2",
    }
)


@dataclass(frozen=True)
class PhotoMetadata:
    captured_on: date | None = None
    camera_model: str | None = None
    is_image: bool = False
    warning: str = ""


def normalize_model(value: object) -> str | None:
    text = unicodedata.normalize("NFC", str(value)).split("\x00", 1)[0]
    text = " ".join(text.split())
    return text or None


def parse_capture_date(value: object) -> date | None:
    text = str(value).strip().rstrip("\x00")
    # EXIF DateTimeOriginal is a local wall-clock date/time. No UTC conversion.
    if not re.fullmatch(r"\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}", text):
        return None
    try:
        return datetime.strptime(text, "%Y:%m:%d %H:%M:%S").date()
    except ValueError:
        return None


def read_metadata(path: Path) -> PhotoMetadata:
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        return PhotoMetadata()
    try:
        with path.open("rb") as stream:
            if path.suffix.lower() == ".cr3":
                tags = read_cr3_tags(stream)
            elif path.suffix.lower() == ".raf":
                tags = read_raf_tags(stream)
            else:
                tags = exifread.process_file(stream, details=False, extract_thumbnail=False)
    except OSError:
        # Permission and I/O errors are not proof of missing EXIF. Abort planning.
        raise
    except Exception as error:
        return PhotoMetadata(is_image=True, warning=f"EXIF 읽기 실패: {error}")

    if not tags:
        return PhotoMetadata(
            is_image=True,
            warning="EXIF 정보를 읽지 못했습니다 (정보 없음·미지원 형식·손상 가능)",
        )

    original = tags.get("EXIF DateTimeOriginal")
    captured_on = parse_capture_date(original) if original is not None else None
    model = tags.get("Image Model")
    return PhotoMetadata(
        captured_on=captured_on,
        camera_model=normalize_model(model) if model is not None else None,
        is_image=True,
        warning="유효하지 않은 EXIF 촬영일: 미분류 처리"
        if original is not None and captured_on is None
        else "",
    )
