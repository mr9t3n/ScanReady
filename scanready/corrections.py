from __future__ import annotations

import cv2
import numpy as np

from .config import Config, load_config
from .report import AnalysisReport, Status


def _normalize_lighting(image: np.ndarray, config: Config) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    luminance, a_channel, b_channel = cv2.split(lab)
    sigma = float(config.value("corrections", "lighting_blur_sigma"))
    center = float(config.value("corrections", "lighting_center"))
    background = cv2.GaussianBlur(luminance, (0, 0), sigma)
    normalized = cv2.addWeighted(luminance, 1.0, background, -1.0, center)
    return cv2.cvtColor(cv2.merge((normalized, a_channel, b_channel)), cv2.COLOR_LAB2BGR)


def _enhance_contrast(image: np.ndarray, config: Config) -> np.ndarray:
    # Whole-page levels stretch: ink toward black, paper toward white. Local (CLAHE) equalization
    # turned faint paper texture into extra marks and lowered the measured text contrast.
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    luminance, a_channel, b_channel = cv2.split(lab)
    paper = float(np.percentile(luminance, float(config.value("contrast", "high_percentile"))))
    ink_pixels = luminance[luminance < paper - float(config.value("contrast", "foreground_delta"))]
    if ink_pixels.size == 0:
        return image.copy()
    ink = float(np.percentile(ink_pixels, float(config.value("contrast", "low_percentile"))))
    if paper - ink < 1.0:
        return image.copy()
    stretched = np.clip((luminance.astype(np.float32) - ink) * (255.0 / (paper - ink)), 0, 255).astype(np.uint8)
    return cv2.cvtColor(cv2.merge((stretched, a_channel, b_channel)), cv2.COLOR_LAB2BGR)


def _deskew_expanded(image: np.ndarray, degrees: float) -> np.ndarray:
    height, width = image.shape[:2]
    center = (width / 2.0, height / 2.0)
    matrix = cv2.getRotationMatrix2D(center, -degrees, 1.0)
    cosine = abs(matrix[0, 0])
    sine = abs(matrix[0, 1])
    new_width = int(np.ceil(height * sine + width * cosine))
    new_height = int(np.ceil(height * cosine + width * sine))
    matrix[0, 2] += new_width / 2.0 - center[0]
    matrix[1, 2] += new_height / 2.0 - center[1]
    return cv2.warpAffine(
        image,
        matrix,
        (new_width, new_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(30, 30, 30),
    )


def apply_corrections(
    original: np.ndarray,
    report: AnalysisReport,
    selected: set[str] | frozenset[str],
    *,
    config: Config | None = None,
) -> np.ndarray:
    active = config or load_config()
    offered = {
        check.correction
        for check in report.checks
        if check.status is Status.FAIL and check.recoverable and check.correction
    }
    requested = set(selected) & offered
    result = original.copy()
    if "normalize_lighting" in requested:
        result = _normalize_lighting(result, active)
    if "enhance_contrast" in requested:
        result = _enhance_contrast(result, active)
    if "deskew" in requested:
        skew = next(check for check in report.checks if check.name == "skew")
        result = _deskew_expanded(result, float(skew.measurement))
    return result
