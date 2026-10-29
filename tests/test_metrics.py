from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest

from scanready import cli
from scanready.evaluate import CalibrationRun, DatasetAccessError, EvaluationRun, ManifestView, calibrate, evaluate_split, write_reports
from scanready.metrics import OUTCOMES, acceptance_checks, evaluation_metrics


@pytest.fixture
def report_root(request: pytest.FixtureRequest) -> Path:
    parent = (Path(__file__).resolve().parent / ".metrics-tmp").resolve()
    root = (parent / request.node.name).resolve()
    if parent not in root.parents:
        raise RuntimeError("Metrics test path escaped its controlled directory.")
    if root.exists():
        shutil.rmtree(root)
    (root / "data").mkdir(parents=True)
    yield root
    if root.exists():
        shutil.rmtree(root)


def _record(
    expected: str,
    predicted: str,
    *,
    expected_defects: set[str] | None = None,
    predicted_defects: set[str] | None = None,
    elapsed: float = 0.2,
    true_skew: float = 0.0,
    residual_skew: float | None = None,
    error: str = "",
) -> dict[str, object]:
    return {
        "image_id": f"image-{expected}-{predicted}-{true_skew}",
        "expected_outcome": expected,
        "predicted_outcome": predicted,
        "expected_defects": expected_defects or set(),
        "predicted_defects": predicted_defects or set(),
        "elapsed_seconds": elapsed,
        "true_skew_deg": true_skew,
        "residual_skew_deg": residual_skew,
        "error": error,
    }


def test_evaluation_metrics_use_fixed_three_by_three_outcome_order() -> None:
    records = [
        _record("READY", "READY"),
        _record("READY AFTER CORRECTION", "READY AFTER CORRECTION", expected_defects={"skew"}, predicted_defects={"skew"}),
        _record("RETRY CAPTURE", "READY", expected_defects={"blur"}),
        _record("RETRY CAPTURE", "RETRY CAPTURE", expected_defects={"glare"}, predicted_defects={"glare"}),
    ]

    metrics = evaluation_metrics(records)

    assert metrics["outcome_order"] == list(OUTCOMES)
    assert metrics["recommendation_accuracy"] == 0.75
    assert metrics["confusion_matrix"] == [[1, 0, 0], [0, 1, 0], [1, 0, 1]]
    assert metrics["per_defect"]["blur"] == {
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
        "support": 1,
        "true_positive": 0,
        "false_positive": 0,
        "false_negative": 1,
    }


def test_evaluation_metrics_handle_empty_classes_and_deskew_accuracy() -> None:
    records = [
        _record(
            "READY AFTER CORRECTION",
            "READY AFTER CORRECTION",
            expected_defects={"skew"},
            predicted_defects={"skew"},
            true_skew=7.0,
            residual_skew=1.0,
        ),
        _record(
            "READY AFTER CORRECTION",
            "READY AFTER CORRECTION",
            expected_defects={"skew"},
            predicted_defects={"skew"},
            true_skew=-12.0,
            residual_skew=2.0,
        ),
    ]

    metrics = evaluation_metrics(records)

    assert metrics["per_defect"]["glare"] == {
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
        "support": 0,
        "true_positive": 0,
        "false_positive": 0,
        "false_negative": 0,
    }
    assert metrics["deskew"] == {"eligible": 2, "successful": 1, "accuracy": 0.5}


def test_acceptance_checks_name_every_shortfall() -> None:
    records = [_record("READY", "READY", elapsed=11.0, error="decode failed")]
    metrics = evaluation_metrics(records)

    checks = acceptance_checks(records, metrics)

    assert checks["passed"] is False
    reasons = "\n".join(checks["failures"])
    assert "at least 50 held-out images" in reasons
    assert "at least 10 clean controls" in reasons
    assert "at least 6" in reasons
    assert "slowest analysis" in reasons
    assert "unhandled errors" in reasons


def test_calibration_view_refuses_heldout_access(report_root: Path) -> None:
    manifest = report_root / "data" / "manifest.csv"
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["image_id", "split"])
        writer.writeheader()
        writer.writerows(
            [
                {"image_id": "cal", "split": "calibration"},
                {"image_id": "hold", "split": "heldout"},
            ]
        )
    view = ManifestView(manifest, allowed_splits={"calibration"})

    assert [row["image_id"] for row in view.rows("calibration")] == ["cal"]
    with pytest.raises(DatasetAccessError, match="heldout.*not permitted"):
        view.rows("heldout")


def test_write_reports_emits_schema_csv_summary_and_confusion_png(report_root: Path) -> None:
    records = [
        _record("READY", "READY"),
        _record("RETRY CAPTURE", "RETRY CAPTURE", expected_defects={"blur"}, predicted_defects={"blur"}),
    ]
    metrics = evaluation_metrics(records)
    checks = acceptance_checks(records, metrics)

    write_reports(report_root, records, metrics, checks, package_versions={"python": "3.11.16"}, config_sha256="abc123", seed=415)

    results = report_root / "results"
    stored = json.loads((results / "metrics.json").read_text(encoding="utf-8"))
    assert stored["schema_version"] == 1
    assert stored["seed"] == 415
    assert stored["config_sha256"] == "abc123"
    assert stored["metrics"]["recommendation_accuracy"] == 1.0
    assert (results / "per_image.csv").read_text(encoding="utf-8").count("\n") == 3
    assert (results / "confusion_matrix.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    summary = (results / "summary.md").read_text(encoding="utf-8")
    assert "Qualification status: FAIL" in summary
    assert "Dataset composition" in summary


def test_evaluate_split_analyzes_only_requested_rows_and_records_failures(report_root: Path) -> None:
    images = report_root / "data" / "sources"
    images.mkdir()
    (images / "cal.png").write_bytes(b"calibration fixture")
    (images / "hold.png").write_bytes(b"heldout fixture")
    manifest = report_root / "data" / "manifest.csv"
    fields = [
        "image_id",
        "file",
        "source_page_id",
        "split",
        "origin",
        "defects",
        "expected_outcome",
        "correction_allowed",
        "true_skew_deg",
    ]
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            [
                {
                    "image_id": "cal",
                    "file": "data/sources/cal.png",
                    "source_page_id": "page-cal",
                    "split": "calibration",
                    "origin": "scholarly",
                    "defects": "clean",
                    "expected_outcome": "READY",
                    "correction_allowed": "false",
                    "true_skew_deg": "0",
                },
                {
                    "image_id": "hold",
                    "file": "data/sources/hold.png",
                    "source_page_id": "page-hold",
                    "split": "heldout",
                    "origin": "captured",
                    "defects": "blur",
                    "expected_outcome": "RETRY CAPTURE",
                    "correction_allowed": "false",
                    "true_skew_deg": "0",
                },
            ]
        )
    seen: list[str] = []

    def analyze_row(path: Path, row: dict[str, str]) -> dict[str, object]:
        seen.append(row["image_id"])
        return {
            "predicted_outcome": "RETRY CAPTURE",
            "predicted_defects": {"blur"},
            "elapsed_seconds": 0.25,
            "residual_skew_deg": None,
            "error": "",
        }

    run = evaluate_split(report_root, split="heldout", seed=415, analyze_row=analyze_row, config_sha256="fixture-hash")

    assert seen == ["hold"]
    assert len(run.records) == 1
    assert run.metrics["recommendation_accuracy"] == 1.0
    assert run.checks["passed"] is False
    assert (report_root / "results" / "metrics.json").is_file()


def test_cli_evaluate_check_uses_acceptance_status(monkeypatch: pytest.MonkeyPatch) -> None:
    failed_run = EvaluationRun(records=[], metrics={}, checks={"passed": False, "failures": ["not enough data"]})
    received: dict[str, object] = {}

    def fake_ensure_generated_data(root: Path, *, seed: int) -> None:
        received.update(generation_root=root, generation_seed=seed)

    def fake_evaluate_split(root: Path, *, split: str, seed: int) -> EvaluationRun:
        received.update(root=root, split=split, seed=seed)
        return failed_run

    monkeypatch.setattr(cli, "ensure_generated_data", fake_ensure_generated_data, raising=False)
    monkeypatch.setattr(cli, "evaluate_split", fake_evaluate_split, raising=False)

    result = cli.main(["evaluate", "--root", "fixture-root", "--split", "heldout", "--seed", "415", "--check"])

    assert result == 1
    assert received == {
        "generation_root": Path("fixture-root"),
        "generation_seed": 415,
        "root": Path("fixture-root"),
        "split": "heldout",
        "seed": 415,
    }


def test_calibrate_reads_only_calibration_and_does_not_change_thresholds(report_root: Path) -> None:
    (report_root / "config").mkdir()
    thresholds = report_root / "config" / "thresholds.yaml"
    thresholds.write_text("example:\n  threshold: 1\n", encoding="utf-8")
    images = report_root / "data" / "sources"
    images.mkdir()
    (images / "cal.png").write_bytes(b"calibration fixture")
    (images / "hold.png").write_bytes(b"heldout fixture")
    with (report_root / "data" / "manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        fields = ["image_id", "file", "source_page_id", "split", "origin", "defects", "expected_outcome", "correction_allowed", "true_skew_deg"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            [
                {"image_id": "cal", "file": "data/sources/cal.png", "source_page_id": "page-cal", "split": "calibration", "origin": "scholarly", "defects": "clean", "expected_outcome": "READY", "correction_allowed": "false", "true_skew_deg": "0"},
                {"image_id": "hold", "file": "data/sources/hold.png", "source_page_id": "page-hold", "split": "heldout", "origin": "captured", "defects": "blur", "expected_outcome": "RETRY CAPTURE", "correction_allowed": "false", "true_skew_deg": "0"},
            ]
        )
    with (report_root / "data/manifest.csv").open() as handle:
        calibration_rows = [row for row in csv.DictReader(handle) if row["split"] == "calibration"]
    with (report_root / "data/calibration.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(calibration_rows)
    original = thresholds.read_bytes()
    seen: list[str] = []

    def analyze_row(path: Path, row: dict[str, str]) -> dict[str, object]:
        seen.append(row["image_id"])
        return {"predicted_outcome": "READY", "predicted_defects": set(), "elapsed_seconds": 0.1, "residual_skew_deg": None, "error": ""}

    run = calibrate(report_root, seed=415, analyze_row=analyze_row, config_sha256="before-hash")

    assert isinstance(run, CalibrationRun)
    assert seen == ["cal"]
    assert thresholds.read_bytes() == original
    report = (report_root / "results" / "calibration.md").read_text(encoding="utf-8")
    assert "Held-out isolation: enforced" in report
    assert "Transform-only variants: unscored" in report
    assert "No threshold changes were applied automatically" in report


def test_cli_calibrate_routes_only_to_calibration(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    run = CalibrationRun(records=[{"image_id": "cal"}], metrics={"recommendation_accuracy": 1.0}, report_path=Path("results/calibration.md"))
    received: dict[str, object] = {}

    def fake_calibrate(root: Path, *, seed: int) -> CalibrationRun:
        received.update(root=root, seed=seed)
        return run

    monkeypatch.setattr(cli, "calibrate", fake_calibrate, raising=False)

    assert cli.main(["calibrate", "--root", "fixture-root", "--seed", "415"]) == 0
    assert received == {"root": Path("fixture-root"), "seed": 415}
    assert "Calibration review complete for 1 image" in capsys.readouterr().out



def test_calibration_never_opens_shared_or_heldout_files(report_root: Path, monkeypatch) -> None:
    calibration = report_root / "data/calibration.csv"
    calibration.write_text("image_id,file,split,defects,expected_outcome\ncal,data/cal.png,calibration,clean,READY\n")
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path.name in {"manifest.csv", "heldout.csv", "sources.csv", "captured_labels.csv", "metrics.json"}:
            pytest.fail(f"Calibration opened forbidden file: {path}")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    run = calibrate(report_root, analyze_row=lambda path, row: {
        "predicted_outcome": "READY", "predicted_defects": set(), "elapsed_seconds": 0.1,
    }, config_sha256="fixture")
    assert [record["image_id"] for record in run.records] == ["cal"]


def test_calibration_refuses_missing_or_contaminated_split_file(report_root: Path) -> None:
    with pytest.raises(DatasetAccessError, match="prepare-data"):
        calibrate(report_root)
    (report_root / "data/calibration.csv").write_text("image_id,split\nsecret,heldout\n")
    with pytest.raises(DatasetAccessError, match="heldout"):
        calibrate(report_root)
    assert not (report_root / "results/calibration.md").exists()


def test_calibration_rejects_generated_rows_in_scored_calibration(report_root: Path) -> None:
    fields = [
        "image_id",
        "file",
        "source_page_id",
        "split",
        "origin",
        "defects",
        "expected_outcome",
        "correction_allowed",
        "true_skew_deg",
    ]
    with (report_root / "data/calibration.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(
            [
                {
                    "image_id": "original",
                    "file": "data/sources/original.png",
                    "source_page_id": "page-original",
                    "split": "calibration",
                    "origin": "scholarly",
                    "defects": "clean",
                    "expected_outcome": "READY",
                    "correction_allowed": "false",
                    "true_skew_deg": "0",
                },
                {
                    "image_id": "legacy-generated",
                    "file": "data/generated/calibration/legacy.png",
                    "source_page_id": "page-original",
                    "split": "calibration",
                    "origin": "generated",
                    "defects": "blur",
                    "expected_outcome": "RETRY CAPTURE",
                    "correction_allowed": "false",
                    "true_skew_deg": "0",
                },
            ]
        )

    with pytest.raises(DatasetAccessError, match="generated.*prepare-data"):
        calibrate(
            report_root,
            analyze_row=lambda path, row: pytest.fail("Generated calibration row was scored"),
            config_sha256="fixture",
        )

    assert not (report_root / "results/calibration.md").exists()


def test_reports_separate_real_captures_from_generated_and_scanned_data(report_root: Path) -> None:
    records = [
        dict(_record("READY", "RETRY CAPTURE"), origin="captured"),
        dict(_record("READY", "READY"), origin="scholarly"),
        dict(_record("READY", "READY"), origin="generated"),
    ]
    metrics = evaluation_metrics(records)
    write_reports(report_root, iter(records), metrics, acceptance_checks(records, metrics),
                  package_versions={}, config_sha256="fixture", seed=415)
    payload = json.loads((report_root / "results/metrics.json").read_text())
    assert payload["by_origin"]["captured"]["image_count"] == 1
    assert payload["by_origin"]["captured"]["recommendation_accuracy"] == 0.0
    assert payload["by_origin"]["scholarly"]["recommendation_accuracy"] == 1.0
    assert payload["by_origin"]["generated"]["image_count"] == 1
    summary = (report_root / "results/summary.md").read_text()
    assert "Real-capture performance" in summary
    assert "0.000 (1 images)" in summary


def test_reports_explicitly_disclose_no_real_captures(report_root: Path) -> None:
    records = [dict(_record("READY", "READY"), origin="scholarly")]
    metrics = evaluation_metrics(records)
    write_reports(report_root, records, metrics, acceptance_checks(records, metrics),
                  package_versions={}, config_sha256="fixture", seed=415)
    assert "No real captures were evaluated" in (report_root / "results/summary.md").read_text()


def test_deskew_denominator_includes_failed_attempts_but_not_unrecoverable_pages() -> None:
    records = [
        _record('READY AFTER CORRECTION', 'RETRY CAPTURE', expected_defects={'skew'}, true_skew=7, residual_skew=None),
        _record('READY AFTER CORRECTION', 'READY AFTER CORRECTION', expected_defects={'skew'}, true_skew=7, residual_skew=.3),
        _record('RETRY CAPTURE', 'RETRY CAPTURE', expected_defects={'skew', 'crop'}, true_skew=7, residual_skew=.2),
    ]
    assert evaluation_metrics(records)['deskew'] == {'eligible': 2, 'successful': 1, 'accuracy': .5}


def test_summary_marks_unmeasurable_rates_and_deskew_as_not_applicable(report_root: Path) -> None:
    from scanready.evaluate import render_summary

    records = [_record("READY", "READY"), _record("READY", "RETRY CAPTURE", predicted_defects={"cropped_edges"})]
    metrics = evaluation_metrics(records)
    write_reports(report_root, records, metrics, acceptance_checks(records, metrics),
                  package_versions={}, config_sha256="fixture", seed=415)
    summary = (report_root / "results/summary.md").read_text(encoding="utf-8")
    assert "- Deskew accuracy: N/A (no eligible cases)" in summary
    assert "- cropped_edges: precision —, recall —, F1 — (support 0; false positives 1)" in summary
    assert "0.000 (0/0)" not in summary
    payload = json.loads((report_root / "results/metrics.json").read_text(encoding="utf-8"))
    assert render_summary(payload) == summary
