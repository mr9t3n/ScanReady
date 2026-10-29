# Third-Party Notices

ScanReady uses `pypdfium2==4.30.0` to rasterize the first page of an uploaded PDF. Version 4.30.0 is the latest stable release that provides binary wheels for all four supported platform bundles, including both Intel and Apple Silicon macOS. It does not extract PDF text, edit PDFs, or rebuild corrected pages as PDFs.

## pypdfium2 and PDFium

- pypdfium2 project code is distributed under Apache-2.0 and BSD-3-Clause terms.
- The bundled PDFium binary is distributed under BSD-style terms and includes notices for its own third-party components.
- Every platform wheel included in a ScanReady release must retain all license and notice files bundled with that wheel.
- The release builder and verifier must fail if a required platform wheel or its associated license material is missing.

Upstream references:

- https://github.com/pypdfium2-team/pypdfium2
- https://pdfium.googlesource.com/pdfium/+/main/LICENSE

This notice summarizes the release obligation; the authoritative license texts shipped with the dependency remain controlling.

## SmartDoc retry demonstration

The real retry demonstration is derived from the CC BY 4.0 SmartDoc 2015 Challenge 1 sample dataset. Source, authors, required citation, license link, and frame/JPEG conversion details are preserved in `demo_images/SMARTDOC_NOTICE.md`. This is a development demonstration, not an independent held-out sample.

The real clean and correctable demonstrations are metadata-free archived Kaggle scans. The publisher source declaration and exact user-approved MIT notice are retained in `demo_images/KAGGLE_NOTICE.md`; file identities and transformations are in `demo_images/CLEAN_SOURCE.md` and `demo_images/CORRECTABLE_SOURCE.md`. All three real demos use calibration/development source pages, never heldout pages.
