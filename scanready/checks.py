from __future__ import annotations

from collections.abc import Callable

import cv2
import numpy as np

from .config import Config, load_config
from .page import PageModel, build_page
from .report import CheckResult, Region, Status


def estimate_skew(image_bgr: np.ndarray, config: Config | None = None) -> float:
    active = config or load_config()
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(
        gray,
        int(active.value("skew", "edge_low")),
        int(active.value("skew", "edge_high")),
        apertureSize=3,
    )
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 1800,
        threshold=int(active.value("skew", "hough_votes")),
        minLineLength=max(1, round(image_bgr.shape[1] * float(active.value("skew", "min_line_fraction")))),
        maxLineGap=int(active.value("skew", "max_line_gap")),
    )
    if lines is None:
        return 0.0
    angles: list[float] = []
    for x1, y1, x2, y2 in lines[:, 0]:
        angle = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        candidate_limit = float(active.value("skew", "candidate_angle_degrees"))
        if -candidate_limit <= angle <= candidate_limit:
            angles.append(angle)
    if not angles:
        return 0.0
    return -float(np.median(angles))


def _paper_bounds(page: PageModel, config: Config) -> tuple[int, int, int, int]:
    contours, _ = cv2.findContours(page.paper_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 0, 0, page.gray.shape[1], page.gray.shape[0]
    largest = max(contours, key=cv2.contourArea)
    minimum_area = page.gray.size * float(config.value("page", "min_paper_area_fraction"))
    if cv2.contourArea(largest) < minimum_area:
        return 0, 0, page.gray.shape[1], page.gray.shape[0]
    return cv2.boundingRect(largest)


def _region(page: PageModel, kind: str, points: tuple[tuple[int, int], ...], label: str) -> Region:
    scale = page.scale_to_original
    original_points = tuple((int(round(x * scale)), int(round(y * scale))) for x, y in points)
    return Region(kind, original_points, label)


def _blur(page: PageModel, config: Config) -> CheckResult:
    score = float(cv2.Laplacian(page.gray, cv2.CV_64F).var())
    fail = float(config.value("blur", "fail_variance"))
    warn = float(config.value("blur", "warn_variance"))
    status = Status.FAIL if score < fail else Status.WARN if score < warn else Status.PASS
    return CheckResult(
        "blur", status, max(0.0, (fail - score) / fail), False, None, score, "variance", fail,
        "The page is too blurry." if status is Status.FAIL else "Sharpness is acceptable.",
        "Retake the page while holding the camera steady." if status is Status.FAIL else "No action needed.",
    )


def _skew(page: PageModel, config: Config) -> CheckResult:
    degrees = estimate_skew(page.image_bgr, config)
    absolute = abs(degrees)
    limit = float(config.value("skew", "pass_degrees"))
    maximum = float(config.value("skew", "max_correctable_degrees"))
    status = Status.PASS if absolute < limit else Status.FAIL
    recoverable = status is Status.FAIL and absolute <= maximum
    regions: tuple[Region, ...] = ()
    if status is Status.FAIL:
        height, width = page.gray.shape
        radians = np.radians(-degrees)
        half = width * 0.4
        center_x, center_y = width / 2, height / 2
        rise = np.tan(radians) * half
        regions = (_region(page, "skew_axis", ((round(center_x - half), round(center_y - rise)), (round(center_x + half), round(center_y + rise))), "Detected skew"),)
    return CheckResult(
        "skew", status, absolute / maximum, recoverable, "deskew" if recoverable else None,
        degrees, "degrees", limit,
        "The page is tilted." if status is Status.FAIL else "Page angle is acceptable.",
        "Straighten the page." if recoverable else ("Retake the page more squarely." if status is Status.FAIL else "No action needed."),
        regions=regions,
        details={"absolute_degrees": absolute},
    )


def _glare(page: PageModel, config: Config) -> CheckResult:
    x, y, width, height = _paper_bounds(page, config)
    roi = page.gray[y : y + height, x : x + width]
    bright = (roi >= int(config.value("glare", "bright_gray_min"))).astype(np.uint8)
    saturated_fraction = float(np.count_nonzero(bright) / max(1, bright.size))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(bright, connectivity=8)
    minimum_area = bright.size * float(config.value("glare", "min_component_fraction"))
    selected = np.zeros_like(bright, dtype=bool)
    background = float(np.percentile(roi, float(config.value("glare", "background_percentile"))))
    distinct_from_paper = int(config.value("glare", "bright_gray_min")) - background >= float(config.value("glare", "minimum_background_delta"))
    for component in range(1, count):
        left, top, component_width, component_height, area = stats[component]
        # A reflection must be brighter than the typical paper, not merely
        # enclosed by text/table lines. Edge-touching reflections still count.
        if distinct_from_paper and area >= minimum_area:
            selected |= labels == component
    fraction = float(np.count_nonzero(selected) / max(1, selected.size))
    limit = float(config.value("glare", "fail_fraction"))
    status = Status.FAIL if fraction >= limit else Status.WARN if saturated_fraction >= limit else Status.PASS
    regions: tuple[Region, ...] = ()
    if status is Status.FAIL:
        ys, xs = np.nonzero(selected)
        regions = (_region(page, "glare", ((x + int(xs.min()), y + int(ys.min())), (x + int(xs.max()), y + int(ys.max()))), "Localized saturation"),)
    message = "A localized saturated region may hide page content." if status is Status.FAIL else (
        "Bright paper is present; saturation alone cannot establish glare." if status is Status.WARN else "No significant glare detected.")
    action = "Move the light or camera and retake the page." if status is Status.FAIL else (
        "Check visually for lost detail in white regions." if status is Status.WARN else "No action needed.")
    return CheckResult(
        "glare", status, fraction / limit, False, None, fraction, "localized fraction", limit,
        message, action, regions=regions, details={"saturated_fraction": saturated_fraction},
    )


def _shadow(page: PageModel, config: Config) -> CheckResult:
    x, y, width, height = _paper_bounds(page, config)
    inset_fraction = float(config.value("shadow", "inset_fraction"))
    inset_x = max(0, round(width * inset_fraction))
    inset_y = max(0, round(height * inset_fraction))
    roi = page.gray[y + inset_y : y + height - inset_y, x + inset_x : x + width - inset_x]
    if roi.size == 0:
        roi = page.gray
    column_level = np.percentile(roi, float(config.value("shadow", "column_percentile")), axis=0)
    low = float(np.percentile(column_level, float(config.value("shadow", "low_percentile"))))
    high = float(np.percentile(column_level, float(config.value("shadow", "high_percentile"))))
    uniformity = low / max(1.0, high)
    limit = float(config.value("shadow", "fail_uniformity"))
    status = Status.FAIL if uniformity < limit else Status.PASS
    region = (_region(page, "shadow", ((x, y), (x + width, y + height)), "Uneven lighting"),) if status is Status.FAIL else ()
    return CheckResult(
        "shadow", status, max(0.0, (limit - uniformity) / limit), status is Status.FAIL,
        "normalize_lighting" if status is Status.FAIL else None, uniformity, "ratio", limit,
        "Uneven shadow crosses the page." if status is Status.FAIL else "Lighting is sufficiently even.",
        "Apply lighting correction or retake under even light." if status is Status.FAIL else "No action needed.",
        regions=region,
    )


def _content_without_edge_background(dark: np.ndarray, config: Config) -> np.ndarray:
    """Ignore broad continuous edge-connected frames/background, retain cut glyphs.

    A dark surrounding surface is not evidence that page content was clipped.
    Thin glyphs and lines crossing an edge remain candidate content.
    """
    count, labels, _, _ = cv2.connectedComponentsWithStats(dark.astype(np.uint8), connectivity=8)
    limit = float(config.value("crop", "background_edge_fraction"))
    background = np.zeros(count, dtype=bool)
    for edge in (labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]):
        # Separate contacts joined inside a table are not a continuous frame.
        starts = np.r_[0, np.flatnonzero(edge[1:] != edge[:-1]) + 1]
        ends = np.r_[starts[1:], len(edge)]
        long_runs = (ends - starts) / len(edge) >= limit
        background[edge[starts[long_runs]]] = True
    background[0] = True
    return ~background[labels]


def _contrast(page: PageModel, config: Config) -> CheckResult:
    x, y, width, height = _paper_bounds(page, config)
    roi = page.gray[y : y + height, x : x + width]
    kernel_size = int(config.value("contrast", "local_background_kernel"))
    local_paper = cv2.morphologyEx(roi, cv2.MORPH_CLOSE, np.ones((kernel_size, kernel_size), np.uint8))
    ink_difference = local_paper.astype(np.float32) - roi.astype(np.float32)
    foreground = ink_difference[ink_difference > float(config.value("contrast", "foreground_delta"))]
    minimum = roi.size * float(config.value("contrast", "minimum_foreground_fraction"))
    contrast_range = float(np.percentile(foreground, float(config.value("contrast", "foreground_percentile")))) if foreground.size >= max(1, minimum) else 0.0
    fail = float(config.value("contrast", "fail_range"))
    unrecoverable = float(config.value("contrast", "unrecoverable_range"))
    status = Status.FAIL if contrast_range < fail else Status.PASS
    recoverable = status is Status.FAIL and contrast_range >= unrecoverable
    return CheckResult(
        "low_contrast", status, max(0.0, (fail - contrast_range) / fail), recoverable,
        "enhance_contrast" if recoverable else None, contrast_range, "gray levels", fail,
        "Text-to-paper contrast is too low." if status is Status.FAIL else "Contrast is acceptable.",
        "Enhance contrast." if recoverable else ("Retake with clearer contrast." if status is Status.FAIL else "No action needed."),
    )


def _crop(page: PageModel, config: Config) -> CheckResult:
    x, y, width, height = _paper_bounds(page, config)
    image_height, image_width = page.gray.shape
    margin = float(config.value("crop", "edge_margin_fraction"))
    touches = x <= image_width * margin or y <= image_height * margin or x + width >= image_width * (1 - margin) or y + height >= image_height * (1 - margin)
    strip_x = max(1, round(image_width * margin))
    strip_y = max(1, round(image_height * margin))
    paper_level = float(np.percentile(page.gray, float(config.value("contrast", "high_percentile"))))
    dark = page.gray < paper_level - float(config.value("crop", "edge_darkening"))
    edge_densities = []
    if x <= image_width * margin:
        edge_densities.append(float(dark[:, :strip_x].mean()))
    if y <= image_height * margin:
        edge_densities.append(float(dark[:strip_y, :].mean()))
    if x + width >= image_width * (1 - margin):
        edge_densities.append(float(dark[:, -strip_x:].mean()))
    if y + height >= image_height * (1 - margin):
        edge_densities.append(float(dark[-strip_y:, :].mean()))
    density = max(edge_densities, default=0.0)
    content = _content_without_edge_background(dark, config)
    _, _, components, _ = cv2.connectedComponentsWithStats(content.astype(np.uint8), connectivity=8)
    minimum_area = page.gray.size * float(config.value("crop", "minimum_edge_component_fraction"))
    edge_content = False
    for left, top, component_width, component_height, area in components[1:]:
        if area >= minimum_area and (left == 0 or top == 0 or left + component_width == image_width or top + component_height == image_height):
            edge_content = True
            break
    clipped = touches and edge_content
    status = Status.FAIL if clipped else Status.WARN if touches else Status.PASS
    region = (_region(page, "page_boundary", ((x, y), (x + width, y + height)), "Page edge"),)
    message = "Content-like marks reach the image edge; check for truncation." if clipped else (
        "The page reaches the image boundary; blank margins do not establish cropping." if touches else "All page edges are visible.")
    action = "Check the edge and retake with space around the page if content is missing." if touches else "No action needed."
    return CheckResult(
        "crop", status, 1.0 if clipped else 0.0, False, None, min(x, y, image_width - (x + width), image_height - (y + height)),
        "pixels", min(image_width, image_height) * margin,
        message, action, regions=region, details={"edge_dark_fraction": density},
    )



CHECKS: tuple[Callable[[PageModel, Config], CheckResult], ...] = (_blur, _skew, _glare, _shadow, _contrast, _crop)


def analyze_checks(image_bgr: np.ndarray, config: Config) -> tuple[CheckResult, ...]:
    page = build_page(
        image_bgr,
        working_long_side=int(config.value("intake", "working_long_side")),
        paper_gray_min=int(config.value("page", "paper_gray_min")),
        ink_gray_max=int(config.value("page", "ink_gray_max")),
        morphology_kernel=int(config.value("page", "morphology_kernel")),
    )
    return tuple(check(page, config) for check in CHECKS)
