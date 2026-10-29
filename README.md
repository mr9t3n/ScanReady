# ScanReady — Document Image Quality Gate

## Download, extract, run

1. Download the ScanReady release zip and extract the entire archive to a short, writable folder, such as `C:\ScanReady` on Windows or `~/ScanReady` on macOS/Linux. Do not run it inside the zip viewer.
2. Open the extracted `ScanReady` folder and run the script for your computer:

   | Platform | Start ScanReady |
   | --- | --- |
   | Windows x64 | Double-click `ScanReady-Windows.bat` |
   | macOS Apple Silicon or Intel | Open `ScanReady-macOS.command` |
   | Linux x86_64 | Run `bash ScanReady-Linux.sh` in that folder |

3. Keep the terminal open while setup installs the bundled Python environment and opens your browser. If the browser does not open, visit the localhost URL printed in the terminal. No administrator access or separate Python installation is needed for a complete release bundle.

**Release status:** the app, one-script setup and all three demos were tested from this release archive on Windows 11 Pro and macOS. The bundled evaluation uses 184 eligible public scans (137 for tuning, 47 held out for testing). Its results fall short of the original acceptance bar, and those shortfalls are disclosed below and in the **Evaluation Results** tab.

On Windows, if the downloaded archive is blocked, right-click the zip, choose **Properties**, select **Unblock** if shown, apply, and extract again. Keep the full folder together and avoid long extraction paths. On macOS, use the file's **Open** action if Finder requests confirmation for a trusted download; managed computers may require their administrator's normal approval process. If executable permissions were lost during extraction, run `bash ScanReady-macOS.command` from Terminal in the extracted folder.

Stop ScanReady with **Ctrl+C** in its terminal. Closing the browser alone does not stop the server. To uninstall, stop it and delete its extracted folder; saved downloads remain wherever your browser put them. Setup creates a private `.runtime` folder and does not alter PATH, shell profiles, or the registry. Streamlit may keep a small user settings directory.

## Check a document

Choose **Analyze Document**, then upload a JPEG/JPG, PNG, or PDF, or select a demonstration. Files are processed locally. The app checks blur, skew, glare, shadow, low contrast, and cropped edges, with labeled evidence and one decision:

- **READY:** no failed quality check.
- **READY AFTER CORRECTION:** all failed checks have a supported correction. Apply the offered corrections and inspect the re-check; a correction is not a guarantee of READY.
- **RETRY CAPTURE:** take a better image because at least one failure cannot be corrected safely.

The result starts with a coloured verdict banner. The evidence image outlines what each check measured (green = pass, amber = warning, red = fail; the dashed outline is the detected page edge) and lists every check with its status in a readable panel. Warnings point at things worth a quick look but don't block READY. Measurements and limits are under **Details**.

Corrections can deskew, normalize lighting, and enhance contrast. They are offered only when every failed check can be corrected; a RETRY CAPTURE result names the checks that need a retake instead. Lighting and contrast corrections are kept only if they improve the check they target; otherwise the app skips them and says why. After **Apply selected corrections and re-check**, the before/after images appear with **Download corrected PNG** directly beneath them, followed by the re-check and a **What changed** list that flags any check that got worse. Straightening places the page on a larger canvas and fills the new corner areas with dark grey; no content is cropped. The download saves a new PNG through your browser without the evidence overlay and preserves the original. Choose another document to start a new analysis. An uploaded file takes precedence over the demo selector; the app says so, and you can clear the upload to use a demo.

Two advisories never change the verdict: if a page looks sideways, the app says so and offers **Rotate left/right 90° and re-check** (plus **Download rotated PNG**); if the detected page covers under 30% of the image, the app notes that the image may not be a document page or that you should move closer.

The real archived clean scan returns READY. The real archived correctable scan returns READY AFTER CORRECTION and becomes READY after deskew. The real motion-blurred SmartDoc retry demo returns RETRY CAPTURE; see `demo_images/SMARTDOC_NOTICE.md` for attribution and processing details. Clean-demo source details are in `demo_images/CLEAN_SOURCE.md`, correctable-demo provenance is in `demo_images/CORRECTABLE_SOURCE.md`, and the exact supplied license notice is in `demo_images/KAGGLE_NOTICE.md`.

## Supported inputs and limits

JPEG and JPG are the same format; PNG and PDF are also supported. Intake validates file contents. The upload limit is 25 MiB; decoded images are limited to 40 megapixels and must have a short side of at least 400 pixels. Analysis uses a reduced working image.

**PDF: analyzing the first page only.** The first page is rasterized at up to 200 DPI, reduced if needed for the pixel limit. Later pages are not analyzed. Empty, corrupt, or password-protected PDFs are rejected with a readable message. A corrected PDF page exports as PNG, never as a modified PDF.

## Evaluation Results

The **Evaluation Results** tab shows the saved held-out results: image count, recommendation accuracy, timing and deskew success first, then the unmet acceptance requirements, per-defect performance and the outcome confusion matrix. The "Evaluated with disclosed shortfalls" label appears only when the status file agrees with every report hash. Missing, malformed, or mismatched status is shown as invalid or stale and never synthesized into success. JSON, CSV, and summary downloads are available when present. Missing reports show a pending message. A configuration mismatch is flagged; old reports do not qualify changed settings. Evaluation results are separate from the setup and platform checks.

**Results in brief.** The held-out test covers 47 clean public scans (no phone captures). Recommendation accuracy is 61.7% (29 of 47); the slowest analysis took 1.57 seconds. Sixteen of the eighteen mistakes are Crop false alarms caused by scanner-edge marks, a known limitation. The original acceptance bar asked for at least 50 held-out images, at least six real examples of each defect, 80% accuracy and measured deskew accuracy; those requirements were not met, because the available public scans are clean and contain no labeled defects. This shortfall is accepted as the project's documented cut line: held-out data was never changed and no setting was tuned on held-out results. The 47 test pages have now been scored, so any future detector change needs a new, untouched test set. Generated transform variants are unscored development assets, not labeled evidence. Example evidence overlays are in `results/examples/`. See `results/development-disclosure.md` and `data/README.md` for population, provenance, labeling and source-page details; `results/calibration.md` covers the 137 tuning scans included in this release.

## Optional verification commands

With the ScanReady Python environment active, run from this folder:

```bash
python -m pytest -q
python -m scanready selftest
python -m scanready check-demos
python -m scanready launch --no-browser --smoke
```

`check-demos` checks all three outcomes, corrects the recoverable demo, saves and reopens a temporary PNG, requires its result to be READY, and verifies unchanged input hashes. Temporary correction files are removed automatically.

After real-data preparation and a frozen calibration configuration:

```bash
python -m scanready evaluate --split heldout --check
```

This writes reports and returns failure for unmet acceptance criteria. Do not tune settings from held-out results. Re-running evaluation overwrites the current reports; retain the qualification evidence before another run.

For a complete extracted release, the platform script accepts `--check --offline-only` to install from bundled assets, run self-test, and smoke-test browser-server health. On Windows:

```bat
ScanReady-Windows.bat --check --offline-only
```

On native Windows, `python tools/verify_release.py --latest` verifies the latest archive and runs its fresh offline extraction check. To inspect only archive structure on another platform, pass its explicit path: `python tools/verify_release.py ../dist/ScanReady_YYYYMMDD_HHMM.zip`. No final archive currently exists.

## Developer setup and tested environments

Use Python 3.11 and the pinned dependencies:

```bash
conda env create -f environment.yml
conda activate scanready
python -m pip check
python -m scanready launch
```

For WSL, use a Linux Python environment on Ubuntu's filesystem. Do not reuse the Windows Conda interpreter or a Windows `.runtime` directory. This workspace's Linux environment is named `scanready`.

Tested on September 28, 2026 by extracting the release archive: Windows 11 Pro (Dell Precision 7820) and macOS — setup, launch, all three demos, correction and download, PNG, single-page and multi-page PDF, and oversized-file rejection. Automated tests run on Linux/WSL; six native-Windows/Unix-shell tests are skipped there. Bundles exist for Windows x64, Linux x86_64, macOS Intel and macOS Apple Silicon; Linux was tested under WSL only. If the browser opens without the full address, copy the `http://127.0.0.1:…` URL printed in the terminal.

## Limitations

The checks use classical OpenCV heuristics and need representative real-data calibration. Known development false positives include scanner-edge marks interpreted as cropped content, mixed-orientation printed content interpreted as page skew, copier surrounds contaminating paper/shadow estimates, and dim but readable captures failing absolute contrast. No detector repair is included in this candidate; each mechanism requires paired positive/negative regressions before future changes. Pure-white scans can trigger glare warnings because bright paper and saturated glare can look alike; white table cells alone are not treated as glare. Glare that cannot be distinguished from already-white paper may be missed. Full-page scans with blank margins receive a boundary warning; dark content or borders touching an edge can still trigger a crop failure. Sparse text is assessed using foreground-to-paper contrast. Page detection, heavy perspective distortion, unusual backgrounds, and very faint content can affect measurements. Corrections may leave defects; always inspect the re-check and output.

Known gaps observed during hands-on testing: page orientation is not a quality check. A page turned 90° can still receive READY; the sideways advisory catches most such pages but not all, and an upside-down (180°) page can't be detected without reading the text. Scanner-edge marks along a border can make **Crop** fail on a page that isn't cropped, which can turn an otherwise fixable page into RETRY CAPTURE. Light-coloured text on digital documents (for example blue headings or grey labels) can fail **Low Contrast** even though it is readable; contrast enhancement often resolves it. Dark-background images, slides, app screenshots and other non-document pictures may be misread, because the page detector looks for a light page and can mistake one light shape for the page.

ScanReady does not implement OCR, PDF text extraction, document question answering, RAG, handwriting recognition, semantic classification, restoration of missing cropped content, whole-PDF analysis, PDF editing or reconstruction, cloud deployment, mobile applications, or deep learning. `FUTURE_WORK.md` describes those unimplemented extensions. Third-party notices are in `THIRD_PARTY_NOTICES.md`.
