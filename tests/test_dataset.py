from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from scanready import cli
import scanready.dataset as dataset_module
from scanready.dataset import DatasetError, generate_data, prepare_data
from scanready.evaluate import EvaluationRun


INPUT_FIELDS = [
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
]

TRANSFORM_FIELDS = [
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
]

@pytest.fixture
def dataset_root(request: pytest.FixtureRequest) -> Path:
    parent = (Path(__file__).resolve().parent / ".dataset-tmp").resolve()
    root = (parent / request.node.name).resolve()
    if parent not in root.parents:
        raise RuntimeError("Dataset test path escaped its controlled directory.")
    if root.exists():
        shutil.rmtree(root)
    (root / "data" / "raw" / "sources").mkdir(parents=True)
    (root / "data" / "raw" / "captured").mkdir(parents=True)
    yield root
    if root.exists():
        shutil.rmtree(root)


def _write_labels(root: Path, source_rows: list[dict[str, str]], captured_rows: list[dict[str, str]] | None = None) -> None:
    for row in source_rows:
        if row.get("license_file"):
            license_path = root / row["license_file"]
            license_path.parent.mkdir(parents=True, exist_ok=True)
            if not license_path.exists():
                license_path.write_text("Fixture license terms\n", encoding="utf-8")
    for name, rows in (("sources.csv", source_rows), ("captured_labels.csv", captured_rows or [])):
        with (root / "data" / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=INPUT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)


def _source_row(relative_path: str, **changes: str) -> dict[str, str]:
    row = {
        "raw_relative_path": relative_path,
        "source_slug": "fixture-source",
        "source_title": "Fixture Document Image Dataset",
        "source_url": "https://example.edu/dataset",
        "access_date": "2026-09-26",
        "doi": "10.0000/fixture",
        "license": "CC BY 4.0",
        "license_file": "data/raw/sources/LICENSE.txt",
        "redistribution_allowed": "true",
        "source_page_id": "fixture:page-001",
        "split": "calibration",
        "defects": "clean",
        "severity": "none",
        "expected_outcome": "READY",
        "correction_allowed": "false",
        "true_skew_deg": "0",
    }
    row.update(changes)
    return row


def _save_jpeg(path: Path, *, size: tuple[int, int] = (2500, 1000), orientation: int | None = None) -> None:
    image = Image.new("RGB", size, "white")
    exif = Image.Exif()
    exif[305] = "private fixture metadata"
    if orientation is not None:
        exif[274] = orientation
    image.save(path, format="JPEG", quality=95, exif=exif)


def test_prepare_data_keeps_raw_bytes_and_writes_metadata_free_2000px_image(dataset_root: Path) -> None:
    raw = dataset_root / "data" / "raw" / "sources" / "page.jpg"
    _save_jpeg(raw, orientation=6)
    (dataset_root / "data" / "raw" / "sources" / "LICENSE.txt").write_text("CC BY 4.0\n", encoding="utf-8")
    _write_labels(dataset_root, [_source_row("data/raw/sources/page.jpg")])
    before = raw.read_bytes()

    rows = prepare_data(dataset_root)

    assert raw.read_bytes() == before
    assert len(rows) == 1
    normalized = dataset_root / rows[0]["file"]
    assert normalized.parent == dataset_root / "data" / "sources"
    with Image.open(normalized) as image:
        assert max(image.size) == 2000
        assert not image.getexif()
    assert rows[0]["raw_sha256"] == hashlib.sha256(before).hexdigest()
    assert rows[0]["release_allowed"] == "true"


def test_prepare_data_quarantines_unknown_redistribution_terms(dataset_root: Path) -> None:
    raw = dataset_root / "data" / "raw" / "sources" / "unknown.png"
    Image.new("RGB", (640, 480), "white").save(raw)
    _write_labels(
        dataset_root,
        [
            _source_row(
                "data/raw/sources/unknown.png",
                license="",
                license_file="",
                redistribution_allowed="",
            )
        ],
    )

    rows = prepare_data(dataset_root)

    assert rows[0]["release_allowed"] == "false"
    assert rows[0]["file"].startswith("data/generated/local-only/")
    assert not list((dataset_root / "data" / "sources").glob("*.png"))


def test_prepare_data_requires_scholarly_provenance(dataset_root: Path) -> None:
    raw = dataset_root / "data" / "raw" / "sources" / "page.png"
    Image.new("RGB", (640, 480), "white").save(raw)
    _write_labels(dataset_root, [_source_row("data/raw/sources/page.png", source_title="")])

    with pytest.raises(DatasetError, match="source_title"):
        prepare_data(dataset_root)


def test_prepare_data_refuses_source_page_split_leakage(dataset_root: Path) -> None:
    first = dataset_root / "data" / "raw" / "sources" / "first.png"
    second = dataset_root / "data" / "raw" / "sources" / "second.png"
    Image.new("RGB", (640, 480), "white").save(first)
    Image.new("RGB", (640, 480), "white").save(second)
    rows = [
        _source_row("data/raw/sources/first.png", split="calibration"),
        _source_row("data/raw/sources/second.png", split="heldout"),
    ]
    _write_labels(dataset_root, rows)

    with pytest.raises(DatasetError, match="source_page_id.*both calibration and heldout"):
        prepare_data(dataset_root)


def test_generate_data_is_deterministic_and_uses_calibration_pages_only(dataset_root: Path) -> None:
    calibration = dataset_root / "data" / "raw" / "sources" / "calibration.png"
    heldout = dataset_root / "data" / "raw" / "sources" / "heldout.png"
    Image.new("RGB", (800, 1000), "white").save(calibration)
    Image.new("RGB", (800, 1000), "ivory").save(heldout)
    _write_labels(
        dataset_root,
        [
            _source_row("data/raw/sources/calibration.png", source_page_id="page-cal", split="calibration"),
            _source_row("data/raw/sources/heldout.png", source_page_id="page-hold", split="heldout"),
        ],
    )
    prepare_data(dataset_root)

    first_rows = generate_data(dataset_root, seed=415)
    first_sidecar = (dataset_root / "data/generated/transforms.csv").read_bytes()
    first_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((dataset_root / "data" / "generated" / "calibration").glob("*.png"))
    }
    second_rows = generate_data(dataset_root, seed=415)
    second_sidecar = (dataset_root / "data/generated/transforms.csv").read_bytes()
    second_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((dataset_root / "data" / "generated" / "calibration").glob("*.png"))
    }

    generated = first_rows
    assert len(generated) == 8
    assert {row["source_page_id"] for row in generated} == {"page-cal"}
    assert {row["split"] for row in generated} == {"calibration"}
    assert {row["nominal_transform"] for row in generated} == {
        "blur",
        "rotation",
        "brightness",
        "crop",
        "scale",
        "contrast_loss",
        "illumination_gradient",
        "glare",
    }
    assert first_rows == second_rows
    assert first_sidecar == second_sidecar
    assert first_hashes == second_hashes

def test_transform_only_generation_preserves_scored_manifests(dataset_root: Path) -> None:
    calibration = dataset_root / "data/raw/sources/calibration.png"
    heldout = dataset_root / "data/raw/sources/heldout.png"
    Image.new("RGB", (800, 1000), "white").save(calibration)
    Image.new("RGB", (800, 1000), "ivory").save(heldout)
    _write_labels(
        dataset_root,
        [
            _source_row("data/raw/sources/calibration.png", source_page_id="page-cal", split="calibration"),
            _source_row("data/raw/sources/heldout.png", source_page_id="page-hold", split="heldout"),
        ],
    )
    prepare_data(dataset_root)
    scored_paths = [dataset_root / f"data/{name}.csv" for name in ("manifest", "calibration", "heldout")]
    scored_before = {path.name: path.read_bytes() for path in scored_paths}

    rows = generate_data(dataset_root, seed=415)

    generated_rows = [row for row in rows if row.get("origin") == "generated"]
    unsupported_labels = {"defects", "severity", "expected_outcome", "correction_allowed", "true_skew_deg"}
    assert all(unsupported_labels.isdisjoint(row) for row in generated_rows)
    assert {path.name: path.read_bytes() for path in scored_paths} == scored_before
    assert len(rows) == 8
    assert all(list(row) == TRANSFORM_FIELDS for row in rows)
    assert {row["source_page_id"] for row in rows} == {"page-cal"}
    assert {row["split"] for row in rows} == {"calibration"}
    assert {row["origin"] for row in rows} == {"generated"}
    assert {row["annotation_status"] for row in rows} == {"transform_only"}
    assert {row["release_allowed"] for row in rows} == {"false"}
    with (dataset_root / "data/generated/transforms.csv").open(newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == TRANSFORM_FIELDS
        assert list(reader) == rows

def test_cli_prepares_then_generates_dataset(dataset_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raw = dataset_root / "data" / "raw" / "sources" / "page.png"
    Image.new("RGB", (640, 480), "white").save(raw)
    _write_labels(dataset_root, [_source_row("data/raw/sources/page.png")])

    assert cli.main(["prepare-data", "--root", str(dataset_root)]) == 0
    assert "Prepared 1 normalized dataset image" in capsys.readouterr().out
    assert cli.main(["generate-data", "--root", str(dataset_root), "--seed", "415"]) == 0
    assert "Generated 8 transform-only variants (unscored)" in capsys.readouterr().out



def test_prepare_data_rejects_identical_content_across_splits(dataset_root: Path) -> None:
    raw = dataset_root / "data/raw/sources/page.png"
    Image.new("RGB", (640, 480), "white").save(raw)
    duplicate = raw.with_name("duplicate.png")
    duplicate.write_bytes(raw.read_bytes())
    _write_labels(dataset_root, [
        _source_row("data/raw/sources/page.png", source_page_id="page-a", split="calibration"),
        _source_row("data/raw/sources/duplicate.png", source_page_id="page-b", split="heldout"),
    ])
    with pytest.raises(DatasetError, match="identical.*both calibration and heldout"):
        prepare_data(dataset_root)
    assert not (dataset_root / "data/manifest.csv").exists()


def test_generate_data_does_not_relabel_a_defective_base_as_correctable(dataset_root: Path) -> None:
    raw = dataset_root / "data/raw/sources/blurry.png"
    Image.new("RGB", (640, 480), "white").save(raw)
    _write_labels(dataset_root, [_source_row(
        "data/raw/sources/blurry.png", defects="blur", severity="severe",
        expected_outcome="RETRY CAPTURE", correction_allowed="false",
    )])
    prepare_data(dataset_root)
    calibration_before = (dataset_root / "data/calibration.csv").read_bytes()
    rows = generate_data(dataset_root)
    assert rows == []
    assert (dataset_root / "data/calibration.csv").read_bytes() == calibration_before
    assert not list((dataset_root / "data/generated/calibration").glob("*.png"))


def test_preparation_and_generation_write_separate_split_manifests(dataset_root: Path) -> None:
    for name, color in (("cal", "white"), ("hold", "ivory")):
        Image.new("RGB", (80, 100), color).save(dataset_root / f"data/raw/sources/{name}.png")
    _write_labels(dataset_root, [
        _source_row("data/raw/sources/cal.png", source_page_id="cal", split="calibration"),
        _source_row("data/raw/sources/hold.png", source_page_id="hold", split="heldout"),
    ])
    prepare_data(dataset_root)
    cal = dataset_root / "data/calibration.csv"
    hold = dataset_root / "data/heldout.csv"
    assert cal.is_file() and hold.is_file()
    with cal.open() as handle:
        assert [row["source_page_id"] for row in csv.DictReader(handle)] == ["cal"]
    original_cal = cal.read_bytes()
    original_hold = hold.read_bytes()
    generated = generate_data(dataset_root)
    with cal.open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert {row["source_page_id"] for row in rows} == {"cal"}
    assert cal.read_bytes() == original_cal
    assert hold.read_bytes() == original_hold
    assert len(generated) == 8


def test_generation_reads_only_calibration_and_never_heldout(
    dataset_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, color in (("cal", "white"), ("hold", "ivory")):
        Image.new("RGB", (160, 200), color).save(dataset_root / f"data/raw/sources/{name}.png")
    _write_labels(
        dataset_root,
        [
            _source_row("data/raw/sources/cal.png", source_page_id="cal", split="calibration"),
            _source_row("data/raw/sources/hold.png", source_page_id="hold", split="heldout"),
        ],
    )
    prepare_data(dataset_root)
    with (dataset_root / "data/heldout.csv").open(newline="") as handle:
        heldout_file = dataset_root / next(csv.DictReader(handle))["file"]
    forbidden = {
        (dataset_root / "data/manifest.csv").resolve(),
        (dataset_root / "data/heldout.csv").resolve(),
    }
    original_open = Path.open
    original_imread = dataset_module.cv2.imread

    def guarded_open(candidate: Path, *args: object, **kwargs: object):
        if candidate.resolve() in forbidden:
            pytest.fail(f"Generation opened forbidden scored file: {candidate}")
        return original_open(candidate, *args, **kwargs)

    def guarded_imread(candidate: str, *args: object, **kwargs: object):
        if Path(candidate).resolve() == heldout_file.resolve():
            pytest.fail(f"Generation opened heldout image: {candidate}")
        return original_imread(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(dataset_module.cv2, "imread", guarded_imread)

    rows = generate_data(dataset_root)

    assert len(rows) == 8
    assert {row["source_page_id"] for row in rows} == {"cal"}


def test_generation_rejects_invalid_calibration_split_before_image_reads(
    dataset_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = dataset_root / "data/raw/sources/page.png"
    Image.new("RGB", (160, 200), "white").save(raw)
    _write_labels(dataset_root, [_source_row("data/raw/sources/page.png")])
    prepare_data(dataset_root)
    calibration = dataset_root / "data/calibration.csv"
    with calibration.open(newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    rows[0]["split"] = "heldout"
    with calibration.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    output_dir = dataset_root / "data/generated/calibration"
    output_dir.mkdir(parents=True)
    existing_png = output_dir / "existing.png"
    existing_png.write_bytes(b"existing generated png")
    sidecar = dataset_root / "data/generated/transforms.csv"
    sidecar.write_bytes(b"existing sidecar")
    monkeypatch.setattr(
        dataset_module.cv2,
        "imread",
        lambda *args, **kwargs: pytest.fail("Image read occurred before split validation"),
    )

    with pytest.raises(DatasetError, match="invalid split.*prepare-data"):
        generate_data(dataset_root)

    assert existing_png.read_bytes() == b"existing generated png"
    assert sidecar.read_bytes() == b"existing sidecar"


def test_generation_rejects_legacy_generated_calibration_before_replacement(
    dataset_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = dataset_root / "data/raw/sources/page.png"
    Image.new("RGB", (160, 200), "white").save(raw)
    _write_labels(dataset_root, [_source_row("data/raw/sources/page.png")])
    prepare_data(dataset_root)
    calibration = dataset_root / "data/calibration.csv"
    with calibration.open(newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    rows[0]["origin"] = "generated"
    with calibration.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    output_dir = dataset_root / "data/generated/calibration"
    output_dir.mkdir(parents=True)
    existing_png = output_dir / "existing.png"
    existing_png.write_bytes(b"existing generated png")
    sidecar = dataset_root / "data/generated/transforms.csv"
    sidecar.write_bytes(b"existing sidecar")
    monkeypatch.setattr(
        dataset_module.cv2,
        "imread",
        lambda *args, **kwargs: pytest.fail("Image read occurred before legacy-row validation"),
    )

    with pytest.raises(DatasetError, match="Legacy generated rows.*prepare-data"):
        generate_data(dataset_root)

    assert existing_png.read_bytes() == b"existing generated png"
    assert sidecar.read_bytes() == b"existing sidecar"


def test_generation_preserves_generated_local_only(dataset_root: Path) -> None:
    raw = dataset_root / "data/raw/sources/page.png"
    Image.new("RGB", (160, 200), "white").save(raw)
    _write_labels(dataset_root, [_source_row("data/raw/sources/page.png")])
    prepare_data(dataset_root)
    local_only = dataset_root / "data/generated/local-only/unrelated.png"
    local_only.parent.mkdir(parents=True, exist_ok=True)
    local_only.write_bytes(b"unrelated local-only bytes")

    generate_data(dataset_root)

    assert local_only.read_bytes() == b"unrelated local-only bytes"


def test_transform_sidecar_records_complete_reproducible_parameters(dataset_root: Path) -> None:
    raw = dataset_root / "data/raw/sources/page.png"
    Image.new("RGB", (160, 200), "white").save(raw)
    _write_labels(dataset_root, [_source_row("data/raw/sources/page.png")])
    prepare_data(dataset_root)
    with (dataset_root / "data/calibration.csv").open(newline="") as handle:
        base = next(csv.DictReader(handle))

    rows = generate_data(dataset_root, seed=415)

    by_transform = {row["nominal_transform"]: row for row in rows}
    parameters = {
        name: json.loads(row["generation_parameters"])
        for name, row in by_transform.items()
    }
    base_sha256 = hashlib.sha256((dataset_root / base["file"]).read_bytes()).hexdigest()
    assert {row["base_image_id"] for row in rows} == {base["image_id"]}
    assert {row["base_image_sha256"] for row in rows} == {base_sha256}
    assert {row["raw_sha256"] for row in rows} == {base["raw_sha256"]}
    assert {item["seed"] for item in parameters.values()} == {415}
    assert all(isinstance(item["derived_seed"], int) for item in parameters.values())
    assert parameters["glare"]["maximum_alpha"] == 0.95
    assert parameters["illumination_gradient"]["minimum_factor"] == 0.45
    assert parameters["illumination_gradient"]["maximum_factor"] == 1.0
    assert abs(parameters["rotation"]["angle_deg"]) in {7.0, 10.0}
    assert "true_skew_deg" not in by_transform["rotation"]


def test_blank_scale_and_brightness_remain_transform_only(dataset_root: Path) -> None:
    raw = dataset_root / "data/raw/sources/blank.png"
    Image.new("RGB", (160, 200), "white").save(raw)
    _write_labels(dataset_root, [_source_row("data/raw/sources/blank.png")])
    prepare_data(dataset_root)

    rows = generate_data(dataset_root)

    by_transform = {row["nominal_transform"]: row for row in rows}
    with Image.open(dataset_root / by_transform["scale"]["file"]) as scaled:
        assert scaled.getextrema() == ((255, 255), (255, 255), (255, 255))
    with Image.open(dataset_root / by_transform["brightness"]["file"]) as darkened:
        assert darkened.getextrema() == ((140, 140), (140, 140), (140, 140))
    assert by_transform["scale"]["annotation_status"] == "transform_only"
    assert by_transform["brightness"]["annotation_status"] == "transform_only"


def test_prepare_explicit_quarter_turn_after_exif_preserves_raw_and_strips_metadata(dataset_root: Path) -> None:
    raw = dataset_root / 'data/raw/captured/sideways.png'
    image = Image.new('RGB', (600, 400), 'white')
    image.paste('red', (0, 0, 100, 100))
    exif = Image.Exif()
    exif[274] = 1
    exif[305] = 'private metadata'
    image.save(raw, exif=exif)
    _write_labels(dataset_root, [])
    row = _source_row('data/raw/captured/sideways.png')
    row['orientation_clockwise_degrees'] = '90'
    with (dataset_root / 'data/captured_labels.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=INPUT_FIELDS + ['orientation_clockwise_degrees'])
        writer.writeheader()
        writer.writerow(row)
    before = raw.read_bytes()
    rows = prepare_data(dataset_root)
    with Image.open(dataset_root / rows[0]['file']) as result:
        assert result.size == (400, 600)
        assert result.getpixel((350, 50)) == (255, 0, 0)
        assert not result.getexif()
    assert rows[0]['orientation_clockwise_degrees'] == '90'
    assert raw.read_bytes() == before


def test_prepare_rejects_conflicting_orientation_for_identical_raw(dataset_root: Path) -> None:
    raw = dataset_root / 'data/raw/sources/page.png'
    Image.new('RGB', (640, 480), 'white').save(raw)
    _write_labels(dataset_root, [_source_row('data/raw/sources/page.png')])
    rows = [dict(_source_row('data/raw/sources/page.png'), orientation_clockwise_degrees=value)
            for value in ('0', '90')]
    with (dataset_root / 'data/sources.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=INPUT_FIELDS + ['orientation_clockwise_degrees'])
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(DatasetError, match='conflicting.*orientation'):
        prepare_data(dataset_root)
    assert not (dataset_root / 'data/manifest.csv').exists()


def test_generated_rotation_preserves_edge_near_content() -> None:
    import numpy as np
    from scanready.dataset import _variant
    image = np.full((200, 400, 3), 255, dtype=np.uint8)
    image[4:16, 4:30] = 0
    output, parameters = _variant(image, "rotation", seed=415)
    assert output.shape[0] > image.shape[0]
    assert output.shape[1] > image.shape[1]
    assert np.count_nonzero(output[:, :, 0] < 128) >= 290
    assert abs(parameters["angle_deg"]) in {7, 10}


def test_generated_crop_preserves_content_inside_blank_margins() -> None:
    import numpy as np
    from scanready.dataset import _variant

    image = np.full((200, 400, 3), 255, dtype=np.uint8)
    image[60:140, 80:320] = 0

    output, parameters = _variant(image, "crop", seed=415)

    assert parameters["margin_fraction"] == 0.06
    assert output[100, 200, 0] == 0
    assert np.count_nonzero(output[:, :, 0] < 128) >= np.count_nonzero(image[:, :, 0] < 128)


def test_generated_crop_removes_an_edge_content_marker() -> None:
    import numpy as np
    from scanready.dataset import _variant
    image = np.full((200, 400, 3), 255, dtype=np.uint8)
    image[2:8, 2:16] = 0
    image[90:110, 180:220] = 80
    output, _ = _variant(image, "crop", seed=415)
    assert output.min() >= 80
    assert np.count_nonzero(output[:, :, 0] < 128) > 0


def _prepare_generation_cli_fixture(dataset_root: Path) -> Path:
    calibration = dataset_root / "data/raw/sources/calibration.png"
    heldout = dataset_root / "data/raw/sources/heldout.png"
    Image.new("RGB", (160, 200), "white").save(calibration)
    Image.new("RGB", (160, 200), "ivory").save(heldout)
    _write_labels(
        dataset_root,
        [
            _source_row("data/raw/sources/calibration.png", source_page_id="page-cal", split="calibration"),
            _source_row("data/raw/sources/heldout.png", source_page_id="page-hold", split="heldout"),
        ],
    )
    rows = prepare_data(dataset_root)
    return dataset_root / next(row["file"] for row in rows if row["split"] == "heldout")


def test_cli_evaluate_refreshes_stale_generation_without_touching_heldout(
    dataset_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    heldout_image = _prepare_generation_cli_fixture(dataset_root)
    generate_data(dataset_root, seed=415)
    generated = sorted((dataset_root / "data/generated/calibration").glob("*.png"))
    generated[0].write_bytes(b"stale generated output")
    heldout_before = {
        "csv": (dataset_root / "data/heldout.csv").read_bytes(),
        "image": heldout_image.read_bytes(),
    }

    def fake_evaluate(root: Path, *, split: str, seed: int) -> EvaluationRun:
        assert generated[0].read_bytes() != b"stale generated output"
        return EvaluationRun(records=[], metrics={}, checks={"passed": False, "failures": ["fixture gate"]})

    monkeypatch.setattr(cli, "evaluate_split", fake_evaluate)

    assert cli.main(["evaluate", "--root", str(dataset_root), "--check"]) == 1
    state = json.loads((dataset_root / "data/generated/transforms-state.json").read_text())
    assert state["seed"] == 415
    assert state["calibration_csv_sha256"] == hashlib.sha256(
        (dataset_root / "data/calibration.csv").read_bytes()
    ).hexdigest()
    assert len(state["base_images"]) == 1
    assert state["generator_source_sha256"]
    assert (dataset_root / "data/heldout.csv").read_bytes() == heldout_before["csv"]
    assert heldout_image.read_bytes() == heldout_before["image"]


def test_cli_evaluate_reuses_fresh_generation_without_touching_heldout(
    dataset_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    heldout_image = _prepare_generation_cli_fixture(dataset_root)
    generate_data(dataset_root, seed=415)
    sidecar = dataset_root / "data/generated/transforms.csv"
    heldout_before = {
        "csv": (dataset_root / "data/heldout.csv").read_bytes(),
        "image": heldout_image.read_bytes(),
    }
    generated_before = {
        path.name: path.read_bytes()
        for path in sorted((dataset_root / "data/generated/calibration").glob("*.png"))
    }
    sidecar_before = sidecar.read_bytes()

    monkeypatch.setattr(
        dataset_module,
        "generate_data",
        lambda *args, **kwargs: pytest.fail("Fresh generated data was regenerated"),
    )
    monkeypatch.setattr(
        cli,
        "evaluate_split",
        lambda root, *, split, seed: EvaluationRun(
            records=[], metrics={}, checks={"passed": False, "failures": ["fixture gate"]}
        ),
    )

    assert cli.main(["evaluate", "--root", str(dataset_root), "--check"]) == 1
    assert (dataset_root / "data/generated/transforms-state.json").is_file()
    assert sidecar.read_bytes() == sidecar_before
    assert {
        path.name: path.read_bytes()
        for path in sorted((dataset_root / "data/generated/calibration").glob("*.png"))
    } == generated_before
    assert (dataset_root / "data/heldout.csv").read_bytes() == heldout_before["csv"]
    assert heldout_image.read_bytes() == heldout_before["image"]
