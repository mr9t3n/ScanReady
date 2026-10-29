from __future__ import annotations

from io import BytesIO
from math import sqrt

import cv2
import numpy as np
import pypdfium2 as pdfium


class PdfInputError(ValueError):
    """A readable PDF intake failure."""


def render_first_page(
    data: bytes,
    *,
    dpi: int,
    max_dpi: int,
    max_megapixels: int,
) -> np.ndarray:
    if not data:
        raise PdfInputError("The PDF is empty.")
    if b"/Encrypt" in data:
        raise PdfInputError("The PDF is password-protected and cannot be analyzed.")
    try:
        document = pdfium.PdfDocument(BytesIO(data), autoclose=True)
        if len(document) < 1:
            document.close()
            raise PdfInputError("The PDF is empty and has no pages.")
        page = document[0]
        width_points, height_points = page.get_size()
        if width_points <= 0 or height_points <= 0:
            page.close()
            document.close()
            raise PdfInputError("The first PDF page has invalid dimensions.")
        desired_scale = min(dpi, max_dpi) / 72.0
        max_pixels = max_megapixels * 1_000_000
        safe_scale = sqrt(max_pixels / (width_points * height_points))
        scale = min(desired_scale, safe_scale)
        bitmap = page.render(scale=scale)
        rgb = np.asarray(bitmap.to_pil().convert("RGB"))
        bitmap.close()
        page.close()
        document.close()
    except PdfInputError:
        raise
    except Exception as exc:
        message = str(exc).lower()
        if "password" in message or "security" in message:
            raise PdfInputError("The PDF is password-protected and cannot be analyzed.") from exc
        raise PdfInputError("The PDF is corrupt or cannot be opened.") from exc
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    bgr.setflags(write=False)
    return bgr
