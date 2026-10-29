from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class PageModel:
    image_bgr: np.ndarray
    gray: np.ndarray
    paper_mask: np.ndarray
    ink_mask: np.ndarray
    scale_to_original: float


def build_page(
    image_bgr: np.ndarray,
    *,
    working_long_side: int,
    paper_gray_min: int,
    ink_gray_max: int,
    morphology_kernel: int,
) -> PageModel:
    height, width = image_bgr.shape[:2]
    scale = min(1.0, working_long_side / max(height, width))
    if scale < 1.0:
        working = cv2.resize(image_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        working = image_bgr.copy()
    gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)
    _, paper = cv2.threshold(gray, paper_gray_min, 255, cv2.THRESH_BINARY)
    kernel = np.ones((morphology_kernel, morphology_kernel), np.uint8)
    paper = cv2.morphologyEx(paper, cv2.MORPH_CLOSE, kernel)
    ink = cv2.inRange(gray, 0, ink_gray_max)
    ink = cv2.bitwise_and(ink, paper)
    return PageModel(working, gray, paper, ink, 1.0 / scale)
