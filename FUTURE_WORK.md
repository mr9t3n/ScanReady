# Future Work

This is a roadmap only. Nothing below is implemented, exposed in the app, installed as a dependency, or part of ScanReady's current results. Each item names the place in the current design where it would plug in, so future work can start from a clear seam.

## Extending what ScanReady can do

| Capability (not implemented) | Where it would fit |
| --- | --- |
| OCR and transcription | A text-extraction service that reads the immutable `LoadedPage` after safe intake, alongside — not inside — the six quality checks. |
| PDF text extraction | A PDF text service built next to `scanready/pdf_io.py`; page-image analysis stays first-page rasterization. |
| Document question answering and RAG | A document-understanding layer that works on extracted text, outside the computer-vision quality pipeline. |
| Handwriting recognition | A recognizer that uses the text-extraction service and the page model without changing readiness decisions. |
| Semantic document classification | Optional classifiers behind the existing report types, leaving the six-check contract unchanged. |
| Restoration of missing cropped content | A separate, clearly labeled restoration step that never implies lost content was recovered by the current app. |
| Whole-PDF and multi-page analysis | A document coordinator that runs the existing single-page pipeline on each page. Today only page 1 is analyzed and there is no page selector. |
| PDF editing or reconstruction | A separate export service. Current corrections are saved as new PNG files and never modify the uploaded PDF. |
| Deep-learning detectors | Optional detector implementations behind the existing report types, once model size, licensing and offline runtime costs are justified. |
| Cloud deployment | A deployment adapter that keeps today's localhost-only, local-data defaults as the safe baseline. |
| Mobile applications | A mobile client built on a stable pipeline API rather than inside the desktop app. |

## Improving the current checks

These came out of evaluation and hands-on testing. They are not implemented.

| Improvement (not implemented) | Why | Where it would fit |
| --- | --- | --- |
| Page-orientation check (90°/180°) | A sideways page can still be READY; today only a display advisory warns about most such pages, and upside-down pages go unnoticed. | A seventh check or an extension of the skew check in `scanready/checks.py`, plus a rotate correction; needs its own calibration. |
| Crop check that ignores scanner-edge marks | Scanner-edge marks cause most evaluation mistakes (16 of 18) by flagging unclipped pages. | `scanready/checks.py` crop check, with paired tests of edge marks versus truly clipped text. |
| Contrast check for coloured or grey digital text | Readable blue headings or grey labels can fail Low Contrast. | `scanready/checks.py` contrast check, with paired tests of dim-but-readable versus washed-out text. |
| Non-document detection | A slide or screenshot can be mistaken for a page. | Page detection in `scanready/page.py`; today a display advisory notes small detected pages. |
| Shadow and copier-surround handling | Dark copier borders can distort paper and shadow estimates. | `scanready/page.py` and the shadow check, with paired tests of a dark surround versus a real shadow across the paper. |

## Stronger evaluation

The current results cover 47 clean public scans and fall short of the original acceptance bar (see the README). A stronger evaluation would add a fresh, untouched test set with at least six real examples of each defect (blur, tilt of 2–15°, glare, shadow, faint text and cut-off edges) plus clean controls, including phone captures. Generated transform variants remain unscored development assets and can't replace real labeled examples.

Any item above needs its own approved specification, tests, dependencies, user-facing disclosure and release-manifest update before it can be described as supported.
