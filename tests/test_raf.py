import io
import struct
from datetime import date

import pytest

from photo_file_manager.engine import build_plan, execute_plan
from photo_file_manager.metadata import read_metadata
from photo_file_manager.raf import read_raf_tags
from photo_file_manager.rules import ClassificationOptions


def test_fujifilm_raf_reads_embedded_exif(tmp_path, photo_factory):
    image = photo_factory(tmp_path / "DSCF0001.RAF", captured="2024:11:06 15:33:48", model="X-T5")
    metadata = read_metadata(image)
    assert metadata.captured_on == date(2024, 11, 6)
    assert metadata.camera_model == "X-T5"
    assert metadata.warning == ""


def test_raf_moves_into_date_raw_and_collision_preserves_both_files(tmp_path, photo_factory):
    source, output = tmp_path / "input", tmp_path / "output"
    original = photo_factory(source / "DSCF0001.RAF", captured="2024:11:06 15:33:48", model="X-T5")
    content = original.read_bytes()
    options = ClassificationOptions()
    summary = execute_plan(build_plan(source, output, classification=options))
    destination = output / "2024년 11월 06일 RAW/DSCF0001.RAF"
    assert summary.moved == 1 and not original.exists()
    assert destination.read_bytes() == content

    # Distinct incoming bytes with the same EXIF and filename must never replace it.
    original.write_bytes(content + b"different incoming raw pixels")
    results = []
    summary = execute_plan(
        build_plan(source, output, classification=options), on_result=results.append
    )
    assert summary.skipped == 1 and summary.moved == summary.failed == 0
    assert destination.read_bytes() == content
    assert original.read_bytes() == content + b"different incoming raw pixels"
    assert "덮어쓰기 안 함" in results[0].message
    assert "입력 파일 유지" in results[0].message


def test_raf_without_capture_date_is_unclassified(tmp_path, photo_factory):
    image = photo_factory(tmp_path / "image.raf", captured=None, model="X-T5")
    metadata = read_metadata(image)
    assert metadata.camera_model == "X-T5" and metadata.captured_on is None


@pytest.mark.parametrize("change", ["header", "offset", "length", "marker", "segment"])
def test_invalid_raf_is_reported(tmp_path, photo_factory, change):
    image = photo_factory(tmp_path / "image.raf")
    data = bytearray(image.read_bytes())
    if change == "header":
        data[:16] = b"invalid format!!"
    elif change == "offset":
        struct.pack_into(">I", data, 84, 0)
    elif change == "length":
        struct.pack_into(">I", data, 88, len(data) + 100)
    elif change == "marker":
        data[148:150] = b"XX"
    else:
        data[152:154] = b"\xff\xff"
    image.write_bytes(data)
    metadata = read_metadata(image)
    assert metadata.captured_on is None
    assert "EXIF 읽기 실패" in metadata.warning


def test_raf_parser_skips_image_payload(tmp_path, photo_factory):
    image = photo_factory(tmp_path / "image.raf")
    raw = image.read_bytes()
    # Simulate a larger preview/pixel payload; count bytes actually read.
    data = raw + bytes(4 * 1024 * 1024)

    class CountingStream(io.BytesIO):
        total_read = 0

        def read(self, size=-1):
            result = super().read(size)
            self.total_read += len(result)
            return result

    stream = CountingStream(data)
    tags = read_raf_tags(stream)
    assert "EXIF DateTimeOriginal" in tags
    assert stream.total_read < 4096


def test_raf_skips_non_exif_app1_before_exif(tmp_path, photo_factory):
    image = photo_factory(tmp_path / "image.raf")
    data = bytearray(image.read_bytes())
    extra = b"\xff\xe1\x00\x06XMP!"
    data[150:150] = extra
    length = struct.unpack_from(">I", data, 88)[0]
    struct.pack_into(">I", data, 88, length + len(extra))
    image.write_bytes(data)
    assert read_metadata(image).camera_model == "ILCE-7M4"


def test_unreadable_metadata_is_no_longer_silent(tmp_path):
    image = tmp_path / "unsupported.jpg"
    image.write_bytes(b"not a supported image")
    assert "EXIF 정보를 읽지 못했습니다" in read_metadata(image).warning
