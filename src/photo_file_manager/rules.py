"""Independent on/off output grouping rules."""

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from photo_file_manager.metadata import RAW_EXTENSIONS, PhotoMetadata


@dataclass(frozen=True)
class FileContext:
    source: Path
    relative_path: Path
    metadata: PhotoMetadata = field(default_factory=PhotoMetadata)


@dataclass(frozen=True)
class ClassificationOptions:
    by_date: bool = True
    by_extension: bool = True
    by_camera: bool = True


class FileFilter(Protocol):
    def __call__(self, file: FileContext) -> bool: ...


class OutputRule(Protocol):
    def __call__(self, file: FileContext) -> Path: ...


def accept_all(file: FileContext) -> bool:
    return True


def preserve_structure(file: FileContext) -> Path:
    return file.relative_path


def safe_folder_name(value: str) -> str:
    # Same rules on Windows and macOS, including Windows device names.
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip().rstrip(". ")
    name = name[:120].rstrip(". ") or "기종 미상"
    if name.split(".", 1)[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        name = "_" + name
    return name


def extension_group(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg", ".jpe", ".jfif"}:
        return ""
    if suffix in RAW_EXTENSIONS:
        return "RAW"
    return safe_folder_name(suffix[1:].upper()) if suffix else "확장자 없음"


def _model_key(file: FileContext) -> str | None:
    model = file.metadata.camera_model
    return model.casefold() if model else None


def _camera_folders(files: list[FileContext]) -> dict[str | None, str]:
    labels = {_model_key(file): file.metadata.camera_model or "기종 미상" for file in files}
    result = {}
    used = set()
    for key in sorted(labels, key=lambda value: (value is not None, value or "")):
        name = safe_folder_name(labels[key])
        # Different model values can become the same legal folder name.
        if name.casefold() in used:
            digest = hashlib.sha256((key or "").encode("utf-8")).hexdigest()[:10]
            name += f" ({digest})"
        used.add(name.casefold())
        result[key] = name
    return result


def classification_rule(
    files: list[FileContext], options: ClassificationOptions
) -> tuple[OutputRule, str]:
    image_models = {_model_key(file) for file in files if file.metadata.is_image}
    same_known_model = len(image_models) == 1 and None not in image_models
    use_camera = options.by_camera and bool(image_models) and not same_known_model
    camera_folders = _camera_folders(files) if use_camera else {}
    note = ""
    if options.by_camera and same_known_model:
        note = "카메라 분류 생략: 모든 입력 이미지의 기종이 같습니다."
    elif options.by_camera and not image_models:
        note = "카메라 분류 생략: 입력 이미지가 없습니다."
    elif use_camera:
        note = "카메라 분류 적용 · 기종 정보가 없는 파일은 '기종 미상'으로 이동합니다."

    if not (options.by_date or options.by_extension or use_camera):
        return preserve_structure, note

    def output_path(file: FileContext) -> Path:
        parts = []
        if use_camera:
            parts.append(camera_folders[_model_key(file)])
        folder = ""
        if options.by_date:
            captured = file.metadata.captured_on
            folder = (
                f"{captured.year:04d}년 {captured.month:02d}월 {captured.day:02d}일"
                if captured
                else "미분류"
            )
        if options.by_extension:
            extension = extension_group(file.source)
            folder = " ".join(part for part in (folder, extension) if part)
        if folder:
            parts.append(folder)
        return Path(*parts) / file.source.name

    return output_path, note
