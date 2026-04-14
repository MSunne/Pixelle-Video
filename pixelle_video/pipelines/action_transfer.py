"""
Action Transfer Pipeline

Generates videos by transferring motion from a reference video to a target image.
Extracted from web/pipelines/action_transfer.py as a standalone, UI-independent service.

Example:
    pipeline = ActionTransferPipeline(pixelle_video)
    result = await pipeline(
        video_path="/path/to/reference.mp4",
        image_path="/path/to/target.jpg",
        prompt="A girl dancing",
        duration=10,
        workflow_key="runninghub/af_xxx.json",
    )
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import httpx
from loguru import logger

from pixelle_video.utils.os_util import create_task_output_dir


# Type alias for progress callback
ProgressCallback = Optional[Callable[[dict], None]]


@dataclass
class ActionTransferResult:
    """Result of action transfer video generation."""
    video_path: str = ""
    file_size: int = 0
    task_id: str = ""
    task_dir: str = ""


class ActionTransferPipeline:
    """
    Action Transfer Pipeline (UI-independent).
    
    Transfers motion from a reference video to a target image
    using ComfyUI workflows.
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
        video_path: str,
        image_path: str,
        prompt: str,
        duration: int,
        workflow_key: str,
        progress_callback: ProgressCallback = None,
    ) -> ActionTransferResult:
        """
        Execute the action transfer pipeline.
        
        Args:
            video_path: Reference video path (the motion source)
            image_path: Target image path (the appearance source)
            prompt: Text prompt for generation
            duration: Target video duration in seconds
            workflow_key: Workflow key (e.g. "runninghub/af_xxx.json")
            progress_callback: Optional progress callback
            
        Returns:
            ActionTransferResult with video path, file size
        """
        self._progress_callback = progress_callback
        
        # Validate inputs
        if not video_path:
            raise ValueError("Reference video path is required")
        if not image_path:
            raise ValueError("Target image path is required")
        if not prompt:
            raise ValueError("Prompt text is required")
        if not workflow_key:
            raise ValueError("Workflow key is required")
        
        # Create task directory
        task_dir, task_id = create_task_output_dir()
        logger.info(f"[ActionTransfer] Task directory: {task_dir}, duration: {duration}s")
        
        # Get ComfyKit instance
        kit = await self.core._get_or_create_comfykit()
        
        try:
            self._emit_progress({"step": "generation", "progress": 0.10})
            
            # Load workflow
            wf_path = Path("workflows") / workflow_key
            if not wf_path.exists():
                raise FileNotFoundError(f"Workflow file does not exist: {wf_path}")
            
            with open(wf_path, 'r', encoding='utf-8') as f:
                workflow_config = json.load(f)
            
            workflow_params = {
                "video": video_path,
                "image": image_path,
                "prompt": prompt,
                "second": duration,
            }
            
            if workflow_config.get("source") == "runninghub" and "workflow_id" in workflow_config:
                workflow_input = workflow_config["workflow_id"]
            else:
                workflow_input = str(wf_path)
            
            self._emit_progress({"step": "video_synthesis", "progress": 0.30})
            
            # Execute workflow
            video_result = await kit.execute(workflow_input, workflow_params)
            
            # Extract video URL from result
            generated_video_url = self._extract_video_url(video_result)
            
            self._emit_progress({"step": "downloading", "progress": 0.80})
            
            # Download video
            final_video_path = os.path.join(task_dir, "final.mp4")
            await self._download_video(generated_video_url, final_video_path)
            
            file_size = os.path.getsize(final_video_path) if os.path.exists(final_video_path) else 0
            
            self._emit_progress({"step": "completed", "progress": 1.0})
            
            return ActionTransferResult(
                video_path=final_video_path,
                file_size=file_size,
                task_id=task_id,
                task_dir=task_dir,
            )
            
        except Exception as e:
            logger.error(f"[ActionTransfer] Pipeline failed: {e}")
            raise
    
    def _emit_progress(self, event: dict):
        """Emit progress event if callback available."""
        if self._progress_callback:
            self._progress_callback(event)
    
    def _extract_video_url(self, result) -> str:
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
        logger.info(f"[ActionTransfer] Video downloaded: {output_path}")
