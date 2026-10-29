# ScanReady Held-Out Evaluation

**Qualification status: FAIL**

## Dataset composition

- Images: 47
- scholarly: 47 images
- Seed: 415
- Configuration SHA-256: `c930ec5613ef2610b41c7f7dbf48a1106f2d9a970f3fc4f18d0b13d344f25e27`

## Outcome performance

- Recommendation accuracy: 0.617
- Slowest analysis: 1.573 seconds
- Deskew accuracy: N/A (no eligible cases)

## Real-capture performance

No real captures were evaluated; capture performance is unmeasured.

## Per-defect performance

- blur: precision —, recall —, F1 — (support 0; false positives 0)
- cropped_edges: precision —, recall —, F1 — (support 0; false positives 16)
- glare: precision —, recall —, F1 — (support 0; false positives 0)
- low_contrast: precision —, recall —, F1 — (support 0; false positives 1)
- shadow: precision —, recall —, F1 — (support 0; false positives 0)
- skew: precision —, recall —, F1 — (support 0; false positives 2)

## Acceptance failures

- Held-out evaluation requires at least 50 held-out images; found 47.
- Held-out evaluation requires at least 6 blur examples; found 0.
- Held-out evaluation requires at least 6 skew examples; found 0.
- Held-out evaluation requires at least 6 glare examples; found 0.
- Held-out evaluation requires at least 6 shadow examples; found 0.
- Held-out evaluation requires at least 6 low_contrast examples; found 0.
- Held-out evaluation requires at least 6 cropped_edges examples; found 0.
- Recommendation accuracy must be at least 0.80; measured 0.617.
- Deskew accuracy requires recoverable 2-15 degree held-out rotations; found 0.
