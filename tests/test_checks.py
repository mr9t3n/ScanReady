from __future__ import annotations

import pytest

from scanready.checks import analyze_checks, estimate_skew
from scanready.config import load_config
from scanready.report import Status
from scanready.synthetic import Defect, render_page


ORDER = ["blur", "skew", "glare", "shadow", "low_contrast", "crop"]


def by_name(image):
    return {check.name: check for check in analyze_checks(image, load_config())}


def test_clean_page_returns_six_ordered_nonfail_checks() -> None:
    checks = analyze_checks(render_page(), load_config())
    assert [check.name for check in checks] == ORDER
    assert all(check.status is not Status.FAIL for check in checks)


@pytest.mark.parametrize(
    ("defect", "check_name"),
    [
        (Defect.BLUR, "blur"),
        (Defect.SKEW_POSITIVE, "skew"),
        (Defect.GLARE, "glare"),
        (Defect.SHADOW, "shadow"),
        (Defect.LOW_CONTRAST, "low_contrast"),
        (Defect.CROP, "crop"),
    ],
)
def test_each_synthetic_defect_triggers_its_check(defect: Defect, check_name: str) -> None:
    assert by_name(render_page(defect))[check_name].status is Status.FAIL


@pytest.mark.parametrize("degrees", [7.0, -7.0])
def test_skew_estimate_is_within_half_degree(degrees: float) -> None:
    image = render_page(Defect.SKEW_POSITIVE if degrees > 0 else Defect.SKEW_NEGATIVE)
    assert estimate_skew(image) == pytest.approx(degrees, abs=0.5)


def test_regions_are_reported_in_original_image_coordinates() -> None:
    image = render_page()
    doubled = __import__("cv2").resize(image, None, fx=2.0, fy=2.0)
    crop = by_name(doubled)["crop"]
    assert crop.regions[0].points[1][0] > 2200


def test_measurable_skew_includes_an_original_coordinate_region() -> None:
    image = render_page(Defect.SKEW_POSITIVE)
    skew = by_name(image)["skew"]
    assert skew.regions
    assert skew.regions[0].kind == "skew_axis"


def test_degenerate_paper_candidate_still_returns_six_checks() -> None:
    import numpy as np

    image = np.zeros((480, 640, 3), dtype=np.uint8)
    image[240, 320] = 255
    checks = analyze_checks(image, load_config())
    assert [item.name for item in checks] == ORDER


def test_full_page_white_scan_with_sparse_crisp_text_is_not_glare_crop_or_low_contrast() -> None:
    import cv2
    import numpy as np
    from scanready.decision import decide
    from scanready.report import Outcome

    scan = np.full((1200, 900, 3), 255, dtype=np.uint8)
    cv2.putText(scan, 'Clear scanned document', (70, 140), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (20, 20, 20), 2)
    cv2.putText(scan, 'Readable text with wide white margins', (70, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (20, 20, 20), 2)
    checks = by_name(scan)
    assert checks['glare'].status is not Status.FAIL
    assert checks['crop'].status is not Status.FAIL
    assert checks['low_contrast'].status is Status.PASS
    assert decide(tuple(checks.values())) is Outcome.READY


def test_scan_with_dark_content_cut_at_boundary_still_fails_crop() -> None:
    import cv2
    import numpy as np
    scan = np.full((1200, 900, 3), 255, dtype=np.uint8)
    for y in range(100, 1100, 30):
        cv2.line(scan, (0, y), (600, y), (20, 20, 20), 8)
    assert by_name(scan)['crop'].status is Status.FAIL


def test_white_table_cells_are_not_glare() -> None:
    import cv2
    import numpy as np
    scan = np.full((1200, 900, 3), 255, dtype=np.uint8)
    for y in range(200, 1001, 200):
        cv2.line(scan, (100, y), (800, y), (0, 0, 0), 2)
    for x in range(100, 801, 175):
        cv2.line(scan, (x, 200), (x, 1000), (0, 0, 0), 2)
    assert by_name(scan)['glare'].status is not Status.FAIL


def test_localized_glare_touching_paper_boundary_is_detected() -> None:
    import cv2
    image = render_page()
    cv2.rectangle(image, (68, 350), (350, 430), (255, 255, 255), -1)
    assert by_name(image)['glare'].status is Status.FAIL


def test_one_clipped_line_is_detected_without_large_edge_coverage() -> None:
    import cv2
    import numpy as np
    scan = np.full((1200, 900, 3), 255, dtype=np.uint8)
    cv2.putText(scan, 'A readable document', (70, 140), cv2.FONT_HERSHEY_SIMPLEX, 1, (20, 20, 20), 2)
    cv2.putText(scan, 'Clipped text line', (-40, 1100), cv2.FONT_HERSHEY_SIMPLEX, 1, (20, 20, 20), 2)
    assert by_name(scan)['crop'].status is Status.FAIL


def test_scanner_frame_with_clear_inner_margins_is_not_clipped_content() -> None:
    import cv2
    import numpy as np
    scan = np.full((1200, 900, 3), 255, dtype=np.uint8)
    cv2.rectangle(scan, (0, 0), (899, 1199), (15, 15, 15), 9)
    cv2.putText(scan, 'Complete document', (90, 180), cv2.FONT_HERSHEY_SIMPLEX, 1, (20, 20, 20), 2)
    assert by_name(scan)['crop'].status is not Status.FAIL


@pytest.mark.parametrize("check_name", ["crop", "low_contrast"])
def test_bright_photo_background_touching_edge_does_not_make_page_cropped(check_name) -> None:
    import cv2
    import numpy as np
    scan = np.full((1200, 900, 3), 150, dtype=np.uint8)
    cv2.rectangle(scan, (110, 90), (790, 1100), (225, 225, 225), -1)
    for y in range(200, 950, 90):
        cv2.putText(scan, 'Clear document text', (160, y), cv2.FONT_HERSHEY_SIMPLEX, .8, (35, 35, 35), 2)
    checks = by_name(scan)
    assert checks[check_name].status is not Status.FAIL


def test_broken_scanner_frame_without_clipped_text_warns_instead_of_rejecting() -> None:
    import cv2
    import numpy as np
    scan = np.full((1200, 900, 3), 255, dtype=np.uint8)
    cv2.line(scan, (0, 0), (0, 480), (20, 20, 20), 7)
    cv2.line(scan, (0, 720), (0, 1199), (20, 20, 20), 7)
    cv2.putText(scan, 'Complete text inside the margin', (90, 180), cv2.FONT_HERSHEY_SIMPLEX, .8, (20, 20, 20), 2)
    assert by_name(scan)['crop'].status is not Status.FAIL


def test_local_ink_contrast_is_not_dominated_by_broad_shading() -> None:
    import cv2
    import numpy as np
    scan = np.full((1200, 900, 3), 225, dtype=np.uint8)
    cv2.rectangle(scan, (100, 100), (800, 1000), (170, 170, 170), -1)
    for y in range(200, 900, 70):
        cv2.putText(scan, 'Readable dark text', (160, y), cv2.FONT_HERSHEY_SIMPLEX, 1, (25, 25, 25), 2)
    assert by_name(scan)['low_contrast'].status is not Status.FAIL


def test_connected_table_rules_clipped_at_edge_are_not_background() -> None:
    import cv2
    import numpy as np
    scan = np.full((1200, 900, 3), 255, dtype=np.uint8)
    for y in range(100, 1100, 30):
        cv2.line(scan, (0, y), (600, y), (20, 20, 20), 8)
    cv2.line(scan, (500, 100), (500, 1090), (20, 20, 20), 3)
    assert by_name(scan)['crop'].status is Status.FAIL
