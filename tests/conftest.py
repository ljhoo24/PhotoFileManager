"""Generate small real EXIF containers; no personal photos or network fixtures."""

import struct

import pytest


def tiff_metadata(model=None, captured=None, *, flat_date=False, modified=None):
    def ifd(entries, start):
        overflow = bytearray()
        records = bytearray()
        data_offset = start + 2 + len(entries) * 12 + 4
        for tag, kind, value in sorted(entries):
            if kind == 4:
                count, encoded = 1, struct.pack("<I", value)
            else:
                raw = value.encode("ascii") + b"\0"
                count = len(raw)
                if count <= 4:
                    encoded = raw.ljust(4, b"\0")
                else:
                    encoded = struct.pack("<I", data_offset + len(overflow))
                    overflow.extend(raw)
            records.extend(struct.pack("<HHI", tag, kind, count) + encoded)
        return struct.pack("<H", len(entries)) + records + b"\0" * 4 + overflow

    entries = []
    if model is not None:
        entries.append((0x0110, 2, model))
    if modified is not None:
        entries.append((0x0132, 2, modified))
    exif = b""
    if captured is not None:
        if flat_date:
            entries.append((0x9003, 2, captured))
        else:
            entries.append((0x8769, 4, 0))
            exif_offset = 8 + len(ifd(entries, 8))
            entries[-1] = (0x8769, 4, exif_offset)
            exif = ifd([(0x9003, 2, captured)], exif_offset)
    return b"II\x2a\0\x08\0\0\0" + ifd(entries, 8) + exif


def box(kind, payload):
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


@pytest.fixture
def photo_factory():
    def make(path, captured="2026:10:09 13:24:55", model="ILCE-7M4", modified=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            payload = b"Exif\0\0" + tiff_metadata(model, captured, modified=modified)
            data = b"\xff\xd8\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload + b"\xff\xd9"
        elif path.suffix.lower() == ".raf":
            payload = b"Exif\0\0" + tiff_metadata(model, captured, modified=modified)
            jpeg = b"\xff\xd8\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload + b"\xff\xd9"
            header = bytearray(148)
            header[:16] = b"FUJIFILMCCD-RAW "
            struct.pack_into(">II", header, 84, len(header), len(jpeg))
            data = bytes(header) + jpeg + b"raw image data"
        elif path.suffix.lower() == ".cr3":
            data = box(b"ftyp", b"crx \0\0\0\1crx isom")
            metadata = box(b"CMT1", tiff_metadata(model=model))
            metadata += box(b"CMT2", tiff_metadata(captured=captured, flat_date=True))
            canon = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")
            data += box(b"moov", box(b"uuid", canon + metadata))
            data += box(b"mdat", b"placeholder")
        else:
            data = tiff_metadata(model, captured, modified=modified)
        path.write_bytes(data)
        return path

    return make
