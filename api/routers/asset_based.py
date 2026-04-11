"""
Asset-Based Video Generation endpoints

Generates videos from user-provided assets (images/videos) with AI-generated
narrations and scene arrangement.

Supports both synchronous and asynchronous generation.
Results are uploaded to S3 when available, with local cleanup to save disk space.
"""

import os
from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from api.dependencies import PixelleVideoDep
from api.schemas.asset_based import (
    AssetBasedVideoRequest,
    AssetBasedVideoResponse,
    AssetBasedVideoAsyncResponse,
)
from api.tasks import task_manager, TaskType


router = APIRouter(prefix="/asset-video", tags=["自定义素材视频生成"])


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
        logger.warning(f"[AssetVideo] S3 upload failed, using local URL: {e}")
    return fallback_url


@router.post("/generate/sync", response_model=AssetBasedVideoResponse)
async def generate_asset_video_sync(
    request_body: AssetBasedVideoRequest,
    pixelle_video: PixelleVideoDep,
    request: Request
):
    """
    同步生成自定义素材视频
    
    使用用户上传的素材（图片/视频），结合 AI 生成的旁白和脚本，
    自动编排场景并生成最终视频。
    
    **注意**：对于复杂的视频，生成可能需要几分钟时间。
    推荐使用 `/generate/async` 接口来进行长时间运行的异步生成。
    
    **工作流：**
    1. 通过 `/api/files` 接口上传素材
    2. 使用素材路径调用此接口
    3. 在返回结果中获得生成的视频 URL（如果配置了则为 S3 URL，否则为本地 URL）
    """
    try:
        logger.info(f"[AssetVideo] Sync generation: {len(request_body.assets)} assets, "
                     f"duration={request_body.duration}s")
        
        # Import pipeline
        from pixelle_video.pipelines.asset_based import AssetBasedPipeline
        
        # Create pipeline
        pipeline = AssetBasedPipeline(pixelle_video)
        
        # Execute pipeline
        ctx = await pipeline(
            assets=request_body.assets,
            video_title=request_body.video_title,
            intent=request_body.intent,
            duration=request_body.duration,
            source=request_body.source,
            voice_id=request_body.voice_id,
            tts_speed=request_body.tts_speed,
            bgm_path=request_body.bgm_path,
            bgm_volume=request_body.bgm_volume,
            bgm_mode=request_body.bgm_mode,
        )
        
        # Get file info before potential S3 upload (which may delete local file)
        file_size = os.path.getsize(ctx.final_video_path) if os.path.exists(ctx.final_video_path) else 0
        duration = sum(f.duration for f in ctx.storyboard.frames if hasattr(f, 'duration') and f.duration)
        
        # Upload to S3 or use local URL
        local_url = path_to_url(request, ctx.final_video_path)
        video_url = _upload_to_s3_if_available(ctx.final_video_path, local_url)
        
        # Clean up local task directory only after successful S3 upload
        if video_url != local_url and hasattr(ctx, 'task_dir') and ctx.task_dir:
            from pixelle_video.utils.os_util import cleanup_task_dir
            cleanup_task_dir(str(ctx.task_dir))
        
        return AssetBasedVideoResponse(
            video_url=video_url,
            duration=duration,
            file_size=file_size
        )
        
    except Exception as e:
        logger.error(f"[AssetVideo] Sync generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate/async", response_model=AssetBasedVideoAsyncResponse)
async def generate_asset_video_async(
    request_body: AssetBasedVideoRequest,
    pixelle_video: PixelleVideoDep,
    request: Request
):
    """
    异步生成自定义素材视频
    
    创建一个后台任务用于视频生成。
    立即返回 `task_id`，用于后续跟踪任务进度。
    
    **工作流：**
    1. 通过 `/api/files` 接口上传素材
    2. 使用素材路径调用此接口
    3. 在返回结果中获得 `task_id`
    4. 轮询调用 `/api/tasks/{task_id}` 来检查任务状态
    5. 当状态变为 "completed" 时，从结果中获取视频 URL
    """
    try:
        logger.info(f"[AssetVideo] Async generation: {len(request_body.assets)} assets, "
                     f"duration={request_body.duration}s")
        
        # Create task
        task = task_manager.create_task(
            task_type=TaskType.ASSET_BASED_VIDEO,
            request_params=request_body.model_dump()
        )
        
        # Define async execution function
        async def execute_asset_video():
            from pixelle_video.pipelines.asset_based import AssetBasedPipeline
            
            pipeline = AssetBasedPipeline(pixelle_video)
            
            ctx = await pipeline(
                assets=request_body.assets,
                video_title=request_body.video_title,
                intent=request_body.intent,
                duration=request_body.duration,
                source=request_body.source,
                voice_id=request_body.voice_id,
                tts_speed=request_body.tts_speed,
                bgm_path=request_body.bgm_path,
                bgm_volume=request_body.bgm_volume,
                bgm_mode=request_body.bgm_mode,
            )
            
            file_size = os.path.getsize(ctx.final_video_path) if os.path.exists(ctx.final_video_path) else 0
            duration = sum(f.duration for f in ctx.storyboard.frames if hasattr(f, 'duration') and f.duration)
            
            local_url = path_to_url(request, ctx.final_video_path)
            video_url = _upload_to_s3_if_available(ctx.final_video_path, local_url)
            
            # Clean up local task directory only after successful S3 upload
            if video_url != local_url and hasattr(ctx, 'task_dir') and ctx.task_dir:
                from pixelle_video.utils.os_util import cleanup_task_dir
                cleanup_task_dir(str(ctx.task_dir))
            
            return {
                "video_url": video_url,
                "duration": duration,
                "file_size": file_size
            }
        
        # Start execution
        await task_manager.execute_task(
            task_id=task.task_id,
            coro_func=execute_asset_video
        )
        
        return AssetBasedVideoAsyncResponse(
            task_id=task.task_id
        )
        
    except Exception as e:
        logger.error(f"[AssetVideo] Async generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
