from __future__ import annotations

from enum import Enum

import cv2
import numpy as np


class Defect(str, Enum):
    BLUR = "blur"
    SKEW_POSITIVE = "skew_positive"
    SKEW_NEGATIVE = "skew_negative"
    GLARE = "glare"
    SHADOW = "shadow"
    LOW_CONTRAST = "low_contrast"
    CROP = "crop"


def _base_page() -> np.ndarray:
    image = np.full((900, 1200, 3), 30, dtype=np.uint8)
    cv2.rectangle(image, (70, 60), (1130, 840), (235, 235, 235), -1)
    cv2.rectangle(image, (70, 60), (1130, 840), (180, 180, 180), 3)
    cv2.putText(image, "SCAN READY", (160, 170), cv2.FONT_HERSHEY_SIMPLEX, 2.1, (20, 20, 20), 5)
    for row in range(250, 760, 55):
        length = 760 if row % 110 else 880
        cv2.line(image, (150, row), (150 + length, row), (35, 35, 35), 6)
    return image


def _rotate(image: np.ndarray, degrees: float) -> np.ndarray:
    height, width = image.shape[:2]
    # Keep the entire page inside the capture while isolating skew as the only defect.
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), degrees, 0.88)
    return cv2.warpAffine(image, matrix, (width, height), flags=cv2.INTER_LINEAR, borderValue=(30, 30, 30))


def render_page(defect: Defect | None = None) -> np.ndarray:
    image = _base_page()
    if defect is None:
        return image
    if defect is Defect.BLUR:
        return cv2.GaussianBlur(image, (31, 31), 9)
    if defect is Defect.SKEW_POSITIVE:
        return _rotate(image, 7.0)
    if defect is Defect.SKEW_NEGATIVE:
        return _rotate(image, -7.0)
    if defect is Defect.GLARE:
        result = image.copy()
        cv2.ellipse(result, (650, 440), (150, 105), -12, 0, 360, (255, 255, 255), -1)
        return result
    if defect is Defect.SHADOW:
        gradient = np.linspace(0.42, 1.0, image.shape[1], dtype=np.float32)[None, :, None]
        return np.clip(image.astype(np.float32) * gradient, 0, 255).astype(np.uint8)
    if defect is Defect.LOW_CONTRAST:
        result = image.copy()
        yy, xx = np.indices(result.shape[:2])
        inside_page = (xx >= 70) & (xx <= 1130) & (yy >= 60) & (yy <= 840)
        paper = np.all(result > 100, axis=2) & inside_page
        ink = np.all(result < 100, axis=2) & inside_page
        result[paper] = 195
        result[ink] = 145
        return result
    if defect is Defect.CROP:
        # Remove actual text/line content, not only the outside paper margin.
        return image[100:800, 240:1050].copy()
    raise ValueError(f"Unknown synthetic defect: {defect}")
