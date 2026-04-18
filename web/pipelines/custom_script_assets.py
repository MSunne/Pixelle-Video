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

import httpx
import streamlit as st
from loguru import logger

from pixelle_video.config import config_manager
from web.components.content_input import render_version_info
from web.i18n import get_language, tr
from web.pipelines.base import PipelineUI, register_pipeline_ui
from web.utils.async_helpers import run_async

API_BASE_URL = (
    os.getenv("PIXELLE_WEB_API_BASE_URL")
    or os.getenv("PIXELLE_API_BASE_URL")
    or "http://127.0.0.1:8000"
)


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

    def _build_api_client(self) -> httpx.Client:
        return httpx.Client(timeout=httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=60.0))

    def _poll_task(self, client: httpx.Client, task_id: str, progress_bar, status_text) -> dict:
        while True:
            response = client.get(f"{API_BASE_URL}/api/custom-script-assets/tasks/{task_id}")
            response.raise_for_status()
            task = response.json()
            status = task.get("status", "")
            progress = task.get("progress") or {}
            percentage = int(progress.get("percentage") or 0)
            message = progress.get("message") or status or tr("status.generating")

            if status == "completed":
                progress_bar.progress(100)
                status_text.text(tr("status.success"))
                return task

            if status == "failed":
                raise RuntimeError(task.get("error") or message)

            if status == "cancelled":
                raise RuntimeError(message or "Task cancelled")

            progress_bar.progress(max(0, min(percentage, 99)))
            status_text.text(message)
            time.sleep(2)

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

            if uploaded_files:
                st.success(tr("asset_based.assets.count", count=len(uploaded_files)))

                with st.expander(tr("asset_based.assets.preview"), expanded=True):
                    cols = st.columns(3)
                    for i, file in enumerate(uploaded_files):
                        with cols[i % 3]:
                            ext = Path(file.name).suffix.lower()
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

        return {
            "asset_files": uploaded_files or [],
            "script_text": script_text,
        }

    def _render_service_and_voice_config(self, pixelle_video: Any) -> dict:
        with st.container(border=True):
            st.markdown(f"**{tr('custom_script_assets.section.service')}**")

            with st.expander(tr("help.feature_description"), expanded=False):
                st.markdown(f"**{tr('help.what')}**")
                st.markdown(tr("custom_script_assets.service.what"))
                st.markdown(f"**{tr('help.how')}**")
                st.markdown(tr("custom_script_assets.service.how"))

            comfyui_config = config_manager.get_comfyui_config()
            has_runninghub = bool(comfyui_config.get("runninghub_api_key"))

            st.info(tr("custom_script_assets.service.runninghub_only"))
            if not has_runninghub:
                st.warning(tr("asset_based.source.runninghub_not_configured"))
            else:
                st.info(tr("asset_based.source.runninghub_hint"))

        workflow_path = "runninghub/tts_index2.json"

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
            "ref_audio_file": ref_audio_file,
        }

    def _render_output_preview(self, pixelle_video: Any, video_params: dict):
        with st.container(border=True):
            st.markdown(f"**{tr('section.video_generation')}**")

            if not config_manager.validate():
                st.warning(tr("settings.not_configured"))

            asset_files = video_params.get("asset_files", [])
            script_text = (video_params.get("script_text") or "").strip()
            ref_audio_file = video_params.get("ref_audio_file")

            if not asset_files:
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

            if not ref_audio_file:
                st.info(tr("custom_script_assets.output.no_ref_audio"))
                st.button(
                    tr("btn.generate"),
                    type="primary",
                    use_container_width=True,
                    disabled=True,
                    key="custom_script_assets_generate_disabled_ref_audio",
                )
                return

            st.info(tr("custom_script_assets.output.ready", count=len(asset_files)))

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
                    with self._build_api_client() as client:
                        status_text.text(tr("custom_script_assets.progress.submitting"))
                        progress_bar.progress(10)

                        multipart_files = []
                        for uploaded_file in asset_files:
                            multipart_files.append(
                                (
                                    "assets",
                                    (
                                        uploaded_file.name,
                                        uploaded_file.getvalue(),
                                        uploaded_file.type or "application/octet-stream",
                                    ),
                                )
                            )
                        multipart_files.append(
                            (
                                "ref_audio",
                                (
                                    ref_audio_file.name,
                                    ref_audio_file.getvalue(),
                                    ref_audio_file.type or "application/octet-stream",
                                ),
                            )
                        )
                        response = client.post(
                            f"{API_BASE_URL}/api/custom-script-assets/generate/async",
                            data={"script_text": script_text},
                            files=multipart_files,
                        )
                        response.raise_for_status()
                        task_id = response.json()["task_id"]

                        status_text.text(tr("custom_script_assets.progress.polling"))
                        progress_bar.progress(20)

                        task = self._poll_task(client, task_id, progress_bar, status_text)
                        result = task.get("result") or {}

                    total_time = time.time() - start_time

                    progress_bar.progress(100)
                    status_text.text(tr("status.success"))
                    video_url = result.get("video_url", "")
                    st.success(tr("status.video_generated", path=video_url or task_id))
                    st.markdown("---")

                    if video_url:
                        file_size_mb = (result.get("file_size", 0) or 0) / (1024 * 1024)
                        duration = result.get("duration", 0)

                        info_text = (
                            f"⏱️ {tr('info.generation_time')} {total_time:.1f}s   "
                            f"📦 {file_size_mb:.2f}MB   "
                            f"🎬 {duration:.1f}s"
                        )
                        st.caption(info_text)
                        st.markdown("---")
                        st.video(video_url)
                        if os.path.exists(video_url):
                            with open(video_url, "rb") as video_file:
                                video_bytes = video_file.read()
                            video_filename = Path(video_url).name or "asset_video.mp4"
                            st.download_button(
                                label="⬇️ 下载视频" if get_language() == "zh_CN" else "⬇️ Download Video",
                                data=video_bytes,
                                file_name=video_filename,
                                mime="video/mp4",
                                use_container_width=True,
                            )
                    else:
                        st.error(tr("status.video_not_found", path=task_id))

                except Exception as e:
                    status_text.text("")
                    progress_bar.empty()
                    st.error(tr("status.error", error=str(e)))
                    logger.exception(e)
                    st.stop()


register_pipeline_ui(CustomScriptAssetsPipelineUI)
