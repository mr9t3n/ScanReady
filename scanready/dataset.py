from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError


class DatasetError(RuntimeError):
    """Raised when dataset preparation would violate the evaluation contract."""


INPUT_FIELDS = (
    "raw_relative_path",
    "source_slug",
    "source_title",
    "source_url",
    "access_date",
    "doi",
    "license",
    "license_file",
    "redistribution_allowed",
    "source_page_id",
    "split",
    "defects",
    "severity",
    "expected_outcome",
    "correction_allowed",
    "true_skew_deg",
)

MANIFEST_FIELDS = (
    "image_id",
    "file",
    "source_page_id",
    "split",
    "origin",
    "source_slug",
    "defects",
    "severity",
    "expected_outcome",
    "correction_allowed",
    "true_skew_deg",
    "generation_parameters",
    "raw_sha256",
    "release_allowed",
    "orientation_clockwise_degrees",
)

TRANSFORM_FIELDS = (
    "image_id",
    "file",
    "source_page_id",
    "split",
    "origin",
    "source_slug",
    "nominal_transform",
    "annotation_status",
    "generation_parameters",
    "raw_sha256",
    "base_image_id",
    "base_image_sha256",
    "release_allowed",
)


GENERATED_DEFECTS = (
    "blur",
    "rotation",
    "brightness",
    "crop",
    "scale",
    "contrast_loss",
    "illumination_gradient",
    "glare",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_bool(value: str) -> bool | None:
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0"}:
        return False
    raise DatasetError(f"Expected a boolean value, got {value!r}.")


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _read_input(path: Path, *, origin: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise DatasetError(f"Dataset label file is missing: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [field for field in INPUT_FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            raise DatasetError(f"{path.name} is missing columns: {', '.join(missing)}")
        rows = []
        for row_number, row in enumerate(reader, start=2):
            cleaned = {field: (row.get(field) or "").strip() for field in INPUT_FIELDS}
            rotation = (row.get("orientation_clockwise_degrees") or "0").strip()
            if rotation not in {"0", "90", "180", "270"}:
                raise DatasetError(f"orientation_clockwise_degrees must be 0, 90, 180, or 270 in row {row_number}.")
            cleaned["orientation_clockwise_degrees"] = rotation
            cleaned["origin"] = origin
            cleaned["row_number"] = str(row_number)
            rows.append(cleaned)
        return rows


def assign_split(source_page_id: str, *, seed: int = 415, heldout_fraction: float = 0.25) -> str:
    if not source_page_id:
        raise DatasetError("source_page_id is required for split isolation.")
    if not 0.0 < heldout_fraction < 1.0:
        raise DatasetError("heldout_fraction must be between 0 and 1.")
    digest = hashlib.sha256(f"{seed}:{source_page_id}".encode("utf-8")).digest()
    fraction = int.from_bytes(digest[:8], "big") / float(2**64)
    return "heldout" if fraction < heldout_fraction else "calibration"


def _validate_and_assign_splits(rows: list[dict[str, str]], *, seed: int) -> None:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        for required in ("raw_relative_path", "source_slug", "source_page_id", "expected_outcome"):
            if not row[required]:
                raise DatasetError(f"{required} is required in row {row['row_number']}.")
        if row["origin"] == "scholarly":
            for required in ("source_title", "source_url", "access_date"):
                if not row[required]:
                    raise DatasetError(f"{required} is required in scholarly row {row['row_number']}.")
        if row["split"] and row["split"] not in {"calibration", "heldout"}:
            raise DatasetError(f"Invalid split {row['split']!r} in row {row['row_number']}.")
        grouped[row["source_page_id"]].append(row)

    for source_page_id, group in grouped.items():
        explicit = {row["split"] for row in group if row["split"]}
        if len(explicit) > 1:
            raise DatasetError(f"source_page_id {source_page_id!r} appears in both calibration and heldout splits.")
        split = next(iter(explicit), assign_split(source_page_id, seed=seed))
        for row in group:
            row["split"] = split


def _normalized_destination(root: Path, row: dict[str, str], raw_hash: str) -> tuple[Path, bool]:
    redistribution = _parse_bool(row["redistribution_allowed"])
    release_allowed = bool(redistribution and row["license"])
    if release_allowed and row["origin"] == "scholarly":
        license_path_text = row["license_file"]
        if not license_path_text:
            raise DatasetError(f"license_file is required when scholarly row {row['row_number']} permits redistribution.")
        license_path = (root / license_path_text).resolve()
        raw_root = (root / "data" / "raw").resolve()
        if not _inside(license_path, raw_root) or not license_path.is_file():
            raise DatasetError(f"license_file is missing or outside data/raw in row {row['row_number']}.")

    if release_allowed:
        folder = root / "data" / ("sources" if row["origin"] == "scholarly" else "captured")
    else:
        folder = root / "data" / "generated" / "local-only"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{raw_hash[:16]}.png", release_allowed


def _normalize(raw_path: Path, destination: Path, *, clockwise_degrees: int = 0) -> None:
    try:
        with Image.open(raw_path) as opened:
            opened.load()
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise DatasetError(f"Could not decode dataset image {raw_path}: {exc}") from exc
    if clockwise_degrees:
        image = image.transpose({90: Image.Transpose.ROTATE_270,
                                 180: Image.Transpose.ROTATE_180,
                                 270: Image.Transpose.ROTATE_90}[clockwise_degrees])
    # Copy pixels only; camera PNGs can retain EXIF/text in their info dictionary.
    image = Image.fromarray(np.asarray(image).copy())
    longest = max(image.size)
    if longest > 2000:
        scale = 2000.0 / longest
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(size, Image.Resampling.LANCZOS)
    image.save(destination, format="PNG")
    with Image.open(destination) as check:
        if check.getexif():
            raise DatasetError(f"Normalized image retained metadata: {destination}")


def _write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in MANIFEST_FIELDS} for row in rows)


def _write_dataset_manifests(root: Path, rows: list[dict[str, str]]) -> None:
    _write_manifest(root / "data/manifest.csv", rows)
    for split in ("calibration", "heldout"):
        _write_manifest(root / f"data/{split}.csv", [row for row in rows if row["split"] == split])


def _read_manifest(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise DatasetError(f"{path.name} is missing; run prepare-data first.")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [field for field in MANIFEST_FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            raise DatasetError(f"{path.name} is missing columns: {', '.join(missing)}")
        return [{field: (row.get(field) or "") for field in MANIFEST_FIELDS} for row in reader]


def prepare_data(root: Path, *, seed: int = 415) -> list[dict[str, str]]:
    root = root.resolve()
    raw_root = (root / "data" / "raw").resolve()
    rows = _read_input(root / "data" / "sources.csv", origin="scholarly")
    rows.extend(_read_input(root / "data" / "captured_labels.csv", origin="captured"))
    if not rows:
        raise DatasetError("No dataset rows were declared in sources.csv or captured_labels.csv.")
    _validate_and_assign_splits(rows, seed=seed)

    resolved: list[tuple[dict[str, str], Path]] = []
    raw_hashes: dict[Path, str] = {}
    content_splits: dict[str, str] = {}
    content_rotations: dict[str, str] = {}
    for row in rows:
        raw_path = (root / row["raw_relative_path"]).resolve()
        if not _inside(raw_path, raw_root):
            raise DatasetError(f"raw_relative_path escapes data/raw in row {row['row_number']}.")
        if raw_path.suffix.lower() not in {".jpg", ".jpeg", ".png"} or not raw_path.is_file():
            raise DatasetError(f"Raw JPEG/PNG file is missing or unsupported in row {row['row_number']}: {raw_path}")
        raw_hashes.setdefault(raw_path, _sha256(raw_path))
        raw_hash = raw_hashes[raw_path]
        rotation = row["orientation_clockwise_degrees"]
        if content_rotations.setdefault(raw_hash, rotation) != rotation:
            raise DatasetError("Identical raw images declare conflicting normalization orientation.")
        previous_split = content_splits.setdefault(raw_hash, row["split"])
        if previous_split != row["split"]:
            raise DatasetError("An identical raw image appears in both calibration and heldout splits.")
        resolved.append((row, raw_path))

    for folder in (root / "data" / "sources", root / "data" / "captured", root / "data" / "generated" / "local-only"):
        if folder.is_dir():
            for stale in folder.glob("*.png"):
                stale.unlink()

    manifest_rows: list[dict[str, str]] = []
    for row, raw_path in resolved:
        raw_hash = raw_hashes[raw_path]
        destination, release_allowed = _normalized_destination(root, row, raw_hash)
        _normalize(raw_path, destination, clockwise_degrees=int(row["orientation_clockwise_degrees"]))
        image_key = f"{row['source_page_id']}:{raw_hash}".encode("utf-8")
        manifest_rows.append(
            {
                "image_id": hashlib.sha256(image_key).hexdigest()[:20],
                "file": destination.relative_to(root).as_posix(),
                "source_page_id": row["source_page_id"],
                "split": row["split"],
                "origin": row["origin"],
                "source_slug": row["source_slug"],
                "defects": row["defects"] or "unknown",
                "severity": row["severity"] or "unknown",
                "expected_outcome": row["expected_outcome"],
                "correction_allowed": str(bool(_parse_bool(row["correction_allowed"]))).lower(),
                "true_skew_deg": row["true_skew_deg"] or "0",
                "generation_parameters": "",
                "raw_sha256": raw_hash,
                "release_allowed": str(release_allowed).lower(),
                "orientation_clockwise_degrees": row["orientation_clockwise_degrees"],
            }
        )

    for raw_path, expected_hash in raw_hashes.items():
        if _sha256(raw_path) != expected_hash:
            raise DatasetError(f"Raw input changed during preparation: {raw_path}")
    manifest_rows.sort(key=lambda row: (row["source_page_id"], row["image_id"]))
    _write_dataset_manifests(root, manifest_rows)
    return manifest_rows


def _variant(image: np.ndarray, defect: str, *, seed: int) -> tuple[np.ndarray, dict[str, object]]:
    height, width = image.shape[:2]
    rng = np.random.default_rng(seed)
    if defect == "blur":
        return cv2.GaussianBlur(image, (15, 15), 0), {"kernel": 15, "sigma_x": 0.0}
    if defect == "rotation":
        angle = float(rng.choice(np.array([-10.0, -7.0, 7.0, 10.0])))
        matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), angle, 1.0)
        # Expand the canvas so rotation does not introduce an accidental crop.
        cosine, sine = abs(matrix[0, 0]), abs(matrix[0, 1])
        expanded_width = int(np.ceil(width * cosine + height * sine))
        expanded_height = int(np.ceil(height * cosine + width * sine))
        matrix[0, 2] += (expanded_width - width) / 2.0
        matrix[1, 2] += (expanded_height - height) / 2.0
        output = cv2.warpAffine(
            image, matrix, (expanded_width, expanded_height), borderValue=(255, 255, 255)
        )
        return output, {
            "angle_deg": angle,
            "scale": 1.0,
            "expanded_canvas": True,
            "border_value": [255, 255, 255],
        }
    if defect == "brightness":
        factor = 0.55
        return np.clip(image.astype(np.float32) * factor, 0, 255).astype(np.uint8), {"factor": factor}
    if defect == "crop":
        margin_x = max(1, round(width * 0.06))
        margin_y = max(1, round(height * 0.06))
        cropped = image[margin_y : height - margin_y, margin_x : width - margin_x]
        return cv2.resize(cropped, (width, height), interpolation=cv2.INTER_LINEAR), {
            "margin_fraction": 0.06,
            "resize_interpolation": "INTER_LINEAR",
        }
    if defect == "scale":
        scale = 0.45
        small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        return cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR), {
            "scale": scale,
            "down_interpolation": "INTER_AREA",
            "up_interpolation": "INTER_LINEAR",
        }
    if defect == "contrast_loss":
        factor = 0.35
        output = np.clip(image.astype(np.float32) * factor + 255.0 * (1.0 - factor), 0, 255)
        return output.astype(np.uint8), {"factor": factor, "white_reference": 255.0}
    if defect == "illumination_gradient":
        gradient = np.linspace(0.45, 1.0, width, dtype=np.float32)[None, :, None]
        return np.clip(image.astype(np.float32) * gradient, 0, 255).astype(np.uint8), {
            "minimum_factor": 0.45,
            "maximum_factor": 1.0,
            "axis": "horizontal",
        }
    if defect == "glare":
        mask = np.zeros((height, width), dtype=np.uint8)
        center = (round(width * 0.72), round(height * 0.28))
        axes = (max(4, round(width * 0.16)), max(4, round(height * 0.10)))
        cv2.ellipse(mask, center, axes, -12, 0, 360, 255, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=max(width, height) * 0.025)
        alpha = (mask.astype(np.float32) / 255.0 * 0.95)[:, :, None]
        output = image.astype(np.float32) * (1.0 - alpha) + 255.0 * alpha
        return np.clip(output, 0, 255).astype(np.uint8), {
            "center": list(center),
            "axes": list(axes),
            "ellipse_angle_deg": -12.0,
            "mask_blur_sigma": max(width, height) * 0.025,
            "maximum_alpha": 0.95,
        }
    raise DatasetError(f"Unsupported generated defect: {defect}")



def _write_transform_sidecar(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRANSFORM_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in TRANSFORM_FIELDS} for row in rows)


def generate_data(root: Path, *, seed: int = 415) -> list[dict[str, str]]:
    root = root.resolve()
    calibration_path = root / "data/calibration.csv"
    base_rows = _read_manifest(calibration_path)

    if any(row["split"] != "calibration" for row in base_rows):
        raise DatasetError(
            "calibration.csv contains an invalid split; run prepare-data before generate-data."
        )
    if any(row["origin"] == "generated" for row in base_rows):
        raise DatasetError(
            "Legacy generated rows are present in calibration.csv; run prepare-data before generate-data."
        )

    eligible_bases = [
        row
        for row in base_rows
        if row["origin"] != "generated"
        and row["expected_outcome"] == "READY"
        and row["defects"].strip().lower() in {"clean", "none"}
    ]
    resolved_bases: list[tuple[dict[str, str], Path]] = []
    for base in sorted(eligible_bases, key=lambda row: row["image_id"]):
        source_path = (root / base["file"]).resolve()
        if not _inside(source_path, root) or not source_path.is_file():
            raise DatasetError(f"Calibration image is missing or unsafe: {base['file']}")
        resolved_bases.append((base, source_path))

    output_dir = root / "data/generated/calibration"
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale in output_dir.glob("*.png"):
        stale.unlink()

    generated_rows: list[dict[str, str]] = []
    for base, source_path in resolved_bases:
        image = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
        if image is None:
            raise DatasetError(f"Could not decode normalized image: {base['file']}")
        base_image_sha256 = _sha256(source_path)
        for index, nominal_transform in enumerate(GENERATED_DEFECTS):
            derived_seed = int.from_bytes(
                hashlib.sha256(
                    f"{seed}:{base['image_id']}:{nominal_transform}".encode("utf-8")
                ).digest()[:8],
                "big",
            )
            output, transform_parameters = _variant(
                image, nominal_transform, seed=derived_seed
            )
            destination = output_dir / f"{base['image_id']}_{index:02d}_{nominal_transform}.png"
            if not cv2.imwrite(str(destination), output):
                raise DatasetError(f"Could not write generated image: {destination}")
            generation_parameters = {
                "seed": seed,
                "derived_seed": derived_seed,
                **transform_parameters,
            }
            generated_rows.append(
                {
                    "image_id": hashlib.sha256(destination.read_bytes()).hexdigest()[:20],
                    "file": destination.relative_to(root).as_posix(),
                    "source_page_id": base["source_page_id"],
                    "split": "calibration",
                    "origin": "generated",
                    "source_slug": base["source_slug"],
                    "nominal_transform": nominal_transform,
                    "annotation_status": "transform_only",
                    "generation_parameters": json.dumps(
                        generation_parameters, sort_keys=True, separators=(",", ":")
                    ),
                    "raw_sha256": base["raw_sha256"],
                    "base_image_id": base["image_id"],
                    "base_image_sha256": base_image_sha256,
                    "release_allowed": "false",
                }
            )

    _write_transform_sidecar(root / "data/generated/transforms.csv", generated_rows)
    _write_generation_state(root, seed=seed, rows=generated_rows)
    return generated_rows


def _generation_input_identity(root: Path, *, seed: int) -> dict[str, object]:
    calibration_path = root / "data/calibration.csv"
    rows = _read_manifest(calibration_path)
    if any(row["split"] != "calibration" for row in rows):
        raise DatasetError(
            "calibration.csv contains an invalid split; run prepare-data before generate-data."
        )
    if any(row["origin"] == "generated" for row in rows):
        raise DatasetError(
            "Legacy generated rows are present in calibration.csv; run prepare-data before generate-data."
        )
    eligible = sorted(
        (
            row
            for row in rows
            if row["origin"] != "generated"
            and row["expected_outcome"] == "READY"
            and row["defects"].strip().lower() in {"clean", "none"}
        ),
        key=lambda row: row["image_id"],
    )
    base_images = []
    for row in eligible:
        path = (root / row["file"]).resolve()
        if not _inside(path, root) or not path.is_file():
            raise DatasetError(f"Calibration image is missing or unsafe: {row['file']}")
        base_images.append(
            {"image_id": row["image_id"], "file": row["file"], "sha256": _sha256(path)}
        )
    return {
        "schema_version": 1,
        "seed": seed,
        "calibration_csv_sha256": _sha256(calibration_path),
        "generator_source_sha256": _sha256(Path(__file__).resolve()),
        "base_images": base_images,
    }


def _write_generation_state(
    root: Path, *, seed: int, rows: list[dict[str, str]]
) -> None:
    state = _generation_input_identity(root, seed=seed)
    sidecar = root / "data/generated/transforms.csv"
    state["sidecar_sha256"] = _sha256(sidecar)
    state["outputs"] = [
        {"file": row["file"], "sha256": _sha256(root / row["file"])}
        for row in rows
    ]
    state_path = root / "data/generated/transforms-state.json"
    temporary = state_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(state_path)


def ensure_generated_data(root: Path, *, seed: int = 415) -> None:
    """Regenerate unscored transforms only when their complete identity is stale."""
    root = root.resolve()
    expected = _generation_input_identity(root, seed=seed)
    state_path = root / "data/generated/transforms-state.json"
    sidecar = root / "data/generated/transforms.csv"
    output_dir = root / "data/generated/calibration"
    try:
        stored = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        stored = None
    fresh = isinstance(stored, dict) and all(
        stored.get(key) == value for key, value in expected.items()
    )
    outputs = stored.get("outputs", []) if isinstance(stored, dict) else []
    if fresh:
        fresh = (
            isinstance(stored.get("sidecar_sha256"), str)
            and sidecar.is_file()
            and _sha256(sidecar) == stored["sidecar_sha256"]
            and isinstance(outputs, list)
        )
    expected_files: set[str] = set()
    if fresh:
        for output in outputs:
            if not isinstance(output, dict):
                fresh = False
                break
            relative = output.get("file")
            digest = output.get("sha256")
            if not isinstance(relative, str) or not isinstance(digest, str):
                fresh = False
                break
            path = (root / relative).resolve()
            if not _inside(path, root) or not path.is_file() or _sha256(path) != digest:
                fresh = False
                break
            expected_files.add(relative)
    if fresh:
        actual_files = (
            {path.relative_to(root).as_posix() for path in output_dir.glob("*.png")}
            if output_dir.is_dir()
            else set()
        )
        fresh = actual_files == expected_files
    if not fresh:
        generate_data(root, seed=seed)
