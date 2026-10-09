"""Read EXIF from the JPEG preview in Fujifilm RAF, without reading raw pixels.

Format reference: https://libopenraw.freedesktop.org/formats/raf/
"""

import io
import struct
from typing import BinaryIO

import exifread


def read_raf_tags(stream: BinaryIO) -> dict:
    stream.seek(0, 2)
    file_size = stream.tell()
    stream.seek(0)
    header = stream.read(92)
    if len(header) != 92 or not header.startswith(b"FUJIFILMCCD-RAW "):
        raise ValueError("유효하지 않은 RAF 헤더")
    offset, length = struct.unpack_from(">II", header, 84)
    end = offset + length
    if offset < 92 or length < 2 or end > file_size:
        raise ValueError("RAF JPEG 미리보기 범위가 올바르지 않습니다.")
    stream.seek(offset)

    def read_exact(size: int) -> bytes:
        if stream.tell() + size > end:
            raise ValueError("RAF JPEG 메타데이터가 미리보기 범위를 벗어납니다.")
        data = stream.read(size)
        if len(data) != size:
            raise ValueError("RAF JPEG 메타데이터가 잘렸습니다.")
        return data

    if read_exact(2) != b"\xff\xd8":
        raise ValueError("RAF 미리보기가 JPEG 형식이 아닙니다.")
    while stream.tell() < end:
        if read_exact(1) != b"\xff":
            raise ValueError("유효하지 않은 RAF JPEG 마커")
        marker = read_exact(1)[0]
        while marker == 0xFF:
            marker = read_exact(1)[0]
        if marker in {0xD9, 0xDA}:  # EOI / SOS: never read the compressed pixels.
            return {}
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if marker in {0x00, 0xD8}:
            raise ValueError("유효하지 않은 RAF JPEG 마커")
        segment_size = struct.unpack(">H", read_exact(2))[0]
        if segment_size < 2 or stream.tell() + segment_size - 2 > end:
            raise ValueError("유효하지 않은 RAF JPEG 세그먼트 길이")
        if marker == 0xE1:
            payload = read_exact(segment_size - 2)
            if payload.startswith(b"Exif\0\0"):
                return exifread.process_file(
                    io.BytesIO(payload[6:]), details=False, extract_thumbnail=False
                )
        else:
            stream.seek(segment_size - 2, 1)
    return {}
