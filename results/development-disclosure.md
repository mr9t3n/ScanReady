# Development evaluation disclosure

**Development candidate — NOT QUALIFIED.**

This descriptive report evaluates the frozen `development-approved-v1` population only. It does not qualify ScanReady or replace the final heldout requirement.

## Population difference

- Working dataset: 202 images = 144 calibration + 58 heldout; 184 scholarly scans + 18 local-only operator captures.
- Evaluated development population: 184 scholarly scans = 137 calibration + 47 heldout.
- Omitted from this development population: all 18 operator captures, including 7 calibration and 11 heldout rows.
- Capture performance is unavailable because the evaluated population contains zero captured images.
- The 47 evaluated source memberships are exposed by this run. Future tuning requires a genuinely untouched heldout population; renaming or restoring these CSVs cannot restore independence.

## Preserved failed gates

- Heldout size is 47, below the required 50.
- Support is zero for blur, skew, glare, shadow, low contrast, and cropped edges; absent support is not a passing result.
- Recommendation accuracy is 0.617, below the required 0.80.
- Eligible recoverable 2–15 degree deskew evidence is unavailable (0 eligible).
- Final coverage, native-platform, extracted-archive, and manual walkthrough gates remain open.

Generated transform variants are unscored development assets and were not evaluated as labeled heldout examples. No thresholds, labels, splits, or detector behavior were changed after seeing these results.
