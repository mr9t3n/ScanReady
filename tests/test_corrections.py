from __future__ import annotations

import hashlib

import cv2
import numpy as np
import pytest

from scanready.checks import analyze_checks, estimate_skew
from scanready.config import load_config
import scanready.corrections as correction_module
from scanready.corrections import apply_corrections
from scanready.decision import decide
from scanready.overlay import draw_evidence, evidence_lines, region_primitive
from scanready.report import AnalysisReport, CheckResult, Outcome, Region, Status
from scanready.synthetic import Defect, render_page


def report_for(image: np.ndarray) -> AnalysisReport:
    config = load_config()
    checks = analyze_checks(image, config)
    return AnalysisReport(decide(checks), checks, (), (image.shape[1], image.shape[0]), 0.0, config.sha256)


def digest(image: np.ndarray) -> str:
    return hashlib.sha256(image.tobytes()).hexdigest()


def test_corrections_return_new_array_without_mutating_original() -> None:
    original = render_page(Defect.LOW_CONTRAST)
    before = digest(original)
    corrected = apply_corrections(original, report_for(original), {"enhance_contrast"})
    assert corrected is not original
    assert digest(original) == before
    assert not np.array_equal(corrected, original)


@pytest.mark.parametrize("defect", [Defect.SKEW_POSITIVE, Defect.SKEW_NEGATIVE])
def test_deskew_preserves_content_and_reduces_residual(defect: Defect) -> None:
    original = render_page(defect)
    corrected = apply_corrections(original, report_for(original), {"deskew"})
    assert corrected.shape[0] >= original.shape[0]
    assert corrected.shape[1] >= original.shape[1]
    assert abs(estimate_skew(corrected)) < 1.5
    assert np.count_nonzero(cv2.cvtColor(corrected, cv2.COLOR_BGR2GRAY) < 100) >= int(
        np.count_nonzero(cv2.cvtColor(original, cv2.COLOR_BGR2GRAY) < 100) * 0.8
    )


@pytest.mark.parametrize(("degrees", "scale"), [(2.0, 0.95), (-2.0, 0.95), (15.0, 0.78), (-15.0, 0.78)])
def test_deskew_covers_full_correctable_range(degrees: float, scale: float) -> None:
    base = render_page()
    height, width = base.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), degrees, scale)
    original = cv2.warpAffine(base, matrix, (width, height), borderValue=(30, 30, 30))
    corrected = apply_corrections(original, report_for(original), {"deskew"})
    assert abs(estimate_skew(corrected)) < 1.5


def test_overlay_is_labeled_copy_and_input_is_unchanged() -> None:
    original = render_page(Defect.GLARE)
    before = digest(original)
    overlay = draw_evidence(original, report_for(original))
    assert overlay is not original
    assert digest(original) == before
    assert not np.array_equal(overlay, original)
    assert cv2.cvtColor(overlay, cv2.COLOR_BGR2GRAY).var() > 0


def test_overlay_contract_includes_measurements_and_skew_line() -> None:
    report = report_for(render_page(Defect.SKEW_POSITIVE))
    lines = evidence_lines(report)
    assert any(line.startswith("Blur:") and "limit" in line for line in lines)
    assert any(line.startswith("Low Contrast:") and "limit" in line for line in lines)
    skew_region = next(check for check in report.checks if check.name == "skew").regions[0]
    assert region_primitive(skew_region) == "line"


def _layout_report(width: int, height: int) -> AnalysisReport:
    names = ("blur", "skew", "glare", "shadow", "low_contrast", "crop")
    regions = {
        "skew": (Region("skew_axis", ((width // 10, height // 2), (width * 9 // 10, height // 2 + 20)), "Detected skew"),),
        "glare": (Region("glare", ((width // 20, height // 30), (width // 3, height // 8)), "Localized saturation"),),
        "crop": (Region("page_boundary", ((0, 0), (width, height)), "Page edge"),),
    }
    checks = tuple(
        CheckResult(name, Status.FAIL if name == "skew" else Status.WARN if name == "crop" else Status.PASS,
                    0.0, name == "skew", "deskew" if name == "skew" else None, 3420.0 if name == "blur" else 0.866,
                    "variance" if name == "blur" else "degrees", 150.0, "m", "a", regions=regions.get(name, ()))
        for name in names
    )
    return AnalysisReport(Outcome.READY_AFTER_CORRECTION, checks, (), (width, height), 0.0, "hash")


def _overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


@pytest.mark.parametrize(("width", "height"), [(518, 647), (1508, 2000), (2544, 3255), (1920, 1080)])
def test_overlay_layout_has_no_overlaps_small_and_full_size(width: int, height: int) -> None:
    from scanready.overlay import evidence_layout

    boxes = evidence_layout(_layout_report(width, height), width, height)
    lines = [box for box in boxes if box.kind == "line"]
    labels = [box for box in boxes if box.kind == "label"]
    panel = next(box for box in boxes if box.kind == "panel")
    assert len(lines) == 6 and len(labels) == 3
    for box in boxes:
        x0, y0, x1, y1 = box.rect
        assert 0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height, box
    text_boxes = lines + labels
    for index, first in enumerate(text_boxes):
        for second in text_boxes[index + 1:]:
            assert not _overlaps(first.rect, second.rect), (first, second)
    for label in labels:
        assert not _overlaps(label.rect, panel.rect), label
    assert lines[0].rect[1] > panel.rect[1]


def test_overlay_text_scales_with_image_width() -> None:
    from scanready.overlay import evidence_layout

    small = next(box for box in evidence_layout(_layout_report(518, 647), 518, 647) if box.kind == "line")
    large = next(box for box in evidence_layout(_layout_report(2544, 3255), 2544, 3255) if box.kind == "line")
    small_height = small.rect[3] - small.rect[1]
    large_height = large.rect[3] - large.rect[1]
    assert small_height >= 14
    assert large_height > small_height * 2.5


def test_overlay_draws_full_size_page_without_changing_input() -> None:
    original = np.full((2000, 1508, 3), 255, np.uint8)
    before = digest(original)
    overlay = draw_evidence(original, _layout_report(1508, 2000))
    assert overlay.shape == original.shape and overlay.dtype == np.uint8
    assert digest(original) == before
    assert int(overlay[40:120, 40:400].mean()) < 200


def test_only_offered_corrections_run_in_fixed_order(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(correction_module, "_normalize_lighting", lambda image, config: calls.append("lighting") or image.copy())
    monkeypatch.setattr(correction_module, "_enhance_contrast", lambda image, config: calls.append("contrast") or image.copy())
    monkeypatch.setattr(correction_module, "_deskew_expanded", lambda image, degrees: calls.append("deskew") or image.copy())
    checks = tuple(
        CheckResult(name, Status.FAIL, 1.0, True, correction, 7.0, "score", 1.0, "m", "a")
        for name, correction in (("shadow", "normalize_lighting"), ("low_contrast", "enhance_contrast"), ("skew", "deskew"))
    )
    report = AnalysisReport(Outcome.READY_AFTER_CORRECTION, checks, (), (1200, 900), 0.0, "hash")
    apply_corrections(render_page(), report, {"deskew", "not_offered", "enhance_contrast", "normalize_lighting"})
    assert calls == ["lighting", "contrast", "deskew"]


def test_contrast_correction_raises_low_contrast_measurement() -> None:
    original = render_page(Defect.LOW_CONTRAST)
    before = next(check for check in report_for(original).checks if check.name == "low_contrast")
    corrected = apply_corrections(original, report_for(original), {"enhance_contrast"})
    after = next(check for check in report_for(corrected).checks if check.name == "low_contrast")
    assert before.status is Status.FAIL
    assert after.measurement > before.measurement
