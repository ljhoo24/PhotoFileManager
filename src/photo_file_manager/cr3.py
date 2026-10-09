"""Read Canon CR3 CMT1/CMT2 TIFF metadata without loading image data.

Container reference: https://github.com/lclevy/canon_cr3/blob/master/readme.md
"""

import io
import struct
from typing import BinaryIO

import exifread

CANON_UUID = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")
MAX_METADATA_SIZE = 16 * 1024 * 1024


def read_cr3_tags(stream: BinaryIO) -> dict:
    stream.seek(0, 2)
    file_size = stream.tell()
    tags = {}

    def visit(start: int, end: int, depth: int = 0) -> None:
        if depth > 4:
            raise ValueError("CR3 metadata nesting is too deep")
        position = start
        while position < end:
            stream.seek(position)
            header = stream.read(8)
            if len(header) != 8:
                raise ValueError("Truncated CR3 box header")
            size, kind = struct.unpack(">I4s", header)
            header_size = 8
            if size == 1:
                extended = stream.read(8)
                if len(extended) != 8:
                    raise ValueError("Truncated CR3 extended box")
                size = struct.unpack(">Q", extended)[0]
                header_size = 16
            elif size == 0:
                size = end - position
            if size < header_size or position + size > end:
                raise ValueError("Invalid CR3 box size")
            payload = position + header_size
            box_end = position + size
            if kind == b"moov":
                visit(payload, box_end, depth + 1)
            elif kind == b"uuid":
                stream.seek(payload)
                if box_end - payload < 16:
                    raise ValueError("Truncated CR3 UUID")
                if stream.read(16) == CANON_UUID:
                    visit(payload + 16, box_end, depth + 1)
            elif kind in {b"CMT1", b"CMT2"}:
                length = box_end - payload
                if length > MAX_METADATA_SIZE:
                    raise ValueError("CR3 metadata block is too large")
                stream.seek(payload)
                data = stream.read(length)
                parsed = exifread.process_file(
                    io.BytesIO(data), details=False, extract_thumbnail=False
                )
                if kind == b"CMT1":
                    if "Image Model" in parsed:
                        tags["Image Model"] = parsed["Image Model"]
                else:
                    original = parsed.get("Image DateTimeOriginal")
                    if original is None:
                        original = parsed.get("EXIF DateTimeOriginal")
                    if original is not None:
                        tags["EXIF DateTimeOriginal"] = original
            position = box_end

    visit(0, file_size)
    return tags
