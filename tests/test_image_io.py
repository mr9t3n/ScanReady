from __future__ import annotations

import io
from pathlib import Path

import pytest
import numpy as np
from PIL import Image

from scanready.checks import analyze_checks
from scanready.config import load_config
from scanready.decision import decide
from scanready.image_io import InputError, load_document


FIXTURES = Path(__file__).parent / "fixtures"


def test_png_signature_wins_over_filename(png_bytes: bytes) -> None:
    loaded = load_document(png_bytes, display_name="renamed.txt")
    assert loaded.source_kind == "png"
    assert loaded.original_bytes == png_bytes
    assert loaded.image_bgr.shape[:2] == (480, 640)


def test_jpeg_and_jpg_are_one_content_kind(jpeg_bytes: bytes) -> None:
    loaded = load_document(jpeg_bytes, display_name="page.jpg")
    assert loaded.source_kind == "jpeg"
    assert loaded.original_bytes == jpeg_bytes


def test_gif_and_renamed_text_fail_closed() -> None:
    with pytest.raises(InputError, match="JPEG, JPG, PNG, or PDF"):
        load_document(b"GIF89a-not-supported", display_name="page.jpg")
    with pytest.raises(InputError, match="JPEG, JPG, PNG, or PDF"):
        load_document(b"plain text", display_name="page.png")


def test_multipage_pdf_renders_first_page_only() -> None:
    source = FIXTURES / "multipage.pdf"
    original = source.read_bytes()
    loaded = load_document(source)
    height, width = loaded.image_bgr.shape[:2]
    assert loaded.source_kind == "pdf"
    assert loaded.original_bytes == original
    assert height > width
    assert height / width == pytest.approx(1.5, rel=0.03)

    single = load_document(FIXTURES / "first_page.pdf")
    assert np.array_equal(loaded.image_bgr, single.image_bgr)
    multi_checks = analyze_checks(loaded.image_bgr, load_config())
    single_checks = analyze_checks(single.image_bgr, load_config())
    assert [(item.name, item.status, item.measurement) for item in multi_checks] == [
        (item.name, item.status, item.measurement) for item in single_checks
    ]
    assert decide(multi_checks) is decide(single_checks)


@pytest.mark.parametrize(
    ("name", "message"),
    [("encrypted.pdf", "password-protected"), ("empty.pdf", "empty"), ("corrupt.pdf", "corrupt")],
)
def test_bad_pdfs_have_readable_errors(tmp_path: Path, name: str, message: str) -> None:
    if name == "encrypted.pdf":
        source = FIXTURES / name
    else:
        source = tmp_path / name
        source.write_bytes(b"" if name == "empty.pdf" else b"%PDF-1.4\nnot a document")
    with pytest.raises(InputError, match=message):
        load_document(source)


def test_oversized_upload_is_rejected_before_decode(png_bytes: bytes) -> None:
    with pytest.raises(InputError, match="upload limit"):
        load_document(png_bytes, max_upload_bytes=10)


def test_stream_reads_only_one_byte_beyond_limit() -> None:
    class GuardedStream(io.BytesIO):
        requested = None

        def read(self, size: int = -1) -> bytes:
            self.requested = size
            if size < 0:
                raise AssertionError("unbounded read")
            return super().read(size)

    stream = GuardedStream(b"x" * 20)
    with pytest.raises(InputError, match="upload limit"):
        load_document(stream, max_upload_bytes=10)
    assert stream.requested == 11


def test_pillow_decompression_bomb_becomes_domain_error(monkeypatch, png_bytes: bytes) -> None:
    def bomb(*args, **kwargs):
        raise Image.DecompressionBombError("hostile dimensions")

    monkeypatch.setattr(Image, "open", bomb)
    with pytest.raises(InputError, match="dimensions are unsafe"):
        load_document(png_bytes)


def test_default_minimum_short_side_is_enforced() -> None:
    image = Image.new("RGB", (399, 500), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    with pytest.raises(InputError, match="at least 400 pixels"):
        load_document(buffer.getvalue())


def test_transparency_is_composited_onto_white() -> None:
    image = Image.new("RGBA", (640, 480), (20, 40, 60, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    loaded = load_document(buffer.getvalue())
    assert loaded.image_bgr[0, 0].tolist() == [255, 255, 255]


def test_exif_orientation_is_applied_without_changing_bytes() -> None:
    image = Image.new("RGB", (640, 480), "white")
    exif = Image.Exif()
    exif[274] = 6
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    source = buffer.getvalue()
    loaded = load_document(source)
    assert loaded.image_bgr.shape[:2] == (640, 480)
    assert loaded.original_bytes == source
