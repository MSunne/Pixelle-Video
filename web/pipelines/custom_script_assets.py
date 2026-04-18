# Copyright (C) 2025 AIDC-AI
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Custom script + asset pipeline UI

Dedicated tab for: uploaded assets + fixed script + reference audio cloning.
"""

import os
import time
import uuid
from pathlib import Path
from typing import Any

import streamlit as st
from loguru import logger

from pixelle_video.config import config_manager
from pixelle_video.models.progress import ProgressEvent
from web.components.content_input import render_version_info
from web.i18n import get_language, tr
from web.pipelines.base import PipelineUI, register_pipeline_ui
from web.utils.async_helpers import run_async
from web.utils.streamlit_helpers import check_and_warn_selfhost_workflow


class CustomScriptAssetsPipelineUI(PipelineUI):
    """UI for fixed script asset-based generation."""

    name = "custom_script_assets"
    icon = "🗣️"

    @property
    def display_name(self):
        return tr("pipeline.custom_script_assets.name")

    @property
    def description(self):
        return tr("pipeline.custom_script_assets.description")

    def render(self, pixelle_video: Any):
        left_col, middle_col, right_col = st.columns([1, 1, 1])

        with left_col:
            input_params = self._render_asset_and_script_input()
            render_version_info()

        with middle_col:
            config_params = self._render_service_and_voice_config(pixelle_video)

        with right_col:
            video_params = {
                "pipeline": self.name,
                **input_params,
                **config_params,
            }
            self._render_output_preview(pixelle_video, video_params)

    def _render_asset_and_script_input(self) -> dict:
        with st.container(border=True):
            st.markdown(f"**{tr('asset_based.section.assets')}**")

            with st.expander(tr("help.feature_description"), expanded=False):
                st.markdown(f"**{tr('help.what')}**")
                st.markdown(tr("asset_based.assets.what"))
                st.markdown(f"**{tr('help.how')}**")
                st.markdown(tr("asset_based.assets.how"))

            uploaded_files = st.file_uploader(
                tr("asset_based.assets.upload"),
                type=["jpg", "jpeg", "png", "gif", "webp", "mp4", "mov", "avi", "mkv", "webm"],
                accept_multiple_files=True,
                help=tr("asset_based.assets.upload_help"),
                key="custom_script_asset_files",
            )

            asset_paths = []
            if uploaded_files:
                session_id = str(uuid.uuid4()).replace("-", "")[:12]
                temp_dir = Path(f"temp/assets_{session_id}")
                temp_dir.mkdir(parents=True, exist_ok=True)

                for uploaded_file in uploaded_files:
                    file_path = temp_dir / uploaded_file.name
                    with open(file_path, "wb") as f:
                        f.write(uploaded_file.getbuffer())
                    asset_paths.append(str(file_path.absolute()))

                st.success(tr("asset_based.assets.count", count=len(asset_paths)))

                with st.expander(tr("asset_based.assets.preview"), expanded=True):
                    cols = st.columns(3)
                    for i, (file, path) in enumerate(zip(uploaded_files, asset_paths)):
                        with cols[i % 3]:
                            ext = Path(path).suffix.lower()
                            if ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
                                st.image(file, caption=file.name, use_container_width=True)
                            elif ext in [".mp4", ".mov", ".avi", ".mkv", ".webm"]:
                                st.video(file)
                                st.caption(file.name)
            else:
                st.info(tr("asset_based.assets.empty_hint"))

        with st.container(border=True):
            st.markdown(f"**{tr('custom_script_assets.section.script')}**")

            with st.expander(tr("help.feature_description"), expanded=False):
                st.markdown(f"**{tr('help.what')}**")
                st.markdown(tr("custom_script_assets.script.what"))
                st.markdown(f"**{tr('help.how')}**")
                st.markdown(tr("custom_script_assets.script.how"))

            script_text = st.text_area(
                tr("custom_script_assets.script.input"),
                placeholder=tr("custom_script_assets.script.placeholder"),
                help=tr("custom_script_assets.script.help"),
                height=220,
                key="custom_script_assets_text",
            )

            split_mode_options = {
                "paragraph": tr("split.mode_paragraph"),
                "line": tr("split.mode_line"),
                "sentence": tr("split.mode_sentence"),
            }
            script_split_mode = st.selectbox(
                tr("split.mode_label"),
                options=list(split_mode_options.keys()),
                format_func=lambda x: split_mode_options[x],
                index=0,
                help=tr("split.mode_help"),
                key="custom_script_assets_split_mode",
            )

        return {
            "assets": asset_paths,
            "script_text": script_text,
            "script_split_mode": script_split_mode,
        }

    def _render_service_and_voice_config(self, pixelle_video: Any) -> dict:
        with st.container(border=True):
            st.markdown(f"**{tr('asset_based.section.source')}**")

            with st.expander(tr("help.feature_description"), expanded=False):
                st.markdown(f"**{tr('help.what')}**")
                st.markdown(tr("asset_based.source.what"))
                st.markdown(f"**{tr('help.how')}**")
                st.markdown(tr("asset_based.source.how"))

            source_options = {
                "runninghub": tr("asset_based.source.runninghub"),
                "selfhost": tr("asset_based.source.selfhost"),
            }

            comfyui_config = config_manager.get_comfyui_config()
            has_runninghub = bool(comfyui_config.get("runninghub_api_key"))
            has_selfhost = bool(comfyui_config.get("comfyui_url"))

            source = st.radio(
                tr("asset_based.source.select"),
                options=list(source_options.keys()),
                format_func=lambda x: source_options[x],
                index=0,
                horizontal=True,
                key="custom_script_assets_source",
                label_visibility="collapsed",
            )

            if source == "runninghub":
                if not has_runninghub:
                    st.warning(tr("asset_based.source.runninghub_not_configured"))
                else:
                    st.info(tr("asset_based.source.runninghub_hint"))
            else:
                if not has_selfhost:
                    st.warning(tr("asset_based.source.selfhost_not_configured"))
                else:
                    st.info(tr("asset_based.source.selfhost_hint"))
                    check_and_warn_selfhost_workflow("selfhost/analyse_image.json")

        workflow_path = f"{source}/tts_index2.json"

        with st.container(border=True):
            st.markdown(f"**{tr('custom_script_assets.section.voice')}**")

            with st.expander(tr("help.feature_description"), expanded=False):
                st.markdown(f"**{tr('help.what')}**")
                st.markdown(tr("custom_script_assets.voice.what"))
                st.markdown(f"**{tr('help.how')}**")
                st.markdown(tr("custom_script_assets.voice.how"))

            ref_audio_file = st.file_uploader(
                tr("tts.ref_audio"),
                type=["mp3", "wav", "flac", "m4a", "aac", "ogg"],
                help=tr("tts.ref_audio_help"),
                key="custom_script_assets_ref_audio_upload",
            )

            ref_audio_path = None
            if ref_audio_file is not None:
                st.audio(ref_audio_file)

                temp_dir = Path("temp")
                temp_dir.mkdir(exist_ok=True)
                ref_audio_path = temp_dir / f"ref_audio_{uuid.uuid4().hex[:8]}_{ref_audio_file.name}"
                with open(ref_audio_path, "wb") as f:
                    f.write(ref_audio_file.getbuffer())
            else:
                st.info(tr("custom_script_assets.voice.required"))

            with st.expander(tr("tts.preview_title"), expanded=False):
                preview_text = st.text_input(
                    tr("tts.preview_text"),
                    value="大家好，这是一段测试语音。",
                    placeholder=tr("tts.preview_text_placeholder"),
                    key="custom_script_assets_preview_text",
                )

                if st.button(
                    tr("tts.preview_button"),
                    key="custom_script_assets_preview_button",
                    use_container_width=True,
                ):
                    if ref_audio_path is None:
                        st.warning(tr("custom_script_assets.voice.preview_missing"))
                    else:
                        with st.spinner(tr("tts.previewing")):
                            try:
                                audio_path = run_async(
                                    pixelle_video.tts(
                                        text=preview_text,
                                        inference_mode="comfyui",
                                        workflow=workflow_path,
                                        ref_audio=str(ref_audio_path),
                                    )
                                )

                                if audio_path:
                                    st.success(tr("tts.preview_success"))
                                    if os.path.exists(audio_path):
                                        st.audio(audio_path, format="audio/mp3")
                                    elif audio_path.startswith("http"):
                                        st.audio(audio_path)
                                    else:
                                        st.error("Failed to generate preview audio")

                                    st.caption(f"📁 {audio_path}")
                                else:
                                    st.error("Failed to generate preview audio")
                            except Exception as e:
                                st.error(tr("tts.preview_failed", error=str(e)))
                                logger.exception(e)

        return {
            "source": source,
            "tts_inference_mode": "comfyui",
            "tts_workflow": workflow_path,
            "ref_audio": str(ref_audio_path) if ref_audio_path else None,
        }

    def _render_output_preview(self, pixelle_video: Any, video_params: dict):
        with st.container(border=True):
            st.markdown(f"**{tr('section.video_generation')}**")

            if not config_manager.validate():
                st.warning(tr("settings.not_configured"))

            assets = video_params.get("assets", [])
            script_text = (video_params.get("script_text") or "").strip()
            ref_audio = video_params.get("ref_audio")

            if not assets:
                st.info(tr("asset_based.output.no_assets"))
                st.button(
                    tr("btn.generate"),
                    type="primary",
                    use_container_width=True,
                    disabled=True,
                    key="custom_script_assets_generate_disabled_assets",
                )
                return

            if not script_text:
                st.info(tr("custom_script_assets.output.no_script"))
                st.button(
                    tr("btn.generate"),
                    type="primary",
                    use_container_width=True,
                    disabled=True,
                    key="custom_script_assets_generate_disabled_script",
                )
                return

            if not ref_audio:
                st.info(tr("custom_script_assets.output.no_ref_audio"))
                st.button(
                    tr("btn.generate"),
                    type="primary",
                    use_container_width=True,
                    disabled=True,
                    key="custom_script_assets_generate_disabled_ref_audio",
                )
                return

            st.info(tr("custom_script_assets.output.ready", count=len(assets)))

            if st.button(
                tr("btn.generate"),
                type="primary",
                use_container_width=True,
                key="custom_script_assets_generate",
            ):
                if not config_manager.validate():
                    st.error(tr("settings.not_configured"))
                    st.stop()

                progress_bar = st.progress(0)
                status_text = st.empty()
                start_time = time.time()

                try:
                    from pixelle_video.pipelines.asset_based import AssetBasedPipeline

                    pipeline = AssetBasedPipeline(pixelle_video)

                    def update_progress(event: ProgressEvent):
                        if event.event_type == "analyzing_assets":
                            if event.extra_info == "start":
                                message = tr("asset_based.progress.analyzing_start", total=event.frame_total)
                            else:
                                message = tr("asset_based.progress.analyzing_complete", count=event.frame_total)
                        elif event.event_type == "analyzing_asset":
                            message = tr(
                                "asset_based.progress.analyzing_asset",
                                current=event.frame_current,
                                total=event.frame_total,
                                name=event.extra_info or "",
                            )
                        elif event.event_type == "generating_script":
                            if event.extra_info == "complete":
                                message = tr("asset_based.progress.script_complete")
                            else:
                                message = tr("asset_based.progress.generating_script")
                        elif event.event_type == "frame_step":
                            action_key = f"progress.step_{event.action}"
                            action_text = tr(action_key)
                            message = tr(
                                "progress.frame_step",
                                current=event.frame_current,
                                total=event.frame_total,
                                step=event.step,
                                action=action_text,
                            )
                        elif event.event_type == "processing_frame":
                            message = tr(
                                "progress.frame",
                                current=event.frame_current,
                                total=event.frame_total,
                            )
                        elif event.event_type == "concatenating":
                            if event.extra_info == "complete":
                                message = tr("asset_based.progress.concat_complete")
                            else:
                                message = tr("progress.concatenating")
                        elif event.event_type == "completed":
                            message = tr("progress.completed")
                        else:
                            message = tr(f"progress.{event.event_type}")

                        status_text.text(message)
                        progress_bar.progress(min(int(event.progress * 100), 99))

                    ctx = run_async(
                        pipeline(
                            assets=assets,
                            content_mode="script",
                            script_text=script_text,
                            script_split_mode=video_params.get("script_split_mode", "paragraph"),
                            source=video_params.get("source", "runninghub"),
                            tts_inference_mode="comfyui",
                            tts_workflow=video_params.get("tts_workflow"),
                            ref_audio=ref_audio,
                            progress_callback=update_progress,
                        )
                    )

                    total_time = time.time() - start_time

                    progress_bar.progress(100)
                    status_text.text(tr("status.success"))
                    st.success(tr("status.video_generated", path=ctx.final_video_path))
                    st.markdown("---")

                    if os.path.exists(ctx.final_video_path):
                        file_size_mb = os.path.getsize(ctx.final_video_path) / (1024 * 1024)
                        n_scenes = len(ctx.storyboard.frames) if ctx.storyboard else 0

                        info_text = (
                            f"⏱️ {tr('info.generation_time')} {total_time:.1f}s   "
                            f"📦 {file_size_mb:.2f}MB   "
                            f"🎬 {n_scenes}{tr('info.scenes_unit')}"
                        )
                        st.caption(info_text)
                        st.markdown("---")
                        st.video(ctx.final_video_path)

                        with open(ctx.final_video_path, "rb") as video_file:
                            video_bytes = video_file.read()
                            video_filename = os.path.basename(ctx.final_video_path)
                            st.download_button(
                                label="⬇️ 下载视频" if get_language() == "zh_CN" else "⬇️ Download Video",
                                data=video_bytes,
                                file_name=video_filename,
                                mime="video/mp4",
                                use_container_width=True,
                            )
                    else:
                        st.error(tr("status.video_not_found", path=ctx.final_video_path))

                except Exception as e:
                    status_text.text("")
                    progress_bar.empty()
                    st.error(tr("status.error", error=str(e)))
                    logger.exception(e)
                    st.stop()


register_pipeline_ui(CustomScriptAssetsPipelineUI)
