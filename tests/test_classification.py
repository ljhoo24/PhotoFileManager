from datetime import date
from itertools import product
from pathlib import Path

import pytest

from photo_file_manager import engine, metadata
from photo_file_manager.engine import build_plan, execute_plan
from photo_file_manager.metadata import PhotoMetadata, read_metadata
from photo_file_manager.rules import (
    ClassificationOptions,
    FileContext,
    classification_rule,
    extension_group,
    safe_folder_name,
)


@pytest.mark.parametrize("suffix", [".jpg", ".JPEG", ".arw", ".NEF", ".CR3"])
def test_reads_capture_date_and_model_from_exif(tmp_path, photo_factory, suffix):
    source = photo_factory(tmp_path / ("photo" + suffix))
    actual = read_metadata(source)
    assert actual.captured_on == date(2026, 10, 9)
    assert actual.camera_model == "ILCE-7M4"
    assert actual.is_image and not actual.warning


def test_requested_jpg_and_raw_folders_with_single_camera(tmp_path, photo_factory):
    source, output = tmp_path / "input", tmp_path / "output"
    jpg = photo_factory(source / "nested/a.JPG")
    raw = photo_factory(source / "elsewhere/a.ARW")
    before = {path.name: path.read_bytes() for path in (jpg, raw)}
    plan = build_plan(source, output, classification=ClassificationOptions())
    assert any("생략" in note for note in plan.notes)
    summary = execute_plan(plan)
    assert summary.moved == 2
    assert (output / "2026년 10월 09일/a.JPG").read_bytes() == before["a.JPG"]
    assert (output / "2026년 10월 09일 RAW/a.ARW").read_bytes() == before["a.ARW"]
    assert not jpg.exists() and not raw.exists()


def test_mixed_cameras_create_camera_before_date(tmp_path, photo_factory):
    source, output = tmp_path / "input", tmp_path / "output"
    photo_factory(source / "sony.jpg", model="ILCE-7M4")
    photo_factory(source / "canon.cr3", model="Canon EOS R6")
    summary = execute_plan(build_plan(source, output, classification=ClassificationOptions()))
    assert summary.moved == 2
    assert (output / "ILCE-7M4/2026년 10월 09일/sony.jpg").exists()
    assert (output / "Canon EOS R6/2026년 10월 09일 RAW/canon.cr3").exists()


def test_missing_date_does_not_fall_back_to_modified_date(tmp_path, photo_factory):
    source, output = tmp_path / "input", tmp_path / "output"
    photo_factory(source / "no_date.jpg", captured=None, modified="2020:01:02 03:04:05")
    photo_factory(source / "no_date.nef", captured=None)
    execute_plan(build_plan(source, output, classification=ClassificationOptions()))
    assert (output / "미분류/no_date.jpg").exists()
    assert (output / "미분류 RAW/no_date.nef").exists()


@pytest.mark.parametrize("value", ["0000:00:00 00:00:00", "2026:02:30 00:00:00", "not-a-date"])
def test_invalid_dates_are_unclassified(tmp_path, photo_factory, value):
    source = photo_factory(tmp_path / "photo.jpg", captured=value)
    actual = read_metadata(source)
    assert actual.captured_on is None
    assert "유효하지" in actual.warning


@pytest.mark.parametrize("by_date,by_extension,by_camera", list(product([False, True], repeat=3)))
def test_all_eight_toggle_combinations(tmp_path, photo_factory, by_date, by_extension, by_camera):
    source, output = tmp_path / "input", tmp_path / "output"
    photo_factory(source / "nested/sony.jpg", model="Sony")
    photo_factory(source / "nested/canon.arw", model="Canon")
    options = ClassificationOptions(by_date, by_extension, by_camera)
    plan = build_plan(source, output, classification=options)
    destinations = {item.source.name: item.destination.relative_to(output) for item in plan.items}
    if not any((by_date, by_extension, by_camera)):
        assert destinations["sony.jpg"] == Path("nested/sony.jpg")
        assert destinations["canon.arw"] == Path("nested/canon.arw")
        return
    for filename, model, suffix in [("sony.jpg", "Sony", ""), ("canon.arw", "Canon", "RAW")]:
        parts = [model] if by_camera else []
        folder = "2026년 10월 09일" if by_date else ""
        if by_extension and suffix:
            folder = (folder + " " + suffix).strip()
        if folder:
            parts.append(folder)
        assert destinations[filename] == Path(*parts) / filename


def test_unknown_camera_does_not_claim_all_images_are_same_model(tmp_path, photo_factory):
    source, output = tmp_path / "input", tmp_path / "output"
    photo_factory(source / "known.jpg")
    photo_factory(source / "unknown.jpg", model=None, captured=None)
    execute_plan(build_plan(source, output, classification=ClassificationOptions()))
    assert (output / "ILCE-7M4/2026년 10월 09일/known.jpg").exists()
    assert (output / "기종 미상/미분류/unknown.jpg").exists()


def test_non_images_do_not_force_camera_split(tmp_path, photo_factory):
    source, output = tmp_path / "input", tmp_path / "output"
    photo_factory(source / "a.jpg")
    (source / "notes.txt").write_text("notes", encoding="utf-8")
    execute_plan(build_plan(source, output, classification=ClassificationOptions()))
    assert (output / "2026년 10월 09일/a.jpg").exists()
    assert (output / "미분류 TXT/notes.txt").exists()


def test_all_off_skips_exif_reader(tmp_path, photo_factory, monkeypatch):
    source, output = tmp_path / "input", tmp_path / "output"
    photo_factory(source / "nested/a.jpg")

    def forbidden(path):
        pytest.fail("EXIF should not be read when date and camera are disabled")

    monkeypatch.setattr(engine, "read_metadata", forbidden)
    plan = build_plan(source, output, classification=ClassificationOptions(False, False, False))
    assert plan.items[0].destination == output / "nested/a.jpg"


def test_extension_only_skips_exif_reader(tmp_path, photo_factory, monkeypatch):
    source, output = tmp_path / "input", tmp_path / "output"
    photo_factory(source / "a.nef")
    monkeypatch.setattr(engine, "read_metadata", lambda _: pytest.fail("Unnecessary EXIF read"))
    plan = build_plan(source, output, classification=ClassificationOptions(False, True, False))
    assert plan.items[0].destination == output / "RAW/a.nef"


@pytest.mark.parametrize(
    "suffix,folder",
    [
        (".JPG", ""),
        (".jpeg", ""),
        (".ARW", "RAW"),
        (".cr3", "RAW"),
        (".nef", "RAW"),
        (".png", "PNG"),
        (".HEIC", "HEIC"),
        ("", "확장자 없음"),
    ],
)
def test_extension_groups(suffix, folder):
    assert extension_group(Path("photo" + suffix)) == folder


def test_permission_error_aborts_before_moving(tmp_path, photo_factory, monkeypatch):
    source, output = tmp_path / "input", tmp_path / "output"
    original = photo_factory(source / "a.jpg")
    monkeypatch.setattr(
        metadata.exifread,
        "process_file",
        lambda *a, **k: (_ for _ in ()).throw(PermissionError("unreadable")),
    )
    with pytest.raises(PermissionError):
        build_plan(source, output, classification=ClassificationOptions())
    assert original.exists() and not output.exists()


def test_invalid_exif_is_reported_without_aborting(tmp_path, photo_factory, monkeypatch):
    source, output = tmp_path / "input", tmp_path / "output"
    photo_factory(source / "a.jpg")

    def broken(*args, **kwargs):
        raise ValueError("invalid metadata")

    monkeypatch.setattr(metadata.exifread, "process_file", broken)
    results = []
    summary = execute_plan(
        build_plan(source, output, classification=ClassificationOptions()),
        on_result=results.append,
    )
    assert summary.moved == 1
    assert (output / "기종 미상/미분류/a.jpg").exists()
    assert "EXIF 읽기 실패" in results[0].message


def test_regrouping_duplicate_filenames_preserves_second_source(tmp_path, photo_factory):
    source, output = tmp_path / "input", tmp_path / "output"
    first = photo_factory(source / "a/photo.jpg")
    second = photo_factory(source / "b/photo.jpg")
    summary = execute_plan(build_plan(source, output, classification=ClassificationOptions()))
    assert summary.moved == 1 and summary.skipped == 1
    assert not first.exists() and second.exists()


def test_camera_names_cannot_escape_or_merge_after_sanitizing():
    contexts = [
        FileContext(Path("a.jpg"), Path("a.jpg"), PhotoMetadata(camera_model="A/B", is_image=True)),
        FileContext(Path("b.jpg"), Path("b.jpg"), PhotoMetadata(camera_model="A:B", is_image=True)),
    ]
    rule, _ = classification_rule(contexts, ClassificationOptions(False, False, True))
    assert rule(contexts[0]).parent != rule(contexts[1]).parent
    assert len(rule(contexts[0]).parts) == len(rule(contexts[1]).parts) == 2
    assert safe_folder_name("../../CON") == ".._.._CON"
    assert safe_folder_name("CON") == "_CON"


def test_truncated_cr3_is_handled_as_unclassified(tmp_path):
    source = tmp_path / "bad.cr3"
    source.write_bytes(b"\x00\x00\xff\xffftypcrx ")
    actual = read_metadata(source)
    assert actual.captured_on is None and actual.camera_model is None
    assert actual.warning
