from __future__ import annotations

import csv
import importlib.metadata
import json
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .config import load_config
from .image_io import load_document
from .metrics import acceptance_checks, canonical_defect, evaluation_metrics
from .pipeline import analyze, correct_and_recheck
from .report import Status


class DatasetAccessError(RuntimeError):
    """Raised when a caller requests a dataset split it is not allowed to inspect."""


@dataclass(frozen=True)
class EvaluationRun:
    records: list[dict[str, Any]]
    metrics: dict[str, Any]
    checks: dict[str, Any]


@dataclass(frozen=True)
class CalibrationRun:
    records: list[dict[str, Any]]
    metrics: dict[str, Any]
    report_path: Path


class ManifestView:
    def __init__(self, manifest_path: Path, *, allowed_splits: set[str] | frozenset[str]) -> None:
        self.manifest_path = manifest_path
        self.allowed_splits = frozenset(allowed_splits)

    def rows(self, split: str) -> list[dict[str, str]]:
        if split not in self.allowed_splits:
            allowed = ", ".join(sorted(self.allowed_splits)) or "none"
            raise DatasetAccessError(f"Access to split {split!r} is not permitted; allowed: {allowed}.")
        with self.manifest_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if "split" not in (reader.fieldnames or []):
                raise DatasetAccessError("The dataset manifest has no split column.")
            return [dict(row) for row in reader if row.get("split") == split]


def _record_for_storage(record: Mapping[str, Any]) -> dict[str, Any]:
    stored: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, (set, frozenset, tuple, list)):
            stored[key] = ";".join(sorted(str(item) for item in value))
        elif value is None:
            stored[key] = ""
        else:
            stored[key] = value
    return stored


def _write_confusion(path: Path, matrix: list[list[int]], labels: list[str]) -> None:
    figure, axis = plt.subplots(figsize=(7, 6))
    image = axis.imshow(matrix, cmap="Blues")
    figure.colorbar(image, ax=axis)
    axis.set_xticks(range(len(labels)), labels, rotation=25, ha="right")
    axis.set_yticks(range(len(labels)), labels)
    axis.set_xlabel("Predicted outcome")
    axis.set_ylabel("Expected outcome")
    axis.set_title("ScanReady outcome confusion matrix")
    for row_index, row in enumerate(matrix):
        for column_index, count in enumerate(row):
            axis.text(column_index, row_index, str(count), ha="center", va="center")
    figure.tight_layout()
    figure.savefig(path, dpi=160)
    plt.close(figure)


def _deskew_text(values: Mapping[str, Any]) -> str:
    deskew = values["deskew"]
    if not deskew["eligible"]:
        return "N/A (no eligible cases)"
    return f"{deskew['accuracy']:.3f} ({deskew['successful']}/{deskew['eligible']})"


def _defect_text(name: str, values: Mapping[str, Any]) -> str:
    if not values["support"]:
        return (f"- {name}: precision —, recall —, F1 — "
                f"(support 0; false positives {values['false_positive']})")
    return (f"- {name}: precision {values['precision']:.3f}, recall {values['recall']:.3f}, "
            f"F1 {values['f1']:.3f}, support {values['support']}")


def render_summary(payload: Mapping[str, Any]) -> str:
    """Render summary.md from a metrics.json payload; used by write_reports and for re-rendering."""
    metrics, checks, by_origin = payload["metrics"], payload["acceptance"], payload["by_origin"]
    captured = by_origin.get("captured")
    capture_lines = (
        [
            f"- Recommendation accuracy: {captured['recommendation_accuracy']:.3f} ({captured['image_count']} images)",
            f"- Slowest analysis: {captured['timing']['slowest_seconds']:.3f} seconds",
            f"- Deskew accuracy: {_deskew_text(captured)}",
        ] if captured else ["No real captures were evaluated; capture performance is unmeasured."]
    )
    status = "PASS" if checks["passed"] else "FAIL"
    defect_lines = [_defect_text(name, values) for name, values in sorted(metrics["per_defect"].items())]
    failure_lines = [f"- {failure}" for failure in checks["failures"]] or ["- None"]
    return "\n".join(
        [
            "# ScanReady Held-Out Evaluation",
            "",
            f"**Qualification status: {status}**",
            "",
            "## Dataset composition",
            "",
            f"- Images: {metrics['image_count']}",
            *[f"- {origin}: {values['image_count']} images" for origin, values in by_origin.items()],
            f"- Seed: {payload['seed']}",
            f"- Configuration SHA-256: `{payload['config_sha256']}`",
            "",
            "## Outcome performance",
            "",
            f"- Recommendation accuracy: {metrics['recommendation_accuracy']:.3f}",
            f"- Slowest analysis: {metrics['timing']['slowest_seconds']:.3f} seconds",
            f"- Deskew accuracy: {_deskew_text(metrics)}",
            "",
            "## Real-capture performance",
            "",
            *capture_lines,
            "",
            "## Per-defect performance",
            "",
            *defect_lines,
            "",
            "## Acceptance failures",
            "",
            *failure_lines,
            "",
        ]
    )


def write_reports(
    root: Path,
    records: Iterable[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    checks: Mapping[str, Any],
    *,
    package_versions: Mapping[str, str],
    config_sha256: str,
    seed: int,
) -> None:
    root = root.resolve()
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    rows = list(records)
    by_origin = {
        origin: evaluation_metrics(row for row in rows if (row.get("origin") or "unknown") == origin)
        for origin in sorted({row.get("origin") or "unknown" for row in rows})
    }
    stored_records = [_record_for_storage(record) for record in rows]
    preferred_fields = [
        "image_id",
        "file",
        "source_page_id",
        "origin",
        "expected_outcome",
        "predicted_outcome",
        "expected_defects",
        "predicted_defects",
        "elapsed_seconds",
        "true_skew_deg",
        "residual_skew_deg",
        "error",
    ]
    extra_fields = sorted({key for record in stored_records for key in record} - set(preferred_fields))
    fields = preferred_fields + extra_fields
    with (results / "per_image.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(stored_records)

    payload = {
        "schema_version": 1,
        "seed": seed,
        "config_sha256": config_sha256,
        "package_versions": dict(sorted(package_versions.items())),
        "metrics": metrics,
        "acceptance": checks,
        "by_origin": by_origin,
    }
    (results / "metrics.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_confusion(
        results / "confusion_matrix.png",
        list(metrics["confusion_matrix"]),
        list(metrics["outcome_order"]),
    )

    summary = render_summary(payload)
    (results / "summary.md").write_text(summary, encoding="utf-8")


def _expected_defects(row: Mapping[str, str]) -> set[str]:
    raw = row.get("defects", "")
    values = {value for value in raw.replace(",", ";").split(";") if value.strip()}
    return {
        canonical_defect(value)
        for value in values
        if canonical_defect(value) not in {"", "clean", "none", "unknown"}
    }


def _default_analyzer(root: Path, config_sha256: str | None) -> tuple[Callable[[Path, dict[str, str]], dict[str, Any]], str]:
    config = load_config(root / "config" / "thresholds.yaml")

    def analyze_row(path: Path, row: dict[str, str]) -> dict[str, Any]:
        loaded = load_document(path, config=config)
        report = analyze(loaded, config=config)
        predicted = {
            canonical_defect(check.name)
            for check in report.checks
            if check.status is Status.FAIL
        }
        residual: float | None = None
        expected = _expected_defects(row)
        true_skew = abs(float(row.get("true_skew_deg") or 0.0))
        if (row.get("expected_outcome") == "READY AFTER CORRECTION"
                and "skew" in expected and 2.0 <= true_skew <= 15.0):
            skew_check = next(check for check in report.checks if check.name == "skew")
            if skew_check.status is Status.FAIL and skew_check.recoverable and skew_check.correction == "deskew":
                _, corrected_report = correct_and_recheck(loaded, report, {"deskew"}, config=config)
                corrected_skew = next(check for check in corrected_report.checks if check.name == "skew")
                residual = abs(float(corrected_skew.measurement))
            else:
                residual = true_skew
        return {
            "predicted_outcome": report.outcome.value,
            "predicted_defects": predicted,
            "elapsed_seconds": report.elapsed_seconds,
            "residual_skew_deg": residual,
            "error": "",
        }

    return analyze_row, config_sha256 or config.sha256


def _package_versions() -> dict[str, str]:
    versions = {"python": sys.version.split()[0]}
    for distribution in ("numpy", "opencv-python-headless", "Pillow", "pypdfium2", "pandas", "matplotlib", "streamlit"):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = "not-installed"
    return versions


def _collect_records(
    root: Path,
    manifest_rows: list[dict[str, str]],
    analyze_row: Callable[[Path, dict[str, str]], dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in manifest_rows:
        file_path = (root / row.get("file", "")).resolve()
        if root not in file_path.parents or not file_path.is_file():
            result = {
                "predicted_outcome": "RETRY CAPTURE",
                "predicted_defects": set(),
                "elapsed_seconds": 0.0,
                "residual_skew_deg": None,
                "error": f"Manifest image is missing or unsafe: {row.get('file', '')}",
            }
        else:
            try:
                result = analyze_row(file_path, row)
            except Exception as exc:  # Evaluation must record per-image failures instead of aborting the run.
                result = {
                    "predicted_outcome": "RETRY CAPTURE",
                    "predicted_defects": set(),
                    "elapsed_seconds": 0.0,
                    "residual_skew_deg": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        records.append(
            {
                "image_id": row.get("image_id", ""),
                "file": row.get("file", ""),
                "source_page_id": row.get("source_page_id", ""),
                "origin": row.get("origin", ""),
                "expected_outcome": row.get("expected_outcome", ""),
                "predicted_outcome": result["predicted_outcome"],
                "expected_defects": _expected_defects(row),
                "predicted_defects": set(result.get("predicted_defects", set())),
                "elapsed_seconds": float(result.get("elapsed_seconds", 0.0)),
                "true_skew_deg": float(row.get("true_skew_deg") or 0.0),
                "residual_skew_deg": result.get("residual_skew_deg"),
                "error": str(result.get("error", "")),
            }
        )
    return records


def calibrate(
    root: Path,
    *,
    seed: int = 415,
    analyze_row: Callable[[Path, dict[str, str]], dict[str, Any]] | None = None,
    config_sha256: str | None = None,
) -> CalibrationRun:
    root = root.resolve()
    # Never open the combined manifest or any held-out file during calibration.
    calibration_path = root / "data/calibration.csv"
    if not calibration_path.is_file():
        raise DatasetAccessError("Calibration manifest is missing; run prepare-data first.")
    with calibration_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if "split" not in (reader.fieldnames or []):
            raise DatasetAccessError("Calibration manifest has no split column; run prepare-data.")
        manifest_rows = []
        for row in reader:
            if row.get("split") != "calibration":
                raise DatasetAccessError("Calibration manifest contains a heldout or invalid split; run prepare-data.")
            if row.get("origin") == "generated":
                raise DatasetAccessError(
                    "Scored calibration contains generated rows; run prepare-data before calibration."
                )
            manifest_rows.append(dict(row))
    if not manifest_rows:
        raise DatasetAccessError("Calibration manifest is empty; add labeled calibration data and run prepare-data.")
    active_analyzer = analyze_row
    active_config_sha256 = config_sha256
    if active_analyzer is None:
        active_analyzer, active_config_sha256 = _default_analyzer(root, config_sha256)
    records = _collect_records(root, manifest_rows, active_analyzer)
    metrics = evaluation_metrics(records)
    report_path = root / "results" / "calibration.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = "\n".join(
        [
            "# ScanReady Calibration Review",
            "",
            f"- Date (UTC): {datetime.now(timezone.utc).isoformat()}",
            f"- Seed: {seed}",
            f"- Calibration images: {len(records)}",
            f"- Configuration SHA-256: `{active_config_sha256 or 'not-recorded'}`",
            f"- Current recommendation accuracy: {metrics['recommendation_accuracy']:.3f}",
            "- Held-out isolation: enforced",
            "- Transform-only variants: unscored (separate sidecar; excluded from this count)",
            "",
            "## Threshold recommendations",
            "",
            "No threshold changes were applied automatically. Review calibration-only false positives, false negatives, and per-defect measurements before approving any edit to `config/thresholds.yaml`.",
            "",
            "## Threshold change audit",
            "",
            "No changes recorded for this run.",
            "",
        ]
    )
    report_path.write_text(report, encoding="utf-8")
    return CalibrationRun(records=records, metrics=metrics, report_path=report_path)


def evaluate_split(
    root: Path,
    *,
    split: str = "heldout",
    seed: int = 415,
    analyze_row: Callable[[Path, dict[str, str]], dict[str, Any]] | None = None,
    config_sha256: str | None = None,
) -> EvaluationRun:
    root = root.resolve()
    view = ManifestView(root / "data" / "manifest.csv", allowed_splits={split})
    manifest_rows = view.rows(split)
    active_analyzer = analyze_row
    active_config_sha256 = config_sha256
    if active_analyzer is None:
        active_analyzer, active_config_sha256 = _default_analyzer(root, config_sha256)
    if not active_config_sha256:
        active_config_sha256 = "not-recorded"

    records = _collect_records(root, manifest_rows, active_analyzer)

    metrics = evaluation_metrics(records)
    checks = acceptance_checks(records, metrics)
    write_reports(
        root,
        records,
        metrics,
        checks,
        package_versions=_package_versions(),
        config_sha256=active_config_sha256,
        seed=seed,
    )
    return EvaluationRun(records=records, metrics=metrics, checks=checks)

