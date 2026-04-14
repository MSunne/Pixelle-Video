"""
Digital Human Video Pipeline

Generates digital human oral broadcast videos from character images and text.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

import httpx
from loguru import logger

from pixelle_video.services.tts_service import TTSResult
from pixelle_video.utils.os_util import create_task_output_dir

ProgressCallback = Optional[Callable[[dict], None]]


@dataclass
class DigitalHumanResult:
    """Result of digital human video generation."""

    video_path: str = ""
    raw_video_path: str = ""
    duration: float = 0.0
    file_size: int = 0
    task_id: str = ""
    task_dir: str = ""
    subtitle_path: Optional[str] = None
    subtitle_ass_path: Optional[str] = None
    subtitle_manifest_path: Optional[str] = None
    subtitle_enabled: bool = False
    subtitle_format: Optional[str] = None


class DigitalHumanPipeline:
    """Digital Human Video Pipeline (UI-independent)."""

    def __init__(self, core):
        self.core = core

    async def __call__(
        self,
        character_assets: List[str],
        mode: str = "customize",
        goods_assets: Optional[List[str]] = None,
        goods_title: Optional[str] = None,
        goods_text: str = "",
        source: str = "runninghub",
        tts_voice: str = "zh-CN-YunjianNeural",
        tts_speed: float = 1.2,
        tts_inference_mode: str = "local",
        tts_workflow: Optional[str] = None,
        ref_audio: Optional[str] = None,
        llm_model: Optional[str] = None,
        subtitle_enabled: bool = True,
        subtitle_output: str = "both",
        subtitle_language: str = "zh_en",
        progress_callback: ProgressCallback = None,
        **kwargs,
    ) -> DigitalHumanResult:
        self._progress_callback = progress_callback

        if not character_assets:
            raise ValueError("At least one character asset (image) is required")
        if mode == "digital" and not goods_assets:
            raise ValueError("goods_assets is required for 'digital' mode")
        if mode not in ("digital", "customize"):
            raise ValueError(f"Invalid mode: {mode}. Must be 'digital' or 'customize'")
        if subtitle_output not in {"burned", "sidecar", "both"}:
            raise ValueError("subtitle_output must be one of: burned, sidecar, both")
        if subtitle_language not in {"zh", "zh_en", "source"}:
            raise ValueError("subtitle_language must be one of: zh, zh_en, source")

        task_dir, task_id = create_task_output_dir()
        logger.info(f"[DigitalHuman] Task directory: {task_dir}, mode: {mode}")

        workflow_paths = self._get_workflow_paths(source)
        kit = await self.core._get_or_create_comfykit()

        try:
            if mode == "customize":
                raw_video_path, narration_text, tts_result = await self._execute_customize(
                    kit=kit,
                    task_dir=task_dir,
                    character_image=character_assets[0],
                    text=goods_text,
                    workflow_paths=workflow_paths,
                    tts_voice=tts_voice,
                    tts_speed=tts_speed,
                    tts_inference_mode=tts_inference_mode,
                    tts_workflow=tts_workflow,
                    ref_audio=ref_audio,
                    source=source,
                )
            else:
                raw_video_path, narration_text, tts_result = await self._execute_digital(
                    kit=kit,
                    task_dir=task_dir,
                    character_image=character_assets[0],
                    goods_image=goods_assets[0],
                    goods_title=goods_title or "",
                    goods_text=goods_text,
                    workflow_paths=workflow_paths,
                    tts_voice=tts_voice,
                    tts_speed=tts_speed,
                    tts_inference_mode=tts_inference_mode,
                    tts_workflow=tts_workflow,
                    ref_audio=ref_audio,
                    source=source,
                )

            final_video_path = os.path.join(task_dir, "final.mp4")
            subtitle_path = None
            subtitle_ass_path = None
            subtitle_manifest_path = None
            final_output_path = raw_video_path

            if subtitle_enabled:
                self._emit_progress({"step": "subtitle_translation", "progress": 0.76})
                self._emit_progress({"step": "subtitle_alignment", "progress": 0.84})
                subtitle_assets = await self.core.subtitle.generate_subtitle_assets(
                    text=narration_text,
                    audio_path=tts_result.audio_path,
                    video_path=raw_video_path,
                    output_dir=task_dir,
                    subtitle_language=subtitle_language,
                    llm_model=llm_model,
                    tts_boundaries=tts_result.boundaries or None,
                )
                subtitle_path = subtitle_assets.srt_path
                subtitle_ass_path = subtitle_assets.ass_path
                subtitle_manifest_path = subtitle_assets.json_path

                if subtitle_output in {"burned", "both"}:
                    self._emit_progress({"step": "subtitle_burn", "progress": 0.92})
                    self.core.video.burn_ass_subtitles(
                        video=raw_video_path,
                        ass_path=subtitle_ass_path,
                        output=final_video_path,
                        fontsdir=self.core.subtitle.get_fontsdir(),
                    )
                    final_output_path = final_video_path
                else:
                    shutil.copyfile(raw_video_path, final_video_path)
                    final_output_path = final_video_path
            else:
                shutil.copyfile(raw_video_path, final_video_path)
                final_output_path = final_video_path

            duration = self.core.video._get_video_duration(final_output_path)
            file_size = os.path.getsize(final_output_path) if os.path.exists(final_output_path) else 0

            self._emit_progress({"step": "completed", "progress": 1.0})

            return DigitalHumanResult(
                video_path=final_output_path,
                raw_video_path=raw_video_path,
                duration=duration,
                file_size=file_size,
                task_id=task_id,
                task_dir=task_dir,
                subtitle_path=subtitle_path,
                subtitle_ass_path=subtitle_ass_path,
                subtitle_manifest_path=subtitle_manifest_path,
                subtitle_enabled=subtitle_enabled,
                subtitle_format="srt" if subtitle_path else None,
            )

        except Exception as e:
            logger.error(f"[DigitalHuman] Pipeline failed: {e}")
            raise

    def _get_workflow_paths(self, source: str) -> dict:
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        return {
            "first_workflow_path": os.path.join(base_dir, f"workflows/{source}/digital_image.json"),
            "second_workflow_path": os.path.join(base_dir, f"workflows/{source}/digital_combination.json"),
            "third_workflow_path": os.path.join(base_dir, f"workflows/{source}/digital_customize.json"),
        }

    def _emit_progress(self, event: dict):
        if self._progress_callback:
            self._progress_callback(event)

    async def _generate_tts(
        self,
        text: str,
        audio_path: str,
        tts_voice: str,
        tts_speed: float,
        tts_inference_mode: str,
        tts_workflow: Optional[str],
        ref_audio: Optional[str],
        source: str = "runninghub",
    ) -> TTSResult:
        tts_kwargs = {
            "text": text,
            "output_path": audio_path,
            "inference_mode": tts_inference_mode,
            "return_result": True,
        }
        if tts_inference_mode == "local":
            tts_kwargs["voice"] = tts_voice
            tts_kwargs["speed"] = tts_speed
        elif tts_inference_mode == "comfyui":
            if tts_workflow:
                tts_kwargs["workflow"] = tts_workflow
            elif ref_audio:
                tts_kwargs["workflow"] = f"{source}/tts_index2.json"
                logger.info(
                    f"[DigitalHuman] Auto-selected Index TTS for voice cloning: {source}/tts_index2.json"
                )
            if ref_audio:
                tts_kwargs["ref_audio"] = ref_audio

        result = await self.core.tts(**tts_kwargs)
        logger.info(f"[DigitalHuman] TTS generated: {audio_path}")
        return result

    async def _execute_workflow(self, kit, workflow_path_str: str, params: dict):
        workflow_path = Path(workflow_path_str)
        if not workflow_path.exists():
            raise FileNotFoundError(f"Workflow file does not exist: {workflow_path}")

        with open(workflow_path, 'r', encoding='utf-8') as f:
            workflow_config = json.load(f)

        if workflow_config.get("source") == "runninghub" and "workflow_id" in workflow_config:
            workflow_input = workflow_config["workflow_id"]
        else:
            workflow_input = str(workflow_config)

        return await kit.execute(workflow_input, params)

    async def _extract_video_url(self, result) -> str:
        generated_video_url = None
        if hasattr(result, 'videos') and result.videos:
            generated_video_url = result.videos[0]
        elif hasattr(result, 'outputs') and result.outputs:
            for _, node_output in result.outputs.items():
                if isinstance(node_output, dict) and 'videos' in node_output:
                    videos = node_output['videos']
                    if videos:
                        generated_video_url = videos[0]
                        break

        if not generated_video_url:
            raise RuntimeError(
                "The workflow did not return a video. Please check the workflow configuration."
            )
        return generated_video_url

    async def _download_video(self, video_url: str, output_path: str):
        timeout = httpx.Timeout(300.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(video_url)
            response.raise_for_status()
            with open(output_path, 'wb') as f:
                f.write(response.content)
        logger.info(f"[DigitalHuman] Video downloaded: {output_path}")

    async def _execute_customize(
        self,
        kit,
        task_dir: str,
        character_image: str,
        text: str,
        workflow_paths: dict,
        tts_voice: str,
        tts_speed: float,
        tts_inference_mode: str,
        tts_workflow: Optional[str],
        ref_audio: Optional[str],
        source: str = "runninghub",
    ) -> tuple[str, str, TTSResult]:
        self._emit_progress({"step": "tts", "progress": 0.25})

        audio_path = os.path.join(task_dir, "narration.mp3")
        tts_result = await self._generate_tts(
            text=text,
            audio_path=audio_path,
            tts_voice=tts_voice,
            tts_speed=tts_speed,
            tts_inference_mode=tts_inference_mode,
            tts_workflow=tts_workflow,
            ref_audio=ref_audio,
            source=source,
        )

        self._emit_progress({"step": "video_synthesis", "progress": 0.65})
        second_result = await self._execute_workflow(
            kit,
            workflow_paths["second_workflow_path"],
            {"videoimage": character_image, "audio": audio_path},
        )

        video_url = await self._extract_video_url(second_result)
        raw_video_path = os.path.join(task_dir, "raw.mp4")
        await self._download_video(video_url, raw_video_path)

        return raw_video_path, text, tts_result

    async def _execute_digital(
        self,
        kit,
        task_dir: str,
        character_image: str,
        goods_image: str,
        goods_title: str,
        goods_text: str,
        workflow_paths: dict,
        tts_voice: str,
        tts_speed: float,
        tts_inference_mode: str,
        tts_workflow: Optional[str],
        ref_audio: Optional[str],
        source: str = "runninghub",
    ) -> tuple[str, str, TTSResult]:
        if goods_text and goods_text.strip():
            return await self._execute_digital_with_text(
                kit=kit,
                task_dir=task_dir,
                character_image=character_image,
                goods_image=goods_image,
                text=goods_text,
                workflow_paths=workflow_paths,
                tts_voice=tts_voice,
                tts_speed=tts_speed,
                tts_inference_mode=tts_inference_mode,
                tts_workflow=tts_workflow,
                ref_audio=ref_audio,
                source=source,
            )
        return await self._execute_digital_auto(
            kit=kit,
            task_dir=task_dir,
            character_image=character_image,
            goods_image=goods_image,
            goods_title=goods_title,
            workflow_paths=workflow_paths,
            tts_voice=tts_voice,
            tts_speed=tts_speed,
            tts_inference_mode=tts_inference_mode,
            tts_workflow=tts_workflow,
            ref_audio=ref_audio,
            source=source,
        )

    async def _execute_digital_with_text(
        self,
        kit,
        task_dir,
        character_image,
        goods_image,
        text,
        workflow_paths,
        tts_voice,
        tts_speed,
        tts_inference_mode,
        tts_workflow,
        ref_audio,
        source="runninghub",
    ) -> tuple[str, str, TTSResult]:
        self._emit_progress({"step": "combine_image", "progress": 0.10})

        combine_result = await self._execute_workflow(
            kit,
            workflow_paths["third_workflow_path"],
            {"firstimage": character_image, "secondimage": goods_image},
        )
        if combine_result.status != "completed":
            raise RuntimeError(f"Image combination workflow failed: {combine_result.msg}")

        generated_image_url = getattr(combine_result, "images", [None])[0]

        self._emit_progress({"step": "tts", "progress": 0.35})
        audio_path = os.path.join(task_dir, "narration.mp3")
        tts_result = await self._generate_tts(
            text=text,
            audio_path=audio_path,
            tts_voice=tts_voice,
            tts_speed=tts_speed,
            tts_inference_mode=tts_inference_mode,
            tts_workflow=tts_workflow,
            ref_audio=ref_audio,
            source=source,
        )

        self._emit_progress({"step": "video_synthesis", "progress": 0.65})
        second_result = await self._execute_workflow(
            kit,
            workflow_paths["second_workflow_path"],
            {"videoimage": generated_image_url, "audio": audio_path},
        )

        video_url = await self._extract_video_url(second_result)
        raw_video_path = os.path.join(task_dir, "raw.mp4")
        await self._download_video(video_url, raw_video_path)

        return raw_video_path, text, tts_result

    async def _execute_digital_auto(
        self,
        kit,
        task_dir,
        character_image,
        goods_image,
        goods_title,
        workflow_paths,
        tts_voice,
        tts_speed,
        tts_inference_mode,
        tts_workflow,
        ref_audio,
        source="runninghub",
    ) -> tuple[str, str, TTSResult]:
        self._emit_progress({"step": "synthesis", "progress": 0.10})

        synthesis_result = await self._execute_workflow(
            kit,
            workflow_paths["first_workflow_path"],
            {"firstimage": character_image, "secondimage": goods_image, "goodstype": goods_title},
        )
        if synthesis_result.status != "completed":
            raise RuntimeError(f"Synthesis workflow failed: {synthesis_result.msg}")

        generated_image_url = getattr(synthesis_result, "images", [None])[0]
        generated_text = getattr(synthesis_result, "texts", [None])[0]

        self._emit_progress({"step": "tts", "progress": 0.35})
        audio_path = os.path.join(task_dir, "narration.mp3")
        tts_result = await self._generate_tts(
            text=generated_text,
            audio_path=audio_path,
            tts_voice=tts_voice,
            tts_speed=tts_speed,
            tts_inference_mode=tts_inference_mode,
            tts_workflow=tts_workflow,
            ref_audio=ref_audio,
            source=source,
        )

        self._emit_progress({"step": "video_synthesis", "progress": 0.65})
        second_result = await self._execute_workflow(
            kit,
            workflow_paths["second_workflow_path"],
            {"videoimage": generated_image_url, "audio": audio_path},
        )

        video_url = await self._extract_video_url(second_result)
        raw_video_path = os.path.join(task_dir, "raw.mp4")
        await self._download_video(video_url, raw_video_path)

        return raw_video_path, generated_text, tts_result
