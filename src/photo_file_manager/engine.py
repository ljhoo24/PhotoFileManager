"""File planning and execution, independent of Qt and metadata libraries."""

import errno
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from photo_file_manager.metadata import PhotoMetadata, read_metadata
from photo_file_manager.rules import (
    ClassificationOptions,
    FileContext,
    FileFilter,
    OutputRule,
    accept_all,
    classification_rule,
    preserve_structure,
)


class ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class MoveItem:
    source: Path
    destination: Path
    note: str = ""


@dataclass(frozen=True)
class FileResult:
    source: Path
    destination: Path | None
    status: Literal["moved", "skipped", "failed"]
    message: str = ""


@dataclass(frozen=True)
class MovePlan:
    source_root: Path
    output_root: Path
    items: tuple[MoveItem, ...]
    skipped: tuple[FileResult, ...]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunSummary:
    moved: int
    skipped: int
    failed: int
    cancelled: bool
    pending: int


def _is_link(path: Path) -> bool:
    # Junction detection is available through pathlib on Python 3.12+.
    if path.is_symlink():
        return True
    if os.name == "nt":
        import stat

        try:
            return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        except FileNotFoundError:
            return False
    return False


def validate_folders(source: str | Path, output: str | Path) -> tuple[Path, Path]:
    if not str(source).strip() or not str(output).strip():
        raise ValidationError("입력 폴더와 출력 폴더를 모두 지정해 주세요.")
    source_root = Path(source).expanduser().resolve()
    output_root = Path(output).expanduser().resolve()
    if not source_root.is_dir():
        raise ValidationError("입력 폴더가 존재하지 않거나 폴더가 아닙니다.")
    if (
        source_root == output_root
        or source_root in output_root.parents
        or output_root in source_root.parents
    ):
        raise ValidationError("입력·출력 폴더는 서로 같거나 서로의 하위 폴더일 수 없습니다.")
    if output_root.exists() and not output_root.is_dir():
        raise ValidationError("출력 경로에 폴더가 아닌 파일이 있습니다.")
    return source_root, output_root


def _check_destination(path: Path, output_root: Path) -> None:
    if not path.is_relative_to(output_root) or path == output_root:
        raise ValidationError("출력 규칙은 출력 폴더 안의 파일 경로를 반환해야 합니다.")
    for parent in (path, *path.parents):
        if _is_link(parent):
            raise ValidationError(f"출력 경로에 링크가 있습니다: {parent}")
        if parent == output_root:
            break
    if not path.resolve().is_relative_to(output_root):
        raise ValidationError("출력 경로가 지정한 출력 폴더를 벗어납니다.")


def build_plan(
    source: str | Path,
    output: str | Path,
    *,
    file_filter: FileFilter = accept_all,
    output_rule: OutputRule = preserve_structure,
    classification: ClassificationOptions | None = None,
    is_cancelled: Callable[[], bool] = lambda: False,
) -> MovePlan:
    """Scan completely before modifying files. Scan errors abort the whole plan."""
    source_root, output_root = validate_folders(source, output)
    items: list[MoveItem] = []
    contexts: list[FileContext] = []
    skipped: list[FileResult] = []

    def scan_error(error: OSError) -> None:
        raise error

    for current, directories, filenames in os.walk(source_root, onerror=scan_error):
        if is_cancelled():
            break
        current_path = Path(current)
        for name in sorted(directories):
            directory = current_path / name
            if _is_link(directory):
                directories.remove(name)
                skipped.append(FileResult(directory, None, "skipped", "링크 폴더 제외"))
        directories.sort()
        for name in sorted(filenames):
            if is_cancelled():
                break
            path = current_path / name
            if _is_link(path) or not path.is_file():
                skipped.append(FileResult(path, None, "skipped", "일반 파일이 아니므로 제외"))
                continue
            context = FileContext(path, path.relative_to(source_root))
            if not file_filter(context):
                skipped.append(FileResult(path, None, "skipped", "필터 조건에 의해 제외"))
                continue
            if classification is not None:
                needs_metadata = classification.by_date or classification.by_camera
                metadata = read_metadata(path) if needs_metadata else PhotoMetadata()
                context = FileContext(path, context.relative_path, metadata)
            contexts.append(context)
    notes = ()
    if classification is not None:
        output_rule, note = classification_rule(contexts, classification)
        notes = (note,) if note else ()
    for context in contexts:
        if is_cancelled():
            break
        relative = Path(output_rule(context))
        if relative.is_absolute() or relative.drive or ".." in relative.parts:
            raise ValidationError(f"유효하지 않은 출력 규칙 경로: {relative}")
        destination = output_root / relative
        _check_destination(destination, output_root)
        items.append(MoveItem(context.source, destination, context.metadata.warning))
    return MovePlan(source_root, output_root, tuple(items), tuple(skipped), notes)


def _copy_then_remove(source: Path, destination: Path) -> None:
    """Cross-volume move: exclusively create, finish copying, then remove source."""
    # Exclusive creation protects against another writer creating this name.
    # A failed copy never removes the source.
    with source.open("rb") as reader:
        with destination.open("xb") as writer:
            try:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
                shutil.copystat(source, destination)
            except BaseException:
                writer.close()
                destination.unlink()
                raise
    # If removal fails, retain the complete destination and report failure.
    source.unlink()


def move_file(source: Path, destination: Path) -> None:
    """Move a regular file without replacing an existing destination."""
    try:
        if os.name == "nt":
            # Windows rename fails if the target exists; POSIX rename overwrites.
            os.rename(source, destination)
            return
        # On macOS, link + unlink provides no-replace move semantics on one volume.
        os.link(source, destination)
    except OSError as error:
        if error.errno not in {errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS}:
            raise
        _copy_then_remove(source, destination)
        return
    source.unlink()


def execute_plan(
    plan: MovePlan,
    *,
    on_result: Callable[[FileResult], None] = lambda _: None,
    on_progress: Callable[[int, int], None] = lambda _done, _total: None,
    is_cancelled: Callable[[], bool] = lambda: False,
) -> RunSummary:
    moved = failed = 0
    skipped = len(plan.skipped)
    processed = 0
    for result in plan.skipped:
        on_result(result)
    on_progress(0, len(plan.items))
    for item in plan.items:
        if is_cancelled():
            break
        try:
            if _is_link(item.source) or not item.source.is_file():
                raise ValidationError("입력 파일이 사라졌거나 일반 파일이 아닙니다.")
            if not item.source.resolve().is_relative_to(plan.source_root):
                raise ValidationError("입력 파일 경로가 입력 폴더를 벗어납니다.")
            _check_destination(item.destination, plan.output_root)
            if os.path.lexists(item.destination):
                raise FileExistsError("출력 경로에 같은 이름이 이미 있습니다.")
            item.destination.parent.mkdir(parents=True, exist_ok=True)
            _check_destination(item.destination, plan.output_root)
            move_file(item.source, item.destination)
        except FileExistsError:
            skipped += 1
            result = FileResult(
                item.source,
                item.destination,
                "skipped",
                "동일 이름 존재 · 덮어쓰기 안 함 · 입력 파일 유지",
            )
        except (OSError, ValidationError) as error:
            failed += 1
            result = FileResult(item.source, item.destination, "failed", str(error))
        else:
            moved += 1
            result = FileResult(item.source, item.destination, "moved", item.note)
        processed += 1
        on_result(result)
        on_progress(processed, len(plan.items))
    return RunSummary(moved, skipped, failed, is_cancelled(), len(plan.items) - processed)
