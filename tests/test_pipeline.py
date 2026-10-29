from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np
import pytest

from scanready.image_io import load_document
from scanready.pipeline import analyze, correct_and_recheck, save_corrected
from scanready.report import Outcome
from scanready.synthetic import Defect, render_page


def png_bytes(image) -> bytes:
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def test_analyze_correct_recheck_lifecycle_keeps_source_bytes() -> None:
    source = png_bytes(render_page(Defect.LOW_CONTRAST))
    before = hashlib.sha256(source).hexdigest()
    loaded = load_document(source, display_name="page.png")
    report = analyze(loaded)
    corrected, corrected_report = correct_and_recheck(loaded, report, {"enhance_contrast"})
    assert hashlib.sha256(source).hexdigest() == before
    assert report.outcome is Outcome.READY_AFTER_CORRECTION
    assert corrected_report.outcome in {Outcome.READY, Outcome.READY_AFTER_CORRECTION}
    assert corrected is not loaded.image_bgr


def test_pdf_correction_saves_png_and_never_modifies_pdf(tmp_path: Path) -> None:
    source = Path(__file__).parent / "fixtures" / "multipage.pdf"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    loaded = load_document(source)
    report = analyze(loaded)
    corrected, _ = correct_and_recheck(loaded, report, set())
    output = save_corrected(corrected, source, tmp_path)
    assert output.suffix.lower() == ".png"
    assert output != source
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert output.exists()


def test_safe_save_uses_suffix_on_collision(tmp_path: Path) -> None:
    image = render_page()
    first = save_corrected(image, Path("page.jpg"), tmp_path, timestamp="20260926-120000")
    second = save_corrected(image, Path("page.jpg"), tmp_path, timestamp="20260926-120000")
    assert first.name == "page_scanready_20260926-120000.png"
    assert second.name == "page_scanready_20260926-120000_2.png"


def test_correct_with_review_skips_tonal_correction_that_does_not_help(monkeypatch) -> None:
    import scanready.corrections as correction_module
    from scanready.pipeline import correct_with_review

    loaded = load_document(png_bytes(render_page(Defect.LOW_CONTRAST)), display_name="page.png")
    report = analyze(loaded)
    monkeypatch.setattr(correction_module, "_enhance_contrast", lambda image, config: image.copy())
    review = correct_with_review(loaded, report, {"enhance_contrast"})
    assert review.applied == ()
    assert "enhance_contrast" in review.skipped
    assert "did not improve" in review.skipped["enhance_contrast"]
    assert review.image is not loaded.image_bgr
    assert (review.image == loaded.image_bgr).all()


def test_correct_with_review_applies_helpful_contrast_correction() -> None:
    from scanready.pipeline import correct_with_review

    loaded = load_document(png_bytes(render_page(Defect.LOW_CONTRAST)), display_name="page.png")
    review = correct_with_review(loaded, analyze(loaded), {"enhance_contrast", "not_offered"})
    assert review.applied == ("enhance_contrast",)
    assert not review.skipped
    contrast = next(change for change in review.changes if change.name == "low_contrast")
    assert contrast.after_measurement > contrast.before_measurement


def test_compare_reports_flags_checks_that_got_worse() -> None:
    from scanready.pipeline import compare_reports
    from scanready.report import AnalysisReport, CheckResult, Outcome, Status

    def report(crop: Status, shadow: Status) -> AnalysisReport:
        checks = (
            CheckResult("shadow", shadow, 0.0, shadow is Status.FAIL, None, 0.8, "ratio", 0.75, "m", "a"),
            CheckResult("crop", crop, 0.0, False, None, 0.0, "pixels", 9.0, "m", "a"),
        )
        return AnalysisReport(Outcome.READY, checks, (), (10, 10), 0.0, "hash")

    changes = {change.name: change for change in compare_reports(report(Status.WARN, Status.FAIL), report(Status.FAIL, Status.PASS))}
    assert changes["crop"].worse and not changes["crop"].improved
    assert changes["shadow"].improved and not changes["shadow"].worse


@pytest.mark.parametrize("rotation", [None, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE])
def test_orientation_advisory_flags_sideways_page_only(rotation) -> None:
    from scanready.pipeline import orientation_advisory

    page = render_page()
    image = page if rotation is None else cv2.rotate(page, rotation)
    advisory = orientation_advisory(image)
    if rotation is None:
        assert advisory is None
    else:
        assert advisory is not None and advisory.kind == "sideways"
        assert "sideways" in advisory.message.lower()


def test_orientation_advisory_ignores_pages_without_text_lines() -> None:
    from scanready.pipeline import orientation_advisory

    assert orientation_advisory(np.full((900, 1200, 3), 235, np.uint8)) is None


def test_page_coverage_advisory_flags_small_detected_page() -> None:
    from scanready.pipeline import page_coverage_advisory
    from scanready.report import AnalysisReport, CheckResult, Outcome, Region, Status

    def report(points) -> AnalysisReport:
        crop = CheckResult("crop", Status.PASS, 0.0, False, None, 5.0, "pixels", 9.0, "m", "a",
                           regions=(Region("page_boundary", points, "Page edge"),))
        return AnalysisReport(Outcome.READY, (crop,), (), (1000, 2000), 0.0, "hash")

    advisory = page_coverage_advisory(report(((300, 700), (700, 1300))))
    assert advisory is not None and advisory.kind == "page_coverage" and "12%" in advisory.message
    assert page_coverage_advisory(report(((0, 0), (1000, 2000)))) is None


def test_rotate_page_turns_pixels_and_keeps_original_bytes() -> None:
    from scanready.pipeline import rotate_page

    loaded = load_document(png_bytes(render_page()), display_name="page.png")
    turned = rotate_page(loaded, 1)
    assert turned.image_bgr.shape[:2] == loaded.image_bgr.shape[1::-1]
    assert turned.original_bytes == loaded.original_bytes
    assert rotate_page(loaded, 4).image_bgr.shape == loaded.image_bgr.shape
