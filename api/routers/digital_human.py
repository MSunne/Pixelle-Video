"""
Digital Human Video Generation endpoints

Generates digital human oral broadcast videos from character images
and narration text using ComfyUI workflows.

Supports both synchronous and asynchronous generation.
Results are uploaded to S3 when available, with local cleanup to save disk space.
"""

import os
from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from api.dependencies import PixelleVideoDep
from api.schemas.digital_human import (
    DigitalHumanVideoRequest,
    DigitalHumanVideoResponse,
    DigitalHumanVideoAsyncResponse,
)
from api.tasks import task_manager, TaskType


router = APIRouter(prefix="/digital-human", tags=["数字人视频"])


def path_to_url(request: Request, file_path: str) -> str:
    """Convert file path to accessible URL."""
    from pathlib import Path

    file_path = file_path.replace("\\", "/")
    is_absolute = os.path.isabs(file_path) or Path(file_path).is_absolute()

    if is_absolute:
        parts = file_path.split("/")
        try:
            output_idx = parts.index("output")
            relative_parts = parts[output_idx + 1:]
            file_path = "/".join(relative_parts)
        except ValueError:
            file_path = Path(file_path).name
    else:
        if file_path.startswith("output/"):
            file_path = file_path[7:]

    base_url = str(request.base_url).rstrip('/')
    return f"{base_url}/api/files/{file_path}"


def _upload_to_s3_if_available(local_path: str, fallback_url: str) -> str:
    """
    Upload file to S3 if available, return S3 public URL.
    Falls back to local API URL if S3 is not configured.
    After successful S3 upload, local file is deleted to save disk space.
    """
    try:
        from pixelle_video.storage import get_s3_storage
        s3 = get_s3_storage()
        if s3.is_available() and os.path.exists(local_path):
            return s3.upload_video(local_path, cleanup_local=True)
    except Exception as e:
        logger.warning(f"[DigitalHuman] S3 upload failed, using local URL: {e}")
    return fallback_url


@router.post("/generate/sync", response_model=DigitalHumanVideoResponse)
async def generate_digital_human_sync(
    request_body: DigitalHumanVideoRequest,
    pixelle_video: PixelleVideoDep,
    request: Request
):
    """
    同步生成数字人视频
    
    **支持模式：**
    - `customize` (口播模式): 人物图片 + 自定义文案 → 数字人朗读口播视频
    - `digital` (带货模式): 人物图片 + 商品图片 → AI 自动生成带货介绍视频
    
    **注意**：生成可能需要几分钟时间。推荐使用 `/generate/async` 接口来进行长时间运行的异步生成。
    
    生成结果会自动上传至 S3（如果已配置），并清理本地临时文件。
    """
    try:
        logger.info(f"[DigitalHuman] Sync generation: mode={request_body.mode}, "
                     f"characters={len(request_body.character_assets)}")
        
        # Import pipeline
        from pixelle_video.pipelines.digital_human import DigitalHumanPipeline
        
        # Create and execute pipeline
        pipeline = DigitalHumanPipeline(pixelle_video)
        
        result = await pipeline(
            character_assets=request_body.character_assets,
            mode=request_body.mode,
            goods_assets=request_body.goods_assets,
            goods_title=request_body.goods_title,
            goods_text=request_body.goods_text,
            source=request_body.source,
            tts_voice=request_body.tts_voice,
            tts_speed=request_body.tts_speed,
            tts_inference_mode=request_body.tts_inference_mode,
            tts_workflow=request_body.tts_workflow,
            ref_audio=request_body.ref_audio,
        )
        
        # Get file info before potential S3 upload
        file_size = result.file_size
        
        # Upload to S3 or use local URL
        local_url = path_to_url(request, result.video_path)
        video_url = _upload_to_s3_if_available(result.video_path, local_url)
        
        # Clean up local task directory only after successful S3 upload
        # (if video_url == local_url, S3 was not available — keep local files)
        if video_url != local_url and result.task_dir:
            from pixelle_video.utils.os_util import cleanup_task_dir
            cleanup_task_dir(result.task_dir)
        
        return DigitalHumanVideoResponse(
            video_url=video_url,
            duration=result.duration,
            file_size=file_size
        )
        
    except ValueError as e:
        logger.error(f"[DigitalHuman] Validation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[DigitalHuman] Sync generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate/async", response_model=DigitalHumanVideoAsyncResponse)
async def generate_digital_human_async(
    request_body: DigitalHumanVideoRequest,
    pixelle_video: PixelleVideoDep,
    request: Request
):
    """
    异步生成数字人视频
    
    创建一个后台任务用于数字人视频生成。
    立即返回 `task_id`，用于后续跟踪任务进度。
    
    **工作流：**
    1. 通过 `/api/files` 接口上传人物/商品图片等素材
    2. 传入图片路径和文案等参数调用此接口
    3. 在返回结果中获得 `task_id`
    4. 轮询调用 `/api/tasks/{task_id}` 来检查任务状态
    5. 当状态变为 "completed" 时，从结果中获取视频 URL
    
    **支持模式：**
    - `customize` (口播模式): 人物图片 + 自定义文案 → 数字人朗读口播视频
    - `digital` (带货模式): 人物图片 + 商品图片 → AI 自动生成带货介绍视频
    """
    try:
        logger.info(f"[DigitalHuman] Async generation: mode={request_body.mode}, "
                     f"characters={len(request_body.character_assets)}")
        
        # Validate request before creating task
        if request_body.mode == "digital" and not request_body.goods_assets:
            raise HTTPException(
                status_code=400,
                detail="goods_assets is required for 'digital' mode"
            )
        
        # Create task
        task = task_manager.create_task(
            task_type=TaskType.DIGITAL_HUMAN_VIDEO,
            request_params=request_body.model_dump()
        )
        
        # Define async execution function
        async def execute_digital_human():
            from pixelle_video.pipelines.digital_human import DigitalHumanPipeline
            
            pipeline = DigitalHumanPipeline(pixelle_video)
            
            result = await pipeline(
                character_assets=request_body.character_assets,
                mode=request_body.mode,
                goods_assets=request_body.goods_assets,
                goods_title=request_body.goods_title,
                goods_text=request_body.goods_text,
                source=request_body.source,
                tts_voice=request_body.tts_voice,
                tts_speed=request_body.tts_speed,
                tts_inference_mode=request_body.tts_inference_mode,
                tts_workflow=request_body.tts_workflow,
                ref_audio=request_body.ref_audio,
            )
            
            file_size = result.file_size
            local_url = path_to_url(request, result.video_path)
            video_url = _upload_to_s3_if_available(result.video_path, local_url)
            
            # Clean up local task directory only after successful S3 upload
            if video_url != local_url and result.task_dir:
                from pixelle_video.utils.os_util import cleanup_task_dir
                cleanup_task_dir(result.task_dir)
            
            return {
                "video_url": video_url,
                "duration": result.duration,
                "file_size": file_size
            }
        
        # Start execution
        await task_manager.execute_task(
            task_id=task.task_id,
            coro_func=execute_digital_human
        )
        
        return DigitalHumanVideoAsyncResponse(
            task_id=task.task_id
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DigitalHuman] Async generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
