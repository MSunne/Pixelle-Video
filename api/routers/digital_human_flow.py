"""
Guided step-by-step Digital Human Video Generation workflow router.
Provides a consolidated, linear Swagger experience.
"""

import os
from pathlib import Path
import uuid
import shutil
from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from api.dependencies import PixelleVideoDep
from api.schemas.digital_human_flow import (
    Step3GenerateRequest,
    Step3GenerateResponse,
)
from api.tasks import Task
from api.tasks import task_manager, TaskType

router = APIRouter()


def _path_to_url(request: Request, file_path: str) -> str:
    """Helper to convert server path to preview URL"""
    file_path = file_path.replace("\\", "/")
    is_absolute = os.path.isabs(file_path) or Path(file_path).is_absolute()

    if is_absolute:
        parts = file_path.split("/")
        try:
            output_idx = parts.index("output")
            file_path = "/".join(parts[output_idx + 1:])
        except ValueError:
            file_path = Path(file_path).name
    else:
        if file_path.startswith("output/"):
            file_path = file_path[7:]

    base_url = str(request.base_url).rstrip('/')
    return f"{base_url}/api/files/{file_path}"


def _upload_to_s3_if_available(local_path: str, fallback_url: str) -> str:
    """Upload to S3 if configured"""
    try:
        from pixelle_video.storage import get_s3_storage
        s3 = get_s3_storage()
        if s3.is_available() and os.path.exists(local_path):
            return s3.upload_video(local_path, cleanup_local=True)
    except Exception as e:
        logger.warning(f"[DigitalHumanFlow] S3 upload failed: {e}")
    return fallback_url



@router.post("/step3-generate-video", response_model=Step3GenerateResponse, tags=["数字人产品口播"], summary="开始生成数字人口播视频")
async def step3_generate_video(
    request_body: Step3GenerateRequest,
    pixelle_video: PixelleVideoDep
):
    """
    提交所有信息（形象图片、商品图片、文本以及TTS参数），异步开始生成最终视频。
    如果提供了 pre_generated_audio_path，可以跳过配音合成步骤。
    
    返回 task_id，用于在 Step 4 轮询状态。
    """
    try:
        # Re-pack into standard DigitalHuman pipeline request format
        from api.schemas.digital_human import DigitalHumanVideoRequest
        standard_req = DigitalHumanVideoRequest(
            character_assets=[request_body.character_asset_path],
            mode=request_body.mode,
            goods_assets=[request_body.goods_asset_path] if request_body.goods_asset_path else None,
            goods_title=request_body.goods_title,
            goods_text=request_body.goods_text,
            source=request_body.source,
            tts_inference_mode="comfyui",
            ref_audio=request_body.ref_audio,
        )
        
        # Create Task
        task = task_manager.create_task(
            task_type=TaskType.DIGITAL_HUMAN_VIDEO,
            request_params=standard_req.model_dump()
        )
        
        async def execute_digital_human_flow():
            from pixelle_video.pipelines.digital_human import DigitalHumanPipeline
            pipeline = DigitalHumanPipeline(pixelle_video)
            
            def on_progress(data: dict):
                current_task = task_manager.get_task(task.task_id)
                if current_task:
                    step = data.get("step", "")
                    progress_val = data.get("progress", 0.0)
                    
                    step_message_map = {
                        "combine_image": "正在合成人与商品关键图...",
                        "synthesis": "正在生成解说文案与关键图...",
                        "tts": "正在合成语音配音...",
                        "video_synthesis": "正在合成对口型视频并渲染...",
                        "completed": "视频生成已完成！"
                    }
                    message = step_message_map.get(step, "正在处理中...")
                    
                    from api.tasks.models import TaskProgress
                    current_task.progress = TaskProgress(
                        percentage=progress_val * 100,
                        message=message
                    )

            # Using the core pipeline, which expects assets as lists
            result = await pipeline(
                character_assets=standard_req.character_assets,
                mode=standard_req.mode,
                goods_assets=standard_req.goods_assets,
                goods_title=standard_req.goods_title,
                goods_text=standard_req.goods_text,
                source=standard_req.source,
                tts_inference_mode=standard_req.tts_inference_mode,
                ref_audio=standard_req.ref_audio,
                progress_callback=on_progress,
            )
            
            # Use a dummy request to build URL since we don't have the real request context in background task easily.
            # We'll just build a relative URL and then fix it, or upload to S3 directly.
            # Easiest way is to just assume default host or S3 will handle it.
            fallback_url = f"/api/files/{Path(result.video_path).name}"
            video_url = _upload_to_s3_if_available(result.video_path, fallback_url)
            
            # Clean up local task directory only after successful S3 upload
            # (if video_url == fallback_url, S3 was not available — keep local files)
            if video_url != fallback_url and result.task_dir:
                from pixelle_video.utils.os_util import cleanup_task_dir
                cleanup_task_dir(result.task_dir)
            
            return {
                "video_url": video_url,
                "duration": result.duration,
                "file_size": result.file_size
            }
            
        # Execute Background Task
        await task_manager.execute_task(
            task_id=task.task_id,
            coro_func=execute_digital_human_flow
        )
        
        return Step3GenerateResponse(task_id=task.task_id)
        
    except Exception as e:
        logger.error(f"[Flow Gen] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/step4-check-status/{task_id}", response_model=Task, tags=["任务进度查询"], summary="轮询任务执行进度与结果")
async def step4_check_status(task_id: str):
    """
    检查第三步生成的数字人视频进度。
    当 status 变为 "completed" 时，result.video_url 将包含生成的视频下载地址。
    """
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task
