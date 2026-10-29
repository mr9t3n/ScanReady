from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import Config, load_config
from .pdf_io import PdfInputError, render_first_page
from .report import LoadedPage


class InputError(ValueError):
    """A safe, user-facing document intake failure."""


def _limit_message(max_bytes: int) -> str:
    if max_bytes % (1024 * 1024) == 0:
        return f"The upload is larger than the {max_bytes // (1024 * 1024)} MB upload limit."
    return f"The upload is larger than the configured upload limit ({max_bytes} bytes)."


def _read_source(source: bytes | bytearray | str | Path | BinaryIO, max_bytes: int) -> tuple[bytes, str]:
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.stat().st_size > max_bytes:
            raise InputError(_limit_message(max_bytes))
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
        return data, path.name
    if isinstance(source, (bytes, bytearray)):
        return bytes(source), "upload"
    data = source.read(max_bytes + 1)
    name = Path(getattr(source, "name", "upload")).name
    return bytes(data), name


def _kind(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"%PDF-"):
        return "pdf"
    return None


def load_document(
    source: bytes | bytearray | str | Path | BinaryIO,
    *,
    display_name: str | None = None,
    config: Config | None = None,
    max_upload_bytes: int | None = None,
    min_short_side: int | None = None,
    max_decoded_megapixels: int | None = None,
    pdf_dpi: int | None = None,
) -> LoadedPage:
    active = config or load_config()
    configured_upload = int(active.value("intake", "max_upload_bytes"))
    configured_min_side = int(active.value("intake", "min_short_side"))
    configured_megapixels = int(active.value("intake", "max_decoded_megapixels"))
    configured_pdf_dpi = int(active.value("intake", "pdf_dpi"))
    upload_limit = configured_upload if max_upload_bytes is None else max_upload_bytes
    minimum_side = configured_min_side if min_short_side is None else min_short_side
    megapixel_limit = configured_megapixels if max_decoded_megapixels is None else max_decoded_megapixels
    requested_pdf_dpi = configured_pdf_dpi if pdf_dpi is None else pdf_dpi
    data, inferred_name = _read_source(source, upload_limit)
    name = display_name or inferred_name
    if not data and name.lower().endswith(".pdf"):
        raise InputError("The PDF is empty.")
    if len(data) > upload_limit:
        raise InputError(_limit_message(upload_limit))
    kind = _kind(data)
    if kind is None:
        raise InputError("Upload a JPEG, JPG, PNG, or PDF document.")
    if kind == "pdf":
        try:
            image_bgr = render_first_page(
                data,
                dpi=requested_pdf_dpi,
                max_dpi=configured_pdf_dpi,
                max_megapixels=megapixel_limit,
            )
        except PdfInputError as exc:
            raise InputError(str(exc)) from exc
    else:
        try:
            with Image.open(BytesIO(data)) as opened:
                width, height = opened.size
                if width <= 0 or height <= 0 or width * height > megapixel_limit * 1_000_000:
                    raise InputError("The decoded image dimensions are unsafe.")
                oriented = ImageOps.exif_transpose(opened)
                if oriented.mode in {"RGBA", "LA"} or "transparency" in oriented.info:
                    rgba = oriented.convert("RGBA")
                    background = Image.new("RGBA", rgba.size, "white")
                    oriented = Image.alpha_composite(background, rgba).convert("RGB")
                else:
                    oriented = oriented.convert("RGB")
                rgb = np.asarray(oriented)
        except InputError:
            raise
        except Image.DecompressionBombError as exc:
            raise InputError("The decoded image dimensions are unsafe.") from exc
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise InputError("The image is corrupt or truncated.") from exc
        image_bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        image_bgr.setflags(write=False)
    if min(image_bgr.shape[:2]) < minimum_side:
        raise InputError(f"The page is too small; the short side must be at least {minimum_side} pixels.")
    return LoadedPage(
        original_bytes=data,
        source_kind=kind,
        display_name=name,
        image_bgr=image_bgr,
    )
