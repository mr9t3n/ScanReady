"""Check bundled demonstration behavior without publishing evaluation evidence."""
from __future__ import annotations

import csv
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from .config import load_config
from .image_io import load_document
from .pipeline import analyze, correct_and_recheck, save_corrected
from .report import Outcome, Status


def check_demos(root: Path) -> list[str]:
    root = root.resolve()
    folder = root / "demo_images"
    config = load_config(root / "config/thresholds.yaml")
    with (folder / "expected.csv").open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"file", "expected_outcome"}.issubset(reader.fieldnames or []):
            raise ValueError("Demo manifest requires file and expected_outcome columns.")
        rows = list(reader)
    if len(rows) != 3 or {row["expected_outcome"] for row in rows} != {item.value for item in Outcome}:
        raise ValueError("Demo manifest must contain three demonstrations, one for each outcome.")
    messages = []
    for row in rows:
        name = row["file"]
        source = (folder / name).resolve()
        if source.parent != folder.resolve() or Path(name).name != name or not source.is_file():
            raise ValueError(f"Unsafe or missing demo image: {name}")
        original_hash = sha256(source.read_bytes()).hexdigest()
        loaded = load_document(source, config=config)
        report = analyze(loaded, config=config)
        if report.outcome.value != row["expected_outcome"]:
            raise ValueError(f"{name}: expected {row['expected_outcome']}, got {report.outcome.value}.")
        selected = {check.correction for check in report.checks
                    if check.status is Status.FAIL and check.recoverable and check.correction}
        detail = report.outcome.value
        if report.outcome is Outcome.READY_AFTER_CORRECTION:
            if not selected:
                raise ValueError(f"{name}: no correction offered.")
            corrected, corrected_report = correct_and_recheck(loaded, report, selected, config=config)
            if corrected_report.outcome is not Outcome.READY:
                raise ValueError(f"{name}: corrected result is {corrected_report.outcome.value}, expected READY.")
            with TemporaryDirectory(prefix="scanready-demo-") as temporary:
                output = save_corrected(corrected, source, temporary)
                if output.resolve() == source or not output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError(f"{name}: correction was not saved as a new PNG.")
                if analyze(load_document(output, config=config), config=config).outcome is not Outcome.READY:
                    raise ValueError(f"{name}: saved correction is not READY.")
            detail += " -> corrected PNG READY"
        elif report.outcome is Outcome.READY and selected:
            raise ValueError(f"{name}: clean demo unexpectedly offers corrections.")
        if sha256(source.read_bytes()).hexdigest() != original_hash:
            raise ValueError(f"{name}: original input changed.")
        messages.append(f"{name}: {detail}; original SHA-256 unchanged")
    return messages
