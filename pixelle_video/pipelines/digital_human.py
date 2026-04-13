"""
Digital Human Video Pipeline

Generates digital human oral broadcast videos from character images and text.
Extracted from web/pipelines/digital_human.py as a standalone, UI-independent service.

Two modes:
    - customize: Character image + custom text → TTS → Video
    - digital: Character image + goods image + title/text → ComfyUI workflow → TTS → Video

Example:
    pipeline = DigitalHumanPipeline(pixelle_video)
    result = await pipeline(
        character_assets=["/path/to/character.jpg"],
        mode="customize",
        goods_text="大家好，今天给大家推荐一款产品...",
        source="runninghub",
        tts_voice="zh-CN-YunjianNeural",
        tts_speed=1.2
    )
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional

import httpx
from loguru import logger

from pixelle_video.utils.os_util import create_task_output_dir


# Type alias for progress callback
ProgressCallback = Optional[Callable[[dict], None]]


@dataclass
class DigitalHumanResult:
    """Result of digital human video generation."""
    video_path: str = ""
    duration: float = 0.0
    file_size: int = 0
    task_id: str = ""
    task_dir: str = ""


class DigitalHumanPipeline:
    """
    Digital Human Video Pipeline (UI-independent).
    
    Generates digital human oral broadcast videos using ComfyUI workflows.
    """
    
    def __init__(self, core):
        """
        Initialize pipeline.
        
        Args:
            core: PixelleVideoCore instance
        """
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
        progress_callback: ProgressCallback = None,
        **kwargs
    ) -> DigitalHumanResult:
        """
        Execute the digital human video pipeline.
        
        Args:
            character_assets: List of character image paths (at least one)
            mode: "customize" (custom text) or "digital" (goods showcase)
            goods_assets: List of goods image paths (required for "digital" mode)
            goods_title: Goods title (used in "digital" mode when no text)
            goods_text: Narration text / goods description
            source: Workflow source ("runninghub" or "selfhost")
            tts_voice: TTS voice ID
            tts_speed: TTS speech speed
            tts_inference_mode: "local" or "comfyui"
            tts_workflow: Custom TTS workflow (for comfyui mode)
            ref_audio: Reference audio for voice cloning
            progress_callback: Optional progress callback
            **kwargs: Additional parameters
            
        Returns:
            DigitalHumanResult with video path, duration, file size
        """
        self._progress_callback = progress_callback
        
        # Validate inputs
        if not character_assets:
            raise ValueError("At least one character asset (image) is required")
        if mode == "digital" and not goods_assets:
            raise ValueError("goods_assets is required for 'digital' mode")
        if mode not in ("digital", "customize"):
            raise ValueError(f"Invalid mode: {mode}. Must be 'digital' or 'customize'")
        
        # Create task directory
        task_dir, task_id = create_task_output_dir()
        logger.info(f"[DigitalHuman] Task directory: {task_dir}, mode: {mode}")
        
        # Build workflow paths based on source
        workflow_paths = self._get_workflow_paths(source)
        
        # Get ComfyKit instance
        kit = await self.core._get_or_create_comfykit()
        
        try:
            if mode == "customize":
                video_path = await self._execute_customize(
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
                video_path = await self._execute_digital(
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
            
            # Get file info
            file_size = os.path.getsize(video_path) if os.path.exists(video_path) else 0
            
            return DigitalHumanResult(
                video_path=video_path,
                duration=0.0,  # Duration can be computed externally if needed
                file_size=file_size,
                task_id=task_id,
                task_dir=task_dir,
            )
            
        except Exception as e:
            logger.error(f"[DigitalHuman] Pipeline failed: {e}")
            raise
    
    def _get_workflow_paths(self, source: str) -> dict:
        """Get workflow file paths based on source."""
        import os
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        return {
            "first_workflow_path": os.path.join(base_dir, f"workflows/{source}/digital_image.json"),
            "second_workflow_path": os.path.join(base_dir, f"workflows/{source}/digital_combination.json"),
            "third_workflow_path": os.path.join(base_dir, f"workflows/{source}/digital_customize.json"),
        }
    
    def _emit_progress(self, event: dict):
        """Emit progress event if callback available."""
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
    ):
        """Generate TTS audio."""
        tts_kwargs = {
            "text": text,
            "output_path": audio_path,
            "inference_mode": tts_inference_mode,
        }
        if tts_inference_mode == "local":
            tts_kwargs["voice"] = tts_voice
            tts_kwargs["speed"] = tts_speed
        elif tts_inference_mode == "comfyui":
            if tts_workflow:
                tts_kwargs["workflow"] = tts_workflow
            elif ref_audio:
                # When ref_audio is provided, auto-select Index TTS (voice cloning)
                # This matches the 8501 Streamlit UI behavior
                tts_kwargs["workflow"] = f"{source}/tts_index2.json"
                logger.info(f"[DigitalHuman] Auto-selected Index TTS for voice cloning: {source}/tts_index2.json")
            if ref_audio:
                tts_kwargs["ref_audio"] = ref_audio
        
        await self.core.tts(**tts_kwargs)
        logger.info(f"[DigitalHuman] TTS generated: {audio_path}")
    
    async def _execute_workflow(self, kit, workflow_path_str: str, params: dict):
        """Execute a ComfyUI workflow and return the result."""
        workflow_path = Path(workflow_path_str)
        if not workflow_path.exists():
            raise FileNotFoundError(f"Workflow file does not exist: {workflow_path}")
        
        with open(workflow_path, 'r', encoding='utf-8') as f:
            workflow_config = json.load(f)
        
        if workflow_config.get("source") == "runninghub" and "workflow_id" in workflow_config:
            workflow_input = workflow_config["workflow_id"]
        else:
            workflow_input = str(workflow_config)
        
        result = await kit.execute(workflow_input, params)
        return result
    
    async def _extract_video_url(self, result) -> str:
        """Extract video URL from workflow result."""
        generated_video_url = None
        if hasattr(result, 'videos') and result.videos:
            generated_video_url = result.videos[0]
        elif hasattr(result, 'outputs') and result.outputs:
            for node_id, node_output in result.outputs.items():
                if isinstance(node_output, dict) and 'videos' in node_output:
                    videos = node_output['videos']
                    if videos and len(videos) > 0:
                        generated_video_url = videos[0]
                        break
        
        if not generated_video_url:
            raise RuntimeError(
                "The workflow did not return a video. Please check the workflow configuration."
            )
        
        return generated_video_url
    
    async def _download_video(self, video_url: str, output_path: str):
        """Download video from URL to local file."""
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
    ) -> str:
        """
        Execute 'customize' mode: character image + custom text → video.
        
        Steps:
            1. TTS: text → audio
            2. ComfyUI: character_image + audio → video
        """
        self._emit_progress({"step": "tts", "progress": 0.25})
        
        # Step 1: TTS
        audio_path = os.path.join(task_dir, "narration.mp3")
        await self._generate_tts(
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
        
        # Step 2: Digital human video synthesis
        second_result = await self._execute_workflow(
            kit,
            workflow_paths["second_workflow_path"],
            {"videoimage": character_image, "audio": audio_path},
        )
        
        video_url = await self._extract_video_url(second_result)
        
        final_video_path = os.path.join(task_dir, "final.mp4")
        await self._download_video(video_url, final_video_path)
        
        self._emit_progress({"step": "completed", "progress": 1.0})
        
        return final_video_path
    
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
    ) -> str:
        """
        Execute 'digital' mode: character + goods → video.
        
        Two sub-paths depending on whether goods_text is provided:
        
        A) With goods_text (user provides narration):
            1. Combine images (third workflow)
            2. TTS
            3. Video synthesis (second workflow)
        
        B) Without goods_text (AI generates narration):
            1. First workflow (generates combined image + narration text)
            2. TTS
            3. Video synthesis (second workflow)
        """
        second_workflow = workflow_paths["second_workflow_path"]
        
        if goods_text and goods_text.strip():
            # Path A: User-provided text
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
        else:
            # Path B: AI-generated text
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
        self, kit, task_dir, character_image, goods_image, text,
        workflow_paths, tts_voice, tts_speed, tts_inference_mode,
        tts_workflow, ref_audio, source="runninghub",
    ) -> str:
        """Digital mode Path A: user provides text."""
        self._emit_progress({"step": "combine_image", "progress": 0.10})
        
        # Step 1: Combine character + goods images
        third_workflow = workflow_paths["third_workflow_path"]
        combine_result = await self._execute_workflow(
            kit, third_workflow,
            {"firstimage": character_image, "secondimage": goods_image},
        )
        if combine_result.status != "completed":
            raise RuntimeError(f"Image combination workflow failed: {combine_result.msg}")
        
        generated_image_url = getattr(combine_result, "images", [None])[0]
        
        self._emit_progress({"step": "tts", "progress": 0.35})
        
        # Step 2: TTS
        audio_path = os.path.join(task_dir, "narration.mp3")
        await self._generate_tts(
            text=text, audio_path=audio_path,
            tts_voice=tts_voice, tts_speed=tts_speed,
            tts_inference_mode=tts_inference_mode,
            tts_workflow=tts_workflow, ref_audio=ref_audio,
            source=source,
        )
        
        self._emit_progress({"step": "video_synthesis", "progress": 0.65})
        
        # Step 3: Video synthesis
        second_result = await self._execute_workflow(
            kit, workflow_paths["second_workflow_path"],
            {"videoimage": generated_image_url, "audio": audio_path},
        )
        
        video_url = await self._extract_video_url(second_result)
        
        final_video_path = os.path.join(task_dir, "final.mp4")
        await self._download_video(video_url, final_video_path)
        
        self._emit_progress({"step": "completed", "progress": 1.0})
        
        return final_video_path
    
    async def _execute_digital_auto(
        self, kit, task_dir, character_image, goods_image, goods_title,
        workflow_paths, tts_voice, tts_speed, tts_inference_mode,
        tts_workflow, ref_audio, source="runninghub",
    ) -> str:
        """Digital mode Path B: AI generates text."""
        self._emit_progress({"step": "synthesis", "progress": 0.10})
        
        # Step 1: First workflow — generates combined image + narration text
        first_workflow = workflow_paths["first_workflow_path"]
        synthesis_result = await self._execute_workflow(
            kit, first_workflow,
            {"firstimage": character_image, "secondimage": goods_image, "goodstype": goods_title},
        )
        if synthesis_result.status != "completed":
            raise RuntimeError(f"Synthesis workflow failed: {synthesis_result.msg}")
        
        generated_image_url = getattr(synthesis_result, "images", [None])[0]
        generated_text = getattr(synthesis_result, "texts", [None])[0]
        
        self._emit_progress({"step": "tts", "progress": 0.35})
        
        # Step 2: TTS
        audio_path = os.path.join(task_dir, "narration.mp3")
        await self._generate_tts(
            text=generated_text, audio_path=audio_path,
            tts_voice=tts_voice, tts_speed=tts_speed,
            tts_inference_mode=tts_inference_mode,
            tts_workflow=tts_workflow, ref_audio=ref_audio,
            source=source,
        )
        
        self._emit_progress({"step": "video_synthesis", "progress": 0.65})
        
        # Step 3: Video synthesis
        second_result = await self._execute_workflow(
            kit, workflow_paths["second_workflow_path"],
            {"videoimage": generated_image_url, "audio": audio_path},
        )
        
        video_url = await self._extract_video_url(second_result)
        
        final_video_path = os.path.join(task_dir, "final.mp4")
        await self._download_video(video_url, final_video_path)
        
        self._emit_progress({"step": "completed", "progress": 1.0})
        
        return final_video_path
