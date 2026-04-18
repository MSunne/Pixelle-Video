"""
Custom script + assets endpoints

Dedicated simplified API for:
- reference assets
- reference voice
- video script

All requests are fixed to RunningHub + reference voice cloning.
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from loguru import logger

from api.config import api_config
from api.dependencies import PixelleVideoDep
from api.schemas.asset_based import AssetBasedVideoAsyncResponse
from api.tasks import Task, TaskType, task_manager

router = APIRouter(prefix="/custom-script-assets", tags=["自定义话术和素材"])


def _save_upload(upload: UploadFile, target_dir: Path) -> str:
    """Persist one uploaded file to a temp directory."""
    original_name = Path(upload.filename or "upload.bin").name
    file_bytes = upload.file.read()
    file_size = len(file_bytes)
    if file_size > api_config.max_upload_size:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File '{original_name}' exceeds max upload size "
                f"({api_config.max_upload_size} bytes)"
            ),
        )

    saved_name = f"{uuid.uuid4().hex[:8]}_{original_name}"
    file_path = target_dir / saved_name
    file_path.write_bytes(file_bytes)
    return str(file_path.resolve())


@router.post("/generate/async", response_model=AssetBasedVideoAsyncResponse)
async def generate_custom_script_assets_async(
    pixelle_video: PixelleVideoDep,
    assets: list[UploadFile] = File(..., description="参考素材，可上传多张图片或多个视频"),
    ref_audio: UploadFile = File(..., description="参考语音"),
    script_text: str = Form(..., description="视频文案"),
):
    """
    Simplified async generation endpoint with exactly 3 business inputs:
    1. `assets`
    2. `ref_audio`
    3. `script_text`

    Internal defaults:
    - content_mode = script
    - script_split_mode = paragraph
    - source = runninghub
    - tts_inference_mode = comfyui
    - tts_workflow = runninghub/tts_index2.json
    """
    try:
        if not assets:
            raise HTTPException(status_code=400, detail="assets is required")
        if not script_text or not script_text.strip():
            raise HTTPException(status_code=400, detail="script_text is required")
        if not ref_audio.filename:
            raise HTTPException(status_code=400, detail="ref_audio is required")

        upload_dir = Path.cwd() / "temp" / "uploads" / "custom_script_assets" / uuid.uuid4().hex[:12]
        upload_dir.mkdir(parents=True, exist_ok=True)

        asset_paths = [_save_upload(upload, upload_dir) for upload in assets]
        ref_audio_path = _save_upload(ref_audio, upload_dir)

        task = task_manager.create_task(
            task_type=TaskType.ASSET_BASED_VIDEO,
            request_params={
                "entrypoint": "custom_script_assets",
                "assets": asset_paths,
                "ref_audio": ref_audio_path,
                "script_text": script_text,
            },
        )

        async def execute_custom_script_assets():
            from pixelle_video.pipelines.asset_based import AssetBasedPipeline

            pipeline = AssetBasedPipeline(pixelle_video)

            def on_progress(event):
                task_manager.update_progress(
                    task.task_id,
                    current=int(event.progress * 100),
                    total=100,
                    message=getattr(event, "extra_info", "") or event.event_type,
                )

            ctx = await pipeline(
                assets=asset_paths,
                content_mode="script",
                script_text=script_text,
                script_split_mode="paragraph",
                source="runninghub",
                tts_inference_mode="comfyui",
                tts_workflow="runninghub/tts_index2.json",
                ref_audio=ref_audio_path,
                progress_callback=on_progress,
            )

            file_size = Path(ctx.final_video_path).stat().st_size if Path(ctx.final_video_path).exists() else 0
            duration = sum(
                frame.duration
                for frame in ctx.storyboard.frames
                if hasattr(frame, "duration") and frame.duration
            )

            return {
                "video_url": ctx.final_video_path,
                "duration": duration,
                "file_size": file_size,
            }

        await task_manager.execute_task(
            task_id=task.task_id,
            coro_func=execute_custom_script_assets,
        )

        return AssetBasedVideoAsyncResponse(task_id=task.task_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CustomScriptAssets] Async generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks/{task_id}", response_model=Task)
async def get_custom_script_assets_task(task_id: str):
    """Query async task progress for the simplified custom-script-assets flow."""
    task = task_manager.get_task(task_id)
    request_params = task.request_params or {} if task else {}
    if (
        not task
        or task.task_type != TaskType.ASSET_BASED_VIDEO
        or request_params.get("entrypoint") != "custom_script_assets"
    ):
        raise HTTPException(status_code=404, detail=f"Custom script asset task {task_id} not found")
    return task
