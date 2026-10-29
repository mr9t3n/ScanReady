from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .checks import analyze_checks
from .config import load_config
from .decision import decide
from .demos import check_demos
from .dataset import DatasetError, ensure_generated_data, generate_data, prepare_data
from .evaluate import DatasetAccessError, calibrate, evaluate_split
from .image_io import InputError, load_document
from .launch import LaunchError, launch_app
from .report import AnalysisReport, Outcome
from .synthetic import Defect, render_page


def analyze_path(path: Path) -> AnalysisReport:
    config = load_config()
    started = time.perf_counter()
    loaded = load_document(path, config=config)
    checks = analyze_checks(loaded.image_bgr, config)
    outcome = decide(checks)
    return AnalysisReport(
        outcome=outcome,
        checks=checks,
        reasons=tuple(check.message for check in checks if check.status.value == "FAIL"),
        image_size=(loaded.image_bgr.shape[1], loaded.image_bgr.shape[0]),
        elapsed_seconds=time.perf_counter() - started,
        config_sha256=config.sha256,
    )


def selftest() -> bool:
    config = load_config()
    cases = [
        (None, Outcome.READY),
        (Defect.BLUR, Outcome.RETRY_CAPTURE),
        (Defect.SKEW_POSITIVE, Outcome.READY_AFTER_CORRECTION),
        (Defect.GLARE, Outcome.RETRY_CAPTURE),
        (Defect.SHADOW, Outcome.READY_AFTER_CORRECTION),
        (Defect.LOW_CONTRAST, Outcome.READY_AFTER_CORRECTION),
        (Defect.CROP, Outcome.RETRY_CAPTURE),
    ]
    failures: list[str] = []
    for defect, expected in cases:
        actual = decide(analyze_checks(render_page(defect), config))
        if actual is not expected:
            failures.append(f"{defect or 'clean'}: expected {expected.value}, got {actual.value}")
    if failures:
        print("Self-test failed:", *failures, sep="\n- ", file=sys.stderr)
        return False
    print(f"ScanReady self-test passed ({len(cases)} cases).")
    return True


def _report_json(report: AnalysisReport) -> str:
    return json.dumps(
        {
            "outcome": report.outcome.value,
            "reasons": report.reasons,
            "image_size": report.image_size,
            "elapsed_seconds": round(report.elapsed_seconds, 4),
            "config_sha256": report.config_sha256,
            "checks": [
                {
                    "name": check.name,
                    "status": check.status.value,
                    "measurement": check.measurement,
                    "unit": check.unit,
                    "limit": check.limit,
                    "message": check.message,
                    "next_action": check.next_action,
                }
                for check in report.checks
            ],
        },
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scanready")
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze_parser = subparsers.add_parser("analyze", help="Analyze one JPEG, JPG, PNG, or PDF document.")
    analyze_parser.add_argument("document", type=Path)
    subparsers.add_parser("selftest", help="Run deterministic synthetic checks.")
    demo_parser = subparsers.add_parser("check-demos", help="Verify demo outcomes, corrected PNG, and unchanged originals.")
    demo_parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    launch_parser = subparsers.add_parser("launch", help="Launch the local ScanReady browser application.")
    launch_parser.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically.")
    launch_parser.add_argument("--smoke", action="store_true", help="Stop after the health check succeeds.")
    launch_parser.add_argument("--port", type=int, help="Use a specific localhost port.")
    prepare_parser = subparsers.add_parser("prepare-data", help="Normalize declared raw evaluation images.")
    prepare_parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    prepare_parser.add_argument("--seed", type=int, default=415)
    generate_parser = subparsers.add_parser("generate-data", help="Regenerate unscored calibration transform variants.")
    generate_parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    generate_parser.add_argument("--seed", type=int, default=415)
    evaluate_parser = subparsers.add_parser("evaluate", help="Run held-out evaluation and write result artifacts.")
    evaluate_parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    evaluate_parser.add_argument("--split", choices=["heldout"], default="heldout")
    evaluate_parser.add_argument("--seed", type=int, default=415)
    evaluate_parser.add_argument("--check", action="store_true", help="Exit nonzero when acceptance checks fail.")
    calibrate_parser = subparsers.add_parser("calibrate", help="Review calibration-only performance without reading held-out rows.")
    calibrate_parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    calibrate_parser.add_argument("--seed", type=int, default=415)
    args = parser.parse_args(argv)
    if args.command == "selftest":
        return 0 if selftest() else 1
    if args.command == "check-demos":
        try:
            messages = check_demos(args.root)
        except (InputError, OSError, ValueError) as exc:
            print(f"ScanReady demo check failed: {exc}", file=sys.stderr)
            return 1
        print(f"ScanReady demo check passed ({len(messages)} demos).")
        print("\n".join(messages))
        return 0
    if args.command == "launch":
        try:
            return launch_app(no_browser=args.no_browser, smoke=args.smoke, port=args.port)
        except LaunchError as exc:
            print(f"ScanReady could not launch: {exc}", file=sys.stderr)
            return 2
    if args.command in {"prepare-data", "generate-data"}:
        try:
            if args.command == "prepare-data":
                rows = prepare_data(args.root, seed=args.seed)
                noun = "image" if len(rows) == 1 else "images"
                print(f"Prepared {len(rows)} normalized dataset {noun}.")
            else:
                rows = generate_data(args.root, seed=args.seed)
                count = len(rows)
                noun = "variant" if count == 1 else "variants"
                print(f"Generated {count} transform-only {noun} (unscored).")
        except (DatasetError, OSError) as exc:
            print(f"ScanReady dataset command failed: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.command == "evaluate":
        try:
            ensure_generated_data(args.root, seed=args.seed)
            run = evaluate_split(args.root, split=args.split, seed=args.seed)
        except (DatasetError, DatasetAccessError, OSError, ValueError) as exc:
            print(f"ScanReady evaluation failed: {exc}", file=sys.stderr)
            return 2
        status = "PASS" if run.checks["passed"] else "FAIL"
        print(f"ScanReady {args.split} evaluation: {status} ({len(run.records)} images).")
        for failure in run.checks["failures"]:
            print(f"- {failure}")
        return 1 if args.check and not run.checks["passed"] else 0
    if args.command == "calibrate":
        try:
            run = calibrate(args.root, seed=args.seed)
        except (DatasetError, DatasetAccessError, OSError, ValueError) as exc:
            print(f"ScanReady calibration failed: {exc}", file=sys.stderr)
            return 2
        noun = "image" if len(run.records) == 1 else "images"
        print(f"Calibration review complete for {len(run.records)} {noun}: {run.report_path}")
        return 0
    try:
        print(_report_json(analyze_path(args.document)))
    except (InputError, OSError) as exc:
        print(f"ScanReady could not analyze the document: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
