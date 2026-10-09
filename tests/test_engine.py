import errno
import os
from pathlib import Path
from threading import Event

import pytest

from photo_file_manager import engine
from photo_file_manager.engine import (
    ValidationError,
    build_plan,
    execute_plan,
    move_file,
    validate_folders,
)


@pytest.fixture
def folders(tmp_path):
    source = tmp_path / "입력 사진"
    source.mkdir()
    return source, tmp_path / "정리 결과"


def write_file(root: Path, relative: str, content: bytes = b"photo") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_moves_all_files_and_preserves_relative_structure(folders):
    source, output = folders
    photo = write_file(source, "여행/사진.JPG", b"image bytes")
    text = write_file(source, "notes.txt", b"all extensions")
    (source / "empty").mkdir()
    modified = photo.stat().st_mtime_ns

    plan = build_plan(source, output)
    assert photo.exists() and text.exists()
    assert not output.exists()
    progress = []
    summary = execute_plan(plan, on_progress=lambda done, total: progress.append((done, total)))

    assert summary.moved == 2
    assert summary.failed == summary.skipped == summary.pending == 0
    assert (output / "여행/사진.JPG").read_bytes() == b"image bytes"
    assert (output / "여행/사진.JPG").stat().st_mtime_ns == modified
    assert (output / "notes.txt").read_bytes() == b"all extensions"
    assert not photo.exists() and not text.exists()
    assert (source / "여행").is_dir() and (source / "empty").is_dir()
    assert progress[-1] == (2, 2)


@pytest.mark.parametrize("conflict_kind", ["file", "directory"])
def test_existing_destination_is_never_replaced(folders, conflict_kind):
    source, output = folders
    original = write_file(source, "photo.jpg", b"new photo")
    if conflict_kind == "file":
        write_file(output, "photo.jpg", b"existing photo")
    else:
        (output / "photo.jpg").mkdir(parents=True)
    summary = execute_plan(build_plan(source, output))
    assert summary.skipped == 1 and summary.moved == 0
    assert original.read_bytes() == b"new photo"
    if conflict_kind == "file":
        assert (output / "photo.jpg").read_bytes() == b"existing photo"
    else:
        assert list((output / "photo.jpg").iterdir()) == []


def test_conflict_created_after_scan_is_skipped(folders):
    source, output = folders
    original = write_file(source, "a.jpg")
    plan = build_plan(source, output)
    write_file(output, "a.jpg", b"other process")
    summary = execute_plan(plan)
    assert summary.skipped == 1 and original.exists()
    assert (output / "a.jpg").read_bytes() == b"other process"


@pytest.mark.parametrize("case", ["same", "inside", "outside"])
def test_overlapping_roots_are_rejected(folders, case):
    source, _ = folders
    output = {"same": source, "inside": source / "nested", "outside": source.parent}[case]
    with pytest.raises(ValidationError):
        build_plan(source, output)


@pytest.mark.parametrize("source,output", [("", "output"), ("input", "")])
def test_blank_paths_are_rejected(source, output):
    with pytest.raises(ValidationError):
        validate_folders(source, output)


def test_missing_input_and_output_file_are_rejected(folders):
    source, output = folders
    with pytest.raises(ValidationError):
        build_plan(source / "missing", output)
    output.write_bytes(b"existing")
    with pytest.raises(ValidationError):
        build_plan(source, output)


def test_filter_and_custom_output_rule_are_extension_points(folders):
    source, output = folders
    photo = write_file(source, "nested/a.jpg")
    other = write_file(source, "b.txt")
    plan = build_plan(
        source,
        output,
        file_filter=lambda file: file.source.suffix == ".jpg",
        output_rule=lambda file: Path("custom/2026") / file.source.name,
    )
    summary = execute_plan(plan)
    assert summary.moved == 1 and summary.skipped == 1
    assert (output / "custom/2026/a.jpg").exists()
    assert not photo.exists() and other.exists()


@pytest.mark.parametrize("relative", ["../escape.jpg", ".", "nested/../../escape.jpg"])
def test_unsafe_output_rule_is_rejected_before_moving(folders, relative):
    source, output = folders
    photo = write_file(source, "a.jpg")
    with pytest.raises(ValidationError):
        build_plan(source, output, output_rule=lambda _: Path(relative))
    assert photo.exists() and not output.exists()


def test_absolute_output_rule_is_rejected(folders):
    source, output = folders
    write_file(source, "a.jpg")
    with pytest.raises(ValidationError):
        build_plan(source, output, output_rule=lambda _: source.parent / "escaped.jpg")


def test_two_inputs_mapping_to_same_name_do_not_overwrite(folders):
    source, output = folders
    write_file(source, "a.jpg", b"a")
    second = write_file(source, "b.jpg", b"b")
    summary = execute_plan(build_plan(source, output, output_rule=lambda _: Path("same.jpg")))
    assert summary.moved == summary.skipped == 1
    assert (output / "same.jpg").read_bytes() == b"a"
    assert second.read_bytes() == b"b"


def test_empty_input_does_not_create_output(folders):
    source, output = folders
    summary = execute_plan(build_plan(source, output))
    assert summary.moved == 0 and summary.failed == 0
    assert not output.exists()


def test_cancel_before_execution_keeps_all_files(folders):
    source, output = folders
    original = write_file(source, "a.jpg")
    summary = execute_plan(build_plan(source, output), is_cancelled=lambda: True)
    assert summary.cancelled and summary.pending == 1
    assert original.exists() and not output.exists()


def test_cancel_between_files_keeps_remaining_files(folders):
    source, output = folders
    write_file(source, "a.jpg")
    remaining = write_file(source, "b.jpg")
    cancel = Event()
    summary = execute_plan(
        build_plan(source, output),
        on_result=lambda _: cancel.set(),
        is_cancelled=cancel.is_set,
    )
    assert summary.moved == 1 and summary.cancelled and summary.pending == 1
    assert remaining.exists()


def test_disappeared_source_does_not_stop_other_files(folders):
    source, output = folders
    removed = write_file(source, "a.jpg")
    write_file(source, "b.jpg")
    plan = build_plan(source, output)
    removed.unlink()
    summary = execute_plan(plan)
    assert summary.failed == 1 and summary.moved == 1
    assert (output / "b.jpg").exists()


def force_cross_volume(monkeypatch):
    def fail_rename(*args, **kwargs):
        raise OSError(errno.EXDEV, "Different volumes")

    monkeypatch.setattr(engine.os, "rename" if os.name == "nt" else "link", fail_rename)


def test_cross_volume_move_removes_source_after_complete_copy(folders, monkeypatch):
    source, output = folders
    original = write_file(source, "a.jpg", b"photo data")
    modified = original.stat().st_mtime_ns
    force_cross_volume(monkeypatch)
    summary = execute_plan(build_plan(source, output))
    assert summary.moved == 1 and not original.exists()
    assert (output / "a.jpg").read_bytes() == b"photo data"
    assert (output / "a.jpg").stat().st_mtime_ns == modified


def test_failed_cross_volume_copy_preserves_source_and_removes_partial(folders, monkeypatch):
    source, output = folders
    original = write_file(source, "a.jpg", b"complete original")
    force_cross_volume(monkeypatch)

    def broken_copy(reader, writer, **kwargs):
        writer.write(b"partial")
        raise OSError("Disk full")

    monkeypatch.setattr(engine.shutil, "copyfileobj", broken_copy)
    summary = execute_plan(build_plan(source, output))
    assert summary.failed == 1
    assert original.read_bytes() == b"complete original"
    assert not (output / "a.jpg").exists()


@pytest.mark.parametrize("cross_volume", [False, True])
def test_low_level_move_refuses_existing_file(folders, monkeypatch, cross_volume):
    source, output = folders
    original = write_file(source, "a.jpg", b"original")
    destination = write_file(output, "a.jpg", b"existing")
    if cross_volume:
        force_cross_volume(monkeypatch)
    with pytest.raises(FileExistsError):
        move_file(original, destination)
    assert original.read_bytes() == b"original"
    assert destination.read_bytes() == b"existing"


def make_link(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError:
        pytest.skip("Symbolic link creation is unavailable on this host")


def test_input_symlinks_are_not_followed(folders):
    source, output = folders
    external = write_file(source.parent, "external/a.jpg")
    make_link(source / "file.jpg", external)
    make_link(source / "folder", external.parent, directory=True)
    summary = execute_plan(build_plan(source, output))
    assert summary.skipped == 2 and summary.moved == 0
    assert external.exists() and not output.exists()


def test_output_symlink_cannot_redirect_moves(folders):
    source, output = folders
    original = write_file(source, "nested/a.jpg")
    output.mkdir()
    external = source.parent / "external"
    external.mkdir()
    make_link(output / "nested", external, directory=True)
    with pytest.raises(ValidationError):
        build_plan(source, output)
    assert original.exists() and not list(external.iterdir())


def test_scan_error_aborts_before_any_move(folders, monkeypatch):
    source, output = folders
    original = write_file(source, "a.jpg")

    def broken_walk(root, *, onerror):
        onerror(PermissionError("Unreadable folder"))
        yield root, [], []

    monkeypatch.setattr(engine.os, "walk", broken_walk)
    with pytest.raises(PermissionError):
        build_plan(source, output)
    assert original.exists() and not output.exists()


@pytest.mark.parametrize("cross_volume", [False, True])
def test_posix_move_branch_preserves_no_replace_semantics(folders, monkeypatch, cross_volume):
    from types import SimpleNamespace

    source, output = folders
    original = write_file(source, "a.jpg", b"posix content")
    destination = output / "a.jpg"
    output.mkdir()

    def link(src, dst):
        if cross_volume:
            raise OSError(errno.EXDEV, "Different volumes")
        return os.link(src, dst)

    # Exercise the macOS branch with real hard links without changing global os.name.
    monkeypatch.setattr(
        engine,
        "os",
        SimpleNamespace(
            name="posix",
            link=link,
            fsync=os.fsync,
        ),
    )
    move_file(original, destination)
    assert not original.exists()
    assert destination.read_bytes() == b"posix content"

    original.write_bytes(b"new content")
    with pytest.raises(FileExistsError):
        move_file(original, destination)
    assert original.read_bytes() == b"new content"
    assert destination.read_bytes() == b"posix content"


def test_source_removal_failure_keeps_complete_destination(folders, monkeypatch):
    source, output = folders
    original = write_file(source, "a.jpg", b"complete content")
    force_cross_volume(monkeypatch)
    real_unlink = Path.unlink

    def deny_original_unlink(path, *args, **kwargs):
        if path == original:
            raise PermissionError("Cannot remove original")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_original_unlink)
    summary = execute_plan(build_plan(source, output))
    assert summary.failed == 1 and summary.moved == 0
    assert original.read_bytes() == b"complete content"
    assert (output / "a.jpg").read_bytes() == b"complete content"
