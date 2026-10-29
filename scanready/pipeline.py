from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import time

import cv2
import numpy as np

from .checks import analyze_checks
from .config import Config, load_config
from .corrections import apply_corrections
from .decision import decide
from .image_io import load_document
from .report import AnalysisReport, LoadedPage, Status


def _report(image: np.ndarray, config: Config, started: float) -> AnalysisReport:
    checks = analyze_checks(image, config)
    return AnalysisReport(
        outcome=decide(checks),
        checks=checks,
        reasons=tuple(check.message for check in checks if check.status is Status.FAIL),
        image_size=(image.shape[1], image.shape[0]),
        elapsed_seconds=time.perf_counter() - started,
        config_sha256=config.sha256,
    )


def analyze(source, *, display_name: str | None = None, config: Config | None = None) -> AnalysisReport:
    active = config or load_config()
    started = time.perf_counter()
    loaded = source if isinstance(source, LoadedPage) else load_document(source, display_name=display_name, config=active)
    return _report(loaded.image_bgr, active, started)


_CORRECTION_ORDER = ("normalize_lighting", "enhance_contrast", "deskew")
# Tonal corrections are kept only when they improve the check they target (higher is better).
_TONAL_TARGETS = {"normalize_lighting": "shadow", "enhance_contrast": "low_contrast"}
_CORRECTION_NAMES = {"normalize_lighting": "Lighting correction", "enhance_contrast": "Contrast enhancement", "deskew": "Straightening"}
_STATUS_RANK = {Status.PASS: 0, Status.WARN: 1, Status.FAIL: 2}


@dataclass(frozen=True)
class CheckChange:
    name: str
    before_status: Status
    after_status: Status
    before_measurement: float
    after_measurement: float
    worse: bool
    improved: bool


@dataclass(frozen=True)
class CorrectionReview:
    image: np.ndarray
    report: AnalysisReport
    applied: tuple[str, ...]
    skipped: dict[str, str]
    changes: tuple[CheckChange, ...]


def compare_reports(before: AnalysisReport, after: AnalysisReport) -> tuple[CheckChange, ...]:
    later = {check.name: check for check in after.checks}
    changes = []
    for check in before.checks:
        other = later.get(check.name)
        if other is None:
            continue
        rank_before, rank_after = _STATUS_RANK[check.status], _STATUS_RANK[other.status]
        changes.append(CheckChange(
            check.name, check.status, other.status, float(check.measurement), float(other.measurement),
            worse=rank_after > rank_before, improved=rank_after < rank_before,
        ))
    return tuple(changes)


def _measurement(checks, name: str) -> float:
    return float(next(check.measurement for check in checks if check.name == name))


def correct_with_review(
    loaded: LoadedPage,
    report: AnalysisReport,
    selected: set[str] | frozenset[str],
    *,
    config: Config | None = None,
) -> CorrectionReview:
    active = config or load_config()
    offered = {
        check.correction
        for check in report.checks
        if check.status is Status.FAIL and check.recoverable and check.correction
    }
    image = loaded.image_bgr
    current_checks = report.checks
    applied: list[str] = []
    skipped: dict[str, str] = {}
    for name in _CORRECTION_ORDER:
        if name not in selected or name not in offered:
            continue
        candidate = apply_corrections(image, report, {name}, config=active)
        target = _TONAL_TARGETS.get(name)
        if target is not None:
            candidate_checks = analyze_checks(candidate, active)
            before, after = _measurement(current_checks, target), _measurement(candidate_checks, target)
            if after <= before:
                skipped[name] = (
                    f"{_CORRECTION_NAMES[name]} did not improve {target.replace('_', ' ')} "
                    f"({before:.3g} → {after:.3g}), so it was not applied."
                )
                continue
            current_checks = candidate_checks
        image = candidate
        applied.append(name)
    if image is loaded.image_bgr:
        image = image.copy()
    corrected_report = _report(image, active, time.perf_counter())
    return CorrectionReview(image, corrected_report, tuple(applied), skipped, compare_reports(report, corrected_report))


# Display-only advisories. They never change a check, the outcome, or evaluation metrics, so their
# constants live here rather than in config/thresholds.yaml. Values were chosen on calibration pages
# only (0/144 upright calibration pages flagged sideways; lowest calibration page coverage 83%).
_ORIENTATION_WORKING_SIDE = 1000
_ORIENTATION_MIN_EVIDENCE = 800
_ORIENTATION_RATIO = 1.8
_ORIENTATION_TRIM_LONGEST = 4
_PAGE_COVERAGE_MIN = 0.30


@dataclass(frozen=True)
class Advisory:
    kind: str
    message: str


def _line_evidence(ink: np.ndarray, kernel: np.ndarray, horizontal: bool) -> int:
    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, kernel)
    _, _, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    lengths = []
    for _, _, width, height, _ in stats[1:]:
        long_side, short_side = (width, height) if horizontal else (height, width)
        if long_side >= 40 and long_side >= 6 * short_side:
            lengths.append(int(long_side))
    lengths.sort()
    # Dropping the longest few removes page borders and copier edges; text contributes many lines.
    return sum(lengths[:-_ORIENTATION_TRIM_LONGEST]) if len(lengths) > _ORIENTATION_TRIM_LONGEST else 0


def orientation_advisory(image_bgr: np.ndarray) -> Advisory | None:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    scale = _ORIENTATION_WORKING_SIDE / max(height, width)
    gray = cv2.resize(gray, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    horizontal = _line_evidence(ink, np.ones((1, 15), np.uint8), horizontal=True)
    vertical = _line_evidence(ink, np.ones((15, 1), np.uint8), horizontal=False)
    if vertical >= _ORIENTATION_MIN_EVIDENCE and vertical >= _ORIENTATION_RATIO * max(horizontal, 1):
        return Advisory(
            "sideways",
            "This page looks sideways: its text lines run top to bottom. The quality checks can't detect "
            "page orientation, so rotate the page and re-check.",
        )
    return None


def page_coverage_advisory(report: AnalysisReport) -> Advisory | None:
    crop = next((check for check in report.checks if check.name == "crop"), None)
    if crop is None or not crop.regions or len(crop.regions[0].points) != 2:
        return None
    (left, top), (right, bottom) = crop.regions[0].points
    width, height = report.image_size
    coverage = max(0, right - left) * max(0, bottom - top) / max(1, width * height)
    if coverage >= _PAGE_COVERAGE_MIN:
        return None
    return Advisory(
        "page_coverage",
        f"The detected page covers only {coverage:.0%} of the image. If this isn't a photo or scan of a "
        "document page, these results may not be meaningful; if it is, move closer so the page fills the frame.",
    )


def advisories(image_bgr: np.ndarray, report: AnalysisReport) -> tuple[Advisory, ...]:
    return tuple(item for item in (orientation_advisory(image_bgr), page_coverage_advisory(report)) if item)


def rotate_page(loaded: LoadedPage, quarter_turns_clockwise: int) -> LoadedPage:
    turns = quarter_turns_clockwise % 4
    if turns == 0:
        return loaded
    code = {1: cv2.ROTATE_90_CLOCKWISE, 2: cv2.ROTATE_180, 3: cv2.ROTATE_90_COUNTERCLOCKWISE}[turns]
    return LoadedPage(loaded.original_bytes, loaded.source_kind, loaded.display_name, cv2.rotate(loaded.image_bgr, code))


def correct_and_recheck(
    loaded: LoadedPage,
    report: AnalysisReport,
    selected: set[str] | frozenset[str],
    *,
    config: Config | None = None,
) -> tuple[np.ndarray, AnalysisReport]:
    review = correct_with_review(loaded, report, selected, config=config)
    return review.image, review.report


def save_corrected(
    image: np.ndarray,
    input_path: str | Path,
    output_dir: str | Path,
    *,
    timestamp: str | None = None,
) -> Path:
    source = Path(input_path)
    destination_dir = Path(output_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{source.stem}_scanready_{stamp}"
    destination = destination_dir / f"{base}.png"
    suffix = 2
    while destination.exists():
        destination = destination_dir / f"{base}_{suffix}.png"
        suffix += 1
    try:
        if source.resolve() == destination.resolve():
            raise ValueError("Corrected output must not overwrite the input file.")
    except OSError:
        pass
    if not cv2.imwrite(str(destination), image):
        raise OSError(f"Could not save corrected image to {destination}")
    return destination
