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
    AssetBasedVideoAsyncResponse,
    AssetBasedVideoRequest,
    AssetBasedVideoResponse,
)
from api.tasks import TaskType, task_manager
from api.utils.helpers import cleanup_after_upload, path_to_url, upload_to_s3_or_fallback

router = APIRouter(prefix="/asset-video", tags=["自定义素材视频生成"])


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

    支持本地 Edge TTS 和参考语音克隆两种配音方式，
    输出视频默认带中英对照画面字幕。
    
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
            content_mode=request_body.content_mode,
            script_text=request_body.script_text,
            script_split_mode=request_body.script_split_mode,
            duration=request_body.duration,
            source=request_body.source,
            tts_inference_mode=request_body.tts_inference_mode,
            tts_voice=request_body.tts_voice,
            tts_workflow=request_body.tts_workflow,
            tts_speed=request_body.tts_speed,
            ref_audio=request_body.ref_audio,
            voice_id=request_body.voice_id,
            bgm_path=request_body.bgm_path,
            bgm_volume=request_body.bgm_volume,
            bgm_mode=request_body.bgm_mode,
            llm_model=request_body.llm_model,
        )
        
        # Get file info before potential S3 upload (which may delete local file)
        file_size = os.path.getsize(ctx.final_video_path) if os.path.exists(ctx.final_video_path) else 0
        duration = sum(f.duration for f in ctx.storyboard.frames if hasattr(f, 'duration') and f.duration)
        
        # Upload to S3 or use local URL
        local_url = path_to_url(request, ctx.final_video_path)
        video_url = upload_to_s3_or_fallback(ctx.final_video_path, local_url)
        
        # Clean up local task directory only after successful S3 upload
        task_dir = getattr(ctx, 'task_dir', None)
        cleanup_after_upload(video_url, local_url, str(task_dir) if task_dir else "")
        
        return AssetBasedVideoResponse(
            video_url=video_url,
            duration=duration,
            file_size=file_size
        )

    except ValueError as e:
        logger.error(f"[AssetVideo] Validation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
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

    支持本地 Edge TTS 和参考语音克隆两种配音方式，
    输出视频默认带中英对照画面字幕。
    
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
            
            # Progress callback for polling clients
            def on_progress(event):
                task_manager.update_progress(
                    task.task_id,
                    current=int(event.progress * 100),
                    total=100,
                    message=getattr(event, 'extra_info', '') or event.event_type
                )
            
            ctx = await pipeline(
                assets=request_body.assets,
                video_title=request_body.video_title,
                intent=request_body.intent,
                content_mode=request_body.content_mode,
                script_text=request_body.script_text,
                script_split_mode=request_body.script_split_mode,
                duration=request_body.duration,
                source=request_body.source,
                tts_inference_mode=request_body.tts_inference_mode,
                tts_voice=request_body.tts_voice,
                tts_workflow=request_body.tts_workflow,
                tts_speed=request_body.tts_speed,
                ref_audio=request_body.ref_audio,
                voice_id=request_body.voice_id,
                bgm_path=request_body.bgm_path,
                bgm_volume=request_body.bgm_volume,
                bgm_mode=request_body.bgm_mode,
                llm_model=request_body.llm_model,
                progress_callback=on_progress,
            )
            
            file_size = os.path.getsize(ctx.final_video_path) if os.path.exists(ctx.final_video_path) else 0
            duration = sum(f.duration for f in ctx.storyboard.frames if hasattr(f, 'duration') and f.duration)
            
            local_url = path_to_url(request, ctx.final_video_path)
            video_url = upload_to_s3_or_fallback(ctx.final_video_path, local_url)
            
            # Clean up local task directory only after successful S3 upload
            task_dir = getattr(ctx, 'task_dir', None)
            cleanup_after_upload(video_url, local_url, str(task_dir) if task_dir else "")
            
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

    except ValueError as e:
        logger.error(f"[AssetVideo] Validation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[AssetVideo] Async generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
