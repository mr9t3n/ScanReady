# ScanReady Dataset Intake

Raw datasets are local development inputs. They are immutable, excluded from the release zip, and never placed directly in normalized output folders.

## Where to place downloads

```text
data/raw/sources/smartdoc2015/
data/raw/sources/diqa5000/
data/raw/sources/docunet/
data/raw/sources/ssd_dis/
data/raw/captured/
```

Use one directory per source. Keep the original filenames, directory structure, annotations, README, license, citation, and download record. Operator-created phone photos go in `data/raw/captured/`.

Do not place raw downloads in `data/sources/` or `data/captured/`; those directories are produced by the ScanReady normalization pipeline.

## Recommended scholarly sources

1. **ICDAR2015 Competition on Smartphone Document Capture and OCR (SmartDoc), Challenge 1** - primary recommendation. It contains handheld smartphone video frames with focus/motion blur, perspective changes, illumination changes, and partial occlusion. The [official dataset page](https://sites.google.com/site/icdar15smartdoc/challenge-1/dataset) identifies a CC BY 4.0 license and links to [Zenodo record 1230218](https://zenodo.org/record/1230218).
2. **DIQA-5000 / VQualA 2025 Document Image Quality Assessment Challenge** - optional quality-score supplement. It contains 5,000 enhanced document images derived from 500 mobile captures with human mean-opinion scores. Start with the [official ICCV workshop paper](https://openaccess.thecvf.com/content/ICCV2025W/VQualA/html/Huang_VQualA_2025_Document_Image_Quality_Assessment_Challenge_ICCVW_2025_paper.html). A [public dataset repository](https://github.com/1536125762/DIQA-5000-Dataset) is available, but do not treat repository visibility as redistribution permission; retain and review any terms delivered with the download.
3. **DocUNet: Document Image Unwarping via a Stacked U-Net - Benchmark Dataset** - optional geometry supplement. The [official project page](https://www3.cs.stonybrook.edu/~cvl/docunet.html) provides original photos, centered document crops, and flatbed scans. Use a small subset for skew/crop/page-boundary stress tests, subject to its redistribution terms.
4. **SSD-DIS: A Semi-Synthetic Shadow Dataset for Document Images** - optional shadow supplement. The [Scientific Data article](https://www.nature.com/articles/s41597-026-07204-4) links the dataset at [figshare DOI 10.6084/m9.figshare.29401811](https://doi.org/10.6084/m9.figshare.29401811). It provides paired shadow-free documents, shadow masks, and shadowed images. A small paired subset is sufficient; the full dataset is unnecessary for this classical-CV evaluation.

Large datasets are not copied wholesale into the final zip. Only normalized, metadata-free evaluation images whose terms allow redistribution may be staged. When terms are unclear, keep images local and release aggregate results only.

## Required provenance record

For every source, record:

- source slug and full title;
- official download URL and access date;
- paper citation/DOI;
- license or usage terms and local license-file path;
- original archive SHA-256;
- whether redistribution in a class submission is allowed;
- source document/video grouping key;
- files selected and reason for selection.

## Minimum practical collection

- At least 10 clean controls.
- At least six examples each for blur, skew, glare, shadow, low contrast, and cropped edges.
- At least 20 operator-captured photographs when practical, because they best represent the actual phone/camera workflow.
- At least 50 total held-out images after grouping by source page.

Never tune thresholds or relabel samples after inspecting held-out results.

## Download priority

Do not download every optional dataset merely because it is listed.

1. Download SmartDoc Challenge 1 and its ground truth/license material.
2. Add operator captures covering the six ScanReady checks, especially glare because scholarly sets may not match the intended phone workflow.
3. Review class balance after source-page grouping.
4. Add only the optional source needed to close an evidenced gap: DocUNet for geometry, SSD-DIS for shadows, or DIQA-5000 for perceptual-quality comparison.

The Sprint 03 intake report must justify every selected source and must show that near-duplicate frames, derived variants, and paired images stay within one split.

## Label and prepare the selected images

Enter scholarly selections in `data/sources.csv` and your own captures in `data/captured_labels.csv`, using the existing header. Each row needs its unchanged `raw_relative_path`, stable `source_page_id`, expected outcome, defect labels, and provenance/redistribution fields. Use `clean` explicitly for a clean page. Assign all frames, crops, paired images, and photographs of the same physical source page the same `source_page_id`; separate filenames do not establish separate pages.

Use `calibration` or `heldout` in `split`, or leave it blank for the deterministic seed-415 assignment. Audit grouping and class coverage before freezing the split. Then, from the application folder:

```bash
python -m scanready prepare-data
python -m scanready generate-data
python -m scanready calibrate
```

Preparation preserves raw bytes, normalizes selected images, and writes `manifest.csv` plus physically separate `calibration.csv` and `heldout.csv` under `data/`. Generation reads only `calibration.csv`, selects non-generated clean READY bases, and writes eight deterministic PNG transformations per eligible base under `data/generated/calibration/`. It records those assets separately in `data/generated/transforms.csv` and does not modify any scored CSV.

Transform sidecar rows use `annotation_status=transform_only` and `nominal_transform`; they intentionally contain no defect, severity, expected-outcome, correction-permission, or true-skew label. A rotation angle is an applied transformation parameter, not an independently measured true skew. Blank margins, brightness, or scale never acquire a quality label merely because the transform ran. The sidecar assets are unscored and release-excluded. If `calibration.csv` still contains legacy generated rows, rerun `prepare-data`; generation rejects them before replacing its PNGs or sidecar.

Calibration opens only the original labeled rows in `calibration.csv`; a missing, empty, mixed-split, or generated-contaminated file is rejected. Its report states that transform-only variants are unscored without opening the combined manifest or held-out data. Calibration records performance without automatically changing thresholds. Review and record any calibration-only threshold decisions before freezing the configuration. Do not edit split files by hand, inspect held-out labels to tune, or rerun preparation after the split is frozen. These files enforce the application's read boundary, not filesystem permissions against arbitrary scripts.

Once configuration and splits are frozen, run `python -m scanready evaluate --split heldout --check`. Its output must satisfy the real-data requirements; controlled fixtures never close that gate. Transform-only generated files, their sidecar, and raw data remain excluded from releases. Final release staging must reconcile manifests with the selected redistributable images and preserve source licensing material.

## Current local intake and orientation

The user-provided batch contains 200 Kaggle scans and 22 phone PNGs. All 55 previously unresolved inputs now have visual-review dispositions: 41 admitted and 14 excluded for a missing-image placeholder or degradation not reliably attributable to the six supported defects. The working manifests contain 186 scans and all 22 phone captures (208 images). Labels comprise 199 controls, eight cropped-content cases, and one recoverable-skew case; one crop case also has skew. Earlier control labels retain their original visual-review provenance and uncertainty.

The deterministic split contains 147 calibration and 61 held-out images. Held-out count now exceeds the minimum, but its labels cover only controls and two crop cases; six-defect and recoverable-rotation coverage are not satisfied. No held-out predictions have been inspected. Mild dim exposure is not automatically a defect; copier noise is not automatically optical blur or physical glare/shadow.

A 21 MB official SmartDoc sample was downloaded separately, with archive checksum, CC BY 4.0 declaration, citation, and frame extraction provenance. One visibly motion-blurred frame supplies the real retry demo; it is reserved as development material and has not been added to the held-out evaluation. Other sample frames remain unscored source candidates.

The optional CSV column `orientation_clockwise_degrees` accepts `0`, `90`, `180`, or `270`. It applies after EXIF orientation and records the additional turn in the normalized manifest. It does not add automatic page-direction recognition to the app. Conflicting rotations for identical raw bytes are rejected. Originals are never rotated in place. Upright working copies of all 22 phone inputs are in `data/generated/intake-preview/<phone_capture_group>/`; these remain local and release-excluded.

The current 208-image evaluation inputs remain local-only while redistribution evidence is reconciled. The separately attributed SmartDoc retry demo is approved by its published CC BY 4.0 terms. Raw working images may contain metadata or sensitive content; normalization removes image metadata without asserting permission to redistribute the depicted content. No held-out qualification run has been performed for this batch.
