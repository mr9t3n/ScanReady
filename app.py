from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import cv2
import streamlit as st

from scanready.image_io import InputError, load_document
from scanready.overlay import draw_evidence
from scanready.pipeline import advisories, analyze, correct_with_review, rotate_page
from scanready.report import AnalysisReport, Outcome, Status
from scanready.results_ui import show_evaluation


ROOT = Path(__file__).resolve().parent
DEMOS = {
    "Clean document": ROOT / "demo_images" / "clean_document.jpg",
    "Correctable document": ROOT / "demo_images" / "correctable_document.jpg",
    "Retry capture document": ROOT / "demo_images" / "retry_capture_document.jpg",
}
VERDICTS = {
    Outcome.READY: (st.success, "READY — this page is ready to use."),
    Outcome.READY_AFTER_CORRECTION: (
        st.warning,
        "READY AFTER CORRECTION — fixable problems were found. Apply the corrections below and inspect the re-check.",
    ),
    Outcome.RETRY_CAPTURE: (
        st.error,
        "RETRY CAPTURE — take a new photo or scan; at least one problem can't be fixed safely.",
    ),
}
LEGEND = (
    "Outline colours: green = pass, amber = warning, red = fail. "
    "The dashed outline marks the detected page edge."
)


def _title(name: str) -> str:
    return name.replace("_", " ").title()


def _rgb(image_bgr):
    return cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)


def show_report(report: AnalysisReport, image_bgr, *, heading: str) -> None:
    st.subheader(f"{heading}: {report.outcome.value}")
    show_banner, meaning = VERDICTS[report.outcome]
    show_banner(meaning)
    if any(check.status is Status.WARN for check in report.checks):
        st.caption("⚠️ Warnings point at things worth a quick look, but they don't block READY.")
    st.image(_rgb(draw_evidence(image_bgr, report)), caption=f"{heading} evidence", use_column_width="auto")
    st.caption(LEGEND)
    for check in report.checks:
        icon = "✅" if check.status is Status.PASS else "⚠️" if check.status is Status.WARN else "❌"
        st.markdown(f"{icon} **{_title(check.name)} - {check.status.value}**")
        st.caption(f"{check.message} {check.next_action}")
    with st.expander(f"Details: {heading.lower()} measurements and limits"):
        for check in report.checks:
            st.text(f"{_title(check.name)}: measured {check.measurement:.3g} {check.unit}; limit {check.limit:.3g}")


def _rotate(key: str, turns: int) -> None:
    st.session_state[key] = (st.session_state.get(key, 0) + turns) % 4


st.set_page_config(page_title="ScanReady", page_icon="📄", layout="wide")
st.title("ScanReady")
st.markdown("Upload a **JPEG, JPG, PNG, or PDF** document to check whether it is ready to use.")

analyze_tab, evaluation_tab = st.tabs(["Analyze Document", "Evaluation Results"])

with analyze_tab:
    demo_name = st.selectbox("Or try a demonstration", ["None", *DEMOS])
    upload = st.file_uploader("Choose a document", type=["jpg", "jpeg", "png", "pdf"])
    data = None
    display_name = None
    if upload is not None:
        data = upload.getvalue()
        display_name = upload.name
        if demo_name != "None":
            st.caption("Using your uploaded file. Remove it to try the selected demonstration.")
    elif demo_name != "None":
        demo_path = DEMOS[demo_name]
        if demo_path.exists():
            data = demo_path.read_bytes()
            display_name = demo_path.name
        else:
            st.warning("That demonstration image is not installed yet.")

    if data is None:
        st.info("Choose a document or demonstration to begin. Your file stays on this computer.")
    else:
        document_id = sha256(data).hexdigest()[:16]
        rotation_key = f"rotation-{document_id}"
        turns = st.session_state.get(rotation_key, 0)
        if data.startswith(b"%PDF-"):
            st.info("PDF: analyzing the first page only. Corrected output is saved as PNG.")
        try:
            with st.spinner("Analyzing…"):
                loaded = rotate_page(load_document(data, display_name=display_name), turns)
                report = analyze(loaded)
                notes = advisories(loaded.image_bgr, report)
        except InputError as exc:
            st.error(str(exc))
        else:
            stem = Path(display_name or "document").stem
            if turns:
                direction = {1: "90° right", 2: "180°", 3: "90° left"}[turns]
                st.info(f"Rotated {direction} for analysis. The original file is unchanged.")
            for note in notes:
                st.warning(note.message)
                if note.kind == "sideways":
                    left, right = st.columns(2)
                    left.button("⟲ Rotate left 90° and re-check", key=f"rotate-left-{document_id}",
                                on_click=_rotate, args=(rotation_key, -1))
                    right.button("⟳ Rotate right 90° and re-check", key=f"rotate-right-{document_id}",
                                 on_click=_rotate, args=(rotation_key, 1))
            show_report(report, loaded.image_bgr, heading="Original result")
            if turns:
                ok, encoded = cv2.imencode(".png", loaded.image_bgr)
                if ok:
                    st.download_button("Download rotated PNG", encoded.tobytes(), file_name=f"{stem}_rotated.png",
                                       mime="image/png", key=f"download-rotated-{document_id}-{turns}")
            if report.outcome is Outcome.RETRY_CAPTURE:
                unfixable = [check for check in report.checks if check.status is Status.FAIL and not check.recoverable]
                names = ", ".join(_title(check.name) for check in unfixable)
                st.error(
                    f"Retake needed: {names} can't be corrected, so corrections aren't offered. "
                    + " ".join(dict.fromkeys(check.next_action for check in unfixable))
                )
                offered = []
            else:
                offered = [check for check in report.checks if check.status is Status.FAIL and check.recoverable and check.correction]
            selected = {
                check.correction
                for check in offered
                if st.checkbox(
                    f"Apply: {check.next_action}",
                    value=True,
                    key=f"correct-{document_id}-{turns}-{check.name}",
                )
            }
            if offered and st.button("Apply selected corrections and re-check", type="primary", key=f"apply-{document_id}-{turns}"):
                review = correct_with_review(loaded, report, selected)
                corrected, corrected_report = review.image, review.report
                left, right = st.columns(2)
                left.image(_rgb(loaded.image_bgr), caption="Original", use_column_width="auto")
                right.image(_rgb(corrected), caption="Corrected", use_column_width="auto")
                if "deskew" in review.applied:
                    st.caption(
                        "Straightening rotates the page onto a larger canvas and fills the new corner areas "
                        "with dark grey; no page content is cropped."
                    )
                ok, encoded = cv2.imencode(".png", corrected)
                if ok:
                    st.download_button(
                        "Download corrected PNG",
                        encoded.tobytes(),
                        file_name=f"{stem}_scanready.png",
                        mime="image/png",
                        type="primary",
                        key=f"download-{document_id}-{turns}",
                    )
                show_report(corrected_report, corrected, heading="Corrected re-check")
                st.subheader("What changed")
                for message in review.skipped.values():
                    st.info(message)
                for change in review.changes:
                    if change.before_status is change.after_status and change.before_measurement == change.after_measurement:
                        continue
                    icon = "🔻" if change.worse else "✅" if change.improved else "•"
                    note = " — got worse; check the corrected page" if change.worse else ""
                    st.markdown(
                        f"{icon} **{_title(change.name)}**: {change.before_status.value} → "
                        f"{change.after_status.value} ({change.before_measurement:.3g} → {change.after_measurement:.3g}){note}"
                    )

with evaluation_tab:
    show_evaluation(ROOT)
