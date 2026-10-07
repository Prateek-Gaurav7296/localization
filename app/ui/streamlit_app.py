"""Streamlit UI for the ad localization pipeline.

The UI only collects inputs, calls ``localize_video`` and renders progress/results. All
processing lives in ``app.pipeline``. Launch with ``streamlit run streamlit_app.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from app.config import LANGUAGES, SUPPORTED_EXTENSIONS, Language, get_settings
from app.logging_config import configure_logging
from app.pipeline import LocalizationResult, PipelineError, Stage, StageStatus, localize_video

STATUS_ICONS = {
    StageStatus.PENDING: "⚪",
    StageStatus.RUNNING: "🔄",
    StageStatus.COMPLETED: "✅",
    StageStatus.FAILED: "❌",
}
AUTO_DETECT = None
RESULT_KEY = "localization_outcome"


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    st.set_page_config(page_title="Ad Localization", page_icon="🎬", layout="centered")
    st.title("Advertisement Video Localization")
    st.caption("Upload an ad, pick a target language, and get a lip-synced localized video.")
    _mock_banner(settings.pipeline.engine, settings.pipeline.lipsync_provider)

    st.subheader("1. Upload advertisement video")
    uploaded = st.file_uploader(
        "Advertisement video",
        type=list(SUPPORTED_EXTENSIONS),
        help=f"Supported formats: {', '.join(SUPPORTED_EXTENSIONS)}",
    )
    if uploaded is not None:
        st.video(uploaded)
        st.caption(f"{uploaded.name} · {uploaded.size / (1024 * 1024):.1f} MB")

    st.subheader("2. Select languages")
    col_source, col_target = st.columns(2)
    source = col_source.selectbox(
        "Source language",
        options=[AUTO_DETECT, *LANGUAGES],
        format_func=lambda lang: "Auto-detect" if lang is None else lang.label,
    )
    target: Language = col_target.selectbox(
        "Target language",
        options=list(LANGUAGES),
        format_func=lambda lang: lang.label,
    )
    same_language = source is not None and source == target
    if same_language:
        st.error("Source and target language must be different.")

    st.subheader("3. Localize")
    start = st.button(
        "Start localization",
        type="primary",
        disabled=uploaded is None or same_language,
        use_container_width=True,
    )
    if start and uploaded is not None:
        st.session_state[RESULT_KEY] = _run(uploaded, source, target)

    outcome = st.session_state.get(RESULT_KEY)
    if outcome:
        _render_outcome(*outcome)


def _run(uploaded, source: Language | None, target: Language) -> tuple[LocalizationResult | None, PipelineError | None]:
    with st.status(f"Localizing to {target.name}…", expanded=True) as status:
        rows = {stage: st.empty() for stage in Stage}
        for stage, row in rows.items():
            row.markdown(f"{STATUS_ICONS[StageStatus.PENDING]} {stage.label}")

        def on_progress(stage: Stage, state: StageStatus, detail: str | None) -> None:
            suffix = f" — {detail}" if detail else ""
            rows[stage].markdown(f"{STATUS_ICONS[state]} {stage.label}{suffix}")

        uploaded.seek(0)
        try:
            result = localize_video(
                uploaded,
                target.code,
                source.code if source else None,
                filename=uploaded.name,
                on_progress=on_progress,
            )
        except PipelineError as error:
            status.update(label=str(error), state="error", expanded=True)
            return error.result, error
        status.update(label="Localization complete", state="complete", expanded=False)
        return result, None


def _render_outcome(result: LocalizationResult | None, error: PipelineError | None) -> None:
    st.subheader("4. Result")
    if error:
        st.error(str(error))
    if result is None:
        return
    if result.run_id:
        st.caption(f"Run ID: `{result.run_id}`")

    if result.output:
        st.markdown("**Localized video**")
        video = result.output.localized_video
        st.video(str(video))
        st.download_button(
            "Download localized video", video.read_bytes(), file_name=f"localized_{result.target_language.code}.mp4",
            mime="video/mp4", use_container_width=True,
        )  # fmt: skip

    if result.intermediate:
        audio = result.intermediate.audio.path
        st.markdown("**Localized audio**")
        st.audio(str(audio))
        st.download_button(
            "Download localized audio", audio.read_bytes(),
            file_name=f"localized_{result.target_language.code}{audio.suffix}", use_container_width=True,
        )  # fmt: skip
        with st.expander("Transcript and translation"):
            st.markdown(f"**Transcript** ({result.intermediate.transcript.language})")
            st.write(result.intermediate.transcript.text)
            st.markdown(f"**Translation** ({result.intermediate.translation.target_language})")
            st.write(result.intermediate.translation.text)

    if result.input:
        with st.expander("Input stage outputs"):
            st.download_button(
                "Silent video (video.mp4)", result.input.silent_video.read_bytes(), file_name="video.mp4",
                mime="video/mp4",
            )  # fmt: skip
            st.download_button(
                "Extracted audio (audio.m4a)", result.input.source_audio.read_bytes(), file_name="audio.m4a",
                mime="audio/mp4",
            )  # fmt: skip

    with st.expander("Processing metadata", expanded=error is not None):
        cols = st.columns(3)
        cols[0].metric("Target", result.target_language.name)
        cols[1].metric("Engine", result.engine)
        cols[2].metric("Lip-sync", result.lipsync_provider)
        st.table(
            [
                {
                    "Stage": stage.label,
                    "Status": f"{STATUS_ICONS[result.stages[stage]]} {result.stages[stage].value}",
                    "Seconds": result.stage_seconds.get(stage),
                }
                for stage in Stage
            ]
        )
        if result.manifest_path and result.manifest_path.is_file():
            st.json(json.loads(Path(result.manifest_path).read_text(encoding="utf-8")), expanded=False)


def _mock_banner(engine: str, lipsync_provider: str) -> None:
    mocks = []
    if engine == "mock":
        mocks.append(
            "**STT/TTT/TTS is mocked**: no transcription or translation happens, and the original audio is reused"
        )
    if lipsync_provider == "mock":
        mocks.append("**Lip-sync is mocked**: the audio track is replaced without lip-sync")
    if mocks:
        st.warning("Development mode.\n\n- " + "\n- ".join(mocks), icon="⚠️")
