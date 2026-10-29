from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


def _write_development_status(root: Path, *, report_hash_override: str | None = None) -> None:
    results = root / "results"
    report_names = (
        "metrics.json",
        "per_image.csv",
        "confusion_matrix.png",
        "summary.md",
        "development-disclosure.md",
    )
    report_hashes = {
        name: hashlib.sha256((results / name).read_bytes()).hexdigest()
        for name in report_names
    }
    if report_hash_override is not None:
        report_hashes["metrics.json"] = report_hash_override
    metrics = json.loads((results / "metrics.json").read_text())
    status = {
        "schema_version": 1,
        "population_id": "development-approved-v1",
        "candidate_label": "Evaluated with disclosed shortfalls",
        "qualification": False,
        "working_population": {"total": 202, "calibration": 144, "heldout": 58},
        "evaluated_population": {
            "total": 184,
            "calibration": 137,
            "heldout": metrics["metrics"]["image_count"],
            "scholarly": 184,
            "captured": 0,
        },
        "omitted_captures": {"total": 18, "calibration": 7, "heldout": 11},
        "report_sha256": report_hashes,
        "failed_checks": metrics["acceptance"]["failures"],
        "platform_gates": {
            "fresh_windows_offline_extraction": "unperformed",
            "native_macos": "unperformed",
            "manual_candidate_walkthrough": "unperformed",
        },
    }
    (results / "development_status.json").write_text(json.dumps(status), encoding="utf-8")


def test_app_starts_with_browser_upload_contract() -> None:
    app = Path(__file__).parents[1] / "app.py"
    run = AppTest.from_file(str(app)).run(timeout=15)
    assert not run.exception
    assert run.title[0].value == "ScanReady"
    assert "JPEG, JPG, PNG, or PDF" in run.markdown[0].value
    assert run.selectbox[0].options == ["None", "Clean document", "Correctable document", "Retry capture document"]


def test_demo_change_replaces_result_and_correction_recheck_is_visible() -> None:
    app = Path(__file__).parents[1] / "app.py"
    run = AppTest.from_file(str(app)).run(timeout=15)
    run.selectbox[0].select("Clean document").run(timeout=15)
    assert "READY" in run.subheader[0].value

    run.selectbox[0].select("Correctable document").run(timeout=15)
    assert "READY AFTER CORRECTION" in run.subheader[0].value
    assert run.checkbox
    run.button[0].click().run(timeout=15)
    assert any("Corrected re-check" in item.value for item in run.subheader)


def test_retry_capture_offers_no_corrections_and_names_unfixable_checks() -> None:
    app = Path(__file__).parents[1] / "app.py"
    run = AppTest.from_file(str(app)).run(timeout=30)
    run.selectbox[0].select("Retry capture document").run(timeout=30)
    assert not run.exception
    assert "RETRY CAPTURE" in run.subheader[0].value
    assert not run.checkbox
    assert not [button for button in run.button if "Apply" in button.label]
    assert any("Blur" in item.value and "retake" in item.value.lower() for item in run.error)


def test_correction_recheck_shows_what_changed() -> None:
    app = Path(__file__).parents[1] / "app.py"
    run = AppTest.from_file(str(app)).run(timeout=30)
    run.selectbox[0].select("Correctable document").run(timeout=30)
    run.button[0].click().run(timeout=30)
    assert not run.exception
    assert any("What changed" in item.value for item in run.subheader)
    assert any("Skew" in item.value and "FAIL" in item.value and "PASS" in item.value for item in run.markdown)


def test_sideways_page_offers_rotation_and_rechecks_rotated_page(tmp_path) -> None:
    import shutil
    import cv2

    source = Path(__file__).parents[1]
    shutil.copy(source / "app.py", tmp_path / "app.py")
    shutil.copytree(source / "config", tmp_path / "config")
    shutil.copytree(source / "demo_images", tmp_path / "demo_images")
    demo = tmp_path / "demo_images/clean_document.jpg"
    cv2.imwrite(str(demo), cv2.rotate(cv2.imread(str(demo)), cv2.ROTATE_90_CLOCKWISE))
    run = AppTest.from_file(str(tmp_path / "app.py")).run(timeout=30)
    run.selectbox[0].select("Clean document").run(timeout=30)
    assert not run.exception
    assert any("sideways" in item.value.lower() for item in run.warning)
    rotate = next(button for button in run.button if "Rotate left" in button.label)
    rotate.click().run(timeout=30)
    assert not run.exception
    assert not any("sideways" in item.value.lower() for item in run.warning)
    assert any("Rotated" in item.value for item in run.info)


@pytest.mark.parametrize(("demo", "kind", "text"), [
    ("Clean document", "success", "READY"),
    ("Correctable document", "warning", "READY AFTER CORRECTION"),
    ("Retry capture document", "error", "RETRY CAPTURE"),
])
def test_verdict_banner_is_coloured_and_explained(demo, kind, text) -> None:
    app = Path(__file__).parents[1] / "app.py"
    run = AppTest.from_file(str(app)).run(timeout=30)
    run.selectbox[0].select(demo).run(timeout=30)
    assert not run.exception
    assert any(item.value.startswith(text) and "—" in item.value for item in getattr(run, kind))


def test_result_explains_warnings_legend_and_keeps_numbers_in_details() -> None:
    app = Path(__file__).parents[1] / "app.py"
    run = AppTest.from_file(str(app)).run(timeout=30)
    run.selectbox[0].select("Clean document").run(timeout=30)
    assert not run.exception
    captions = [item.value for item in run.caption]
    assert any("don't block READY" in value for value in captions)
    assert any("green = pass" in value for value in captions)
    assert any("Details" in item.label for item in run.expander)
    assert not any("Measured" in item.value for item in run.markdown)


def test_straightened_result_explains_corner_fill_and_offers_download() -> None:
    app = Path(__file__).parents[1] / "app.py"
    run = AppTest.from_file(str(app)).run(timeout=30)
    run.selectbox[0].select("Correctable document").run(timeout=30)
    run.button[0].click().run(timeout=30)
    assert not run.exception
    assert any("corner" in item.value.lower() for item in run.caption)
    assert run.get("download_button")


def test_results_tab_displays_reports_and_honest_shortfalls(tmp_path) -> None:
    import shutil
    from scanready.config import load_config
    from scanready.evaluate import write_reports
    from scanready.metrics import evaluation_metrics, acceptance_checks

    source = Path(__file__).parents[1]
    shutil.copy(source / "app.py", tmp_path / "app.py")
    shutil.copytree(source / "config", tmp_path / "config")
    records = [
        {"image_id": f"fixture-{index}", "expected_outcome": "READY", "predicted_outcome": "READY",
         "expected_defects": set(), "predicted_defects": set(), "elapsed_seconds": 0.2}
        for index in range(47)
    ]
    metrics = evaluation_metrics(records)
    write_reports(tmp_path, records, metrics, acceptance_checks(records, metrics),
                  package_versions={"python": "fixture"}, config_sha256=load_config().sha256, seed=415)
    (tmp_path / "results/development-disclosure.md").write_text("fixture", encoding="utf-8")
    _write_development_status(tmp_path)
    run = AppTest.from_file(str(tmp_path / "app.py")).run(timeout=15)
    assert not run.exception
    assert any("Evaluated with disclosed shortfalls" in item.value for item in run.info)
    assert any("not met" in item.value and "cut line" in item.value for item in run.warning)
    assert not any("not qualified" in item.value.lower() for item in [*run.error, *run.warning, *run.info])
    assert any("50 held-out" in item.value for item in run.markdown)
    assert any("zero captured" in item.value.lower() for item in run.warning)
    assert any("no eligible deskew" in item.value.lower() for item in run.warning)
    assert run.metric and run.dataframe
    deskew_tile = next(metric for metric in run.metric if metric.label == "Deskew success")
    assert deskew_tile.value == "N/A"
    defects = run.dataframe[0].value
    assert set(defects["precision"]) == {"—"} and "false_positive" in defects.columns
    assert run.table and set(run.table[0].value.columns) == {"Package", "Version"}
    assert any("requirement" in item.value and "not met" in item.value for item in run.caption)


def test_results_tab_does_not_label_missing_status_as_development_candidate(tmp_path) -> None:
    import shutil
    source = Path(__file__).parents[1]
    shutil.copy(source / "app.py", tmp_path / "app.py")
    shutil.copytree(source / "config", tmp_path / "config")
    run = AppTest.from_file(str(tmp_path / "app.py")).run(timeout=15)
    assert not run.exception
    assert any("No held-out evaluation results" in item.value for item in run.info)
    assert not any("disclosed shortfalls" in item.value for item in run.info)


@pytest.mark.parametrize("status_contents", ["malformed", "hash-mismatch"])
def test_results_tab_rejects_invalid_or_stale_development_status(tmp_path, status_contents) -> None:
    import shutil
    from scanready.config import load_config
    from scanready.evaluate import write_reports
    from scanready.metrics import evaluation_metrics, acceptance_checks

    source = Path(__file__).parents[1]
    shutil.copy(source / "app.py", tmp_path / "app.py")
    shutil.copytree(source / "config", tmp_path / "config")
    records = [{"image_id": "fixture", "expected_outcome": "READY", "predicted_outcome": "READY",
                "expected_defects": set(), "predicted_defects": set(), "elapsed_seconds": 0.2}]
    metrics = evaluation_metrics(records)
    write_reports(tmp_path, records, metrics, acceptance_checks(records, metrics),
                  package_versions={}, config_sha256=load_config().sha256, seed=415)
    (tmp_path / "results/development-disclosure.md").write_text("fixture", encoding="utf-8")
    if status_contents == "malformed":
        (tmp_path / "results/development_status.json").write_text("{}", encoding="utf-8")
    else:
        _write_development_status(tmp_path, report_hash_override="0" * 64)

    run = AppTest.from_file(str(tmp_path / "app.py")).run(timeout=15)
    assert not run.exception
    assert any("invalid or stale" in item.value.lower() for item in run.warning)
    assert not any("Evaluated with disclosed shortfalls" in item.value for item in run.info)


def test_results_tab_handles_invalid_report_without_traceback(tmp_path) -> None:
    import shutil
    shutil.copy(Path(__file__).parents[1] / "app.py", tmp_path / "app.py")
    (tmp_path / "results").mkdir()
    (tmp_path / "results/metrics.json").write_text('{"schema_version": 1, "metrics": null}')
    run = AppTest.from_file(str(tmp_path / "app.py")).run(timeout=15)
    assert not run.exception
    assert any("could not" in item.value.lower() for item in run.error)


def test_results_tab_warns_when_thresholds_have_changed(tmp_path) -> None:
    import shutil
    from scanready.config import load_config
    from scanready.evaluate import write_reports
    from scanready.metrics import evaluation_metrics, acceptance_checks

    source = Path(__file__).parents[1]
    shutil.copy(source / "app.py", tmp_path / "app.py")
    shutil.copytree(source / "config", tmp_path / "config")
    records = [{"image_id": "fixture", "expected_outcome": "READY", "predicted_outcome": "READY",
                "expected_defects": set(), "predicted_defects": set(), "elapsed_seconds": 0.2}]
    metrics = evaluation_metrics(records)
    write_reports(tmp_path, records, metrics, acceptance_checks(records, metrics),
                  package_versions={}, config_sha256=load_config().sha256, seed=415)
    thresholds = tmp_path / "config/thresholds.yaml"
    thresholds.write_text(thresholds.read_text() + "\n# Changed configuration\n")
    run = AppTest.from_file(str(tmp_path / "app.py")).run(timeout=15)
    assert not run.exception
    assert any("different configuration" in item.value for item in run.warning)
    assert not run.success


@pytest.mark.parametrize(("field", "value"), [
    ("per_defect", {"blur": {"precision": [1, {"unexpected": 2}]}}),
    ("recommendation_accuracy", 10**400),
])
def test_results_tab_rejects_unrenderable_metrics(tmp_path, field, value) -> None:
    import json
    import shutil
    from scanready.config import load_config
    from scanready.evaluate import write_reports
    from scanready.metrics import evaluation_metrics, acceptance_checks

    source = Path(__file__).parents[1]
    shutil.copy(source / "app.py", tmp_path / "app.py")
    shutil.copytree(source / "config", tmp_path / "config")
    records = [{"image_id": "fixture", "expected_outcome": "READY", "predicted_outcome": "READY",
                "expected_defects": set(), "predicted_defects": set(), "elapsed_seconds": 0.2}]
    metrics = evaluation_metrics(records)
    write_reports(tmp_path, records, metrics, acceptance_checks(records, metrics),
                  package_versions={}, config_sha256=load_config().sha256, seed=415)
    path = tmp_path / "results/metrics.json"
    original = json.loads(path.read_text())
    original["metrics"][field] = value
    path.write_text(json.dumps(original))
    run = AppTest.from_file(str(tmp_path / "app.py")).run(timeout=15)
    assert not run.exception
    assert any("could not" in item.value.lower() for item in run.error)
