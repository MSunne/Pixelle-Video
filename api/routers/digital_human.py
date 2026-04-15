"""
Digital Human Video Generation endpoints

Generates digital human oral broadcast videos from character images
and narration text using ComfyUI workflows.

Supports both synchronous and asynchronous generation.
Results are uploaded to S3 when available, with local cleanup to save disk space.
"""

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from api.config import api_config
from api.dependencies import PixelleVideoDep
from api.schemas.digital_human import (
    DigitalHumanVideoAsyncResponse,
    DigitalHumanVideoRequest,
    DigitalHumanVideoResponse,
)
from api.tasks import TaskType, task_manager
from api.utils.digital_human_tasks import build_digital_human_request_fingerprint
from api.utils.helpers import (
    cleanup_after_uploads,
    upload_outputs_to_s3_or_fallback,
)

router = APIRouter(prefix="/digital-human", tags=["数字人视频"])





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
            llm_model=request_body.llm_model,
            source=request_body.source,
            tts_voice=request_body.tts_voice,
            tts_speed=request_body.tts_speed,
            tts_inference_mode=request_body.tts_inference_mode,
            tts_workflow=request_body.tts_workflow,
            ref_audio=request_body.ref_audio,
            subtitle_enabled=request_body.subtitle_enabled,
            subtitle_output=request_body.subtitle_output,
            subtitle_language=request_body.subtitle_language,
        )
        
        file_size = result.file_size
        final_urls, local_urls = upload_outputs_to_s3_or_fallback(
            request,
            {
                "video": result.video_path,
                "subtitle": result.subtitle_path,
            },
        )
        cleanup_after_uploads(final_urls, local_urls, result.task_dir)
        
        return DigitalHumanVideoResponse(
            video_url=final_urls["video"],
            duration=result.duration,
            file_size=file_size,
            subtitle_enabled=result.subtitle_enabled,
            subtitle_format=result.subtitle_format,
            subtitle_url=final_urls.get("subtitle"),
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

        fingerprint = build_digital_human_request_fingerprint(request_body.model_dump())
        existing_task = task_manager.find_active_task_by_fingerprint(
            TaskType.DIGITAL_HUMAN_VIDEO,
            fingerprint,
        )
        if existing_task:
            logger.info(
                "[DigitalHuman] Reusing existing task {} for duplicate async request",
                existing_task.task_id,
            )
            return DigitalHumanVideoAsyncResponse(task_id=existing_task.task_id)

        active_digital_human_tasks = task_manager.count_active_tasks(TaskType.DIGITAL_HUMAN_VIDEO)
        if active_digital_human_tasks >= api_config.digital_human_max_active_tasks:
            raise HTTPException(
                status_code=429,
                detail=(
                    "Digital human queue is busy. "
                    "Please wait for current jobs to finish before submitting more."
                ),
            )
        
        # Create task
        task = task_manager.create_task(
            task_type=TaskType.DIGITAL_HUMAN_VIDEO,
            request_params=request_body.model_dump(),
            request_fingerprint=fingerprint,
        )
        
        # Define async execution function
        async def execute_digital_human():
            from pixelle_video.pipelines.digital_human import DigitalHumanPipeline
            
            pipeline = DigitalHumanPipeline(pixelle_video)
            
            # Progress callback: update task progress for polling clients
            def on_progress(data: dict):
                step = data.get("step", "")
                progress_val = data.get("progress", 0.0)
                step_messages = {
                    "combine_image": "正在合成人与商品关键图...",
                    "synthesis": "正在生成解说文案与关键图...",
                    "tts": "正在合成语音配音...",
                    "video_synthesis": "正在合成对口型视频并渲染...",
                    "subtitle_translation": "正在生成双语字幕...",
                    "subtitle_alignment": "正在对齐字幕时间轴...",
                    "subtitle_burn": "正在烧录字幕...",
                    "completed": "视频生成已完成！"
                }
                task_manager.update_progress(
                    task.task_id,
                    current=int(progress_val * 100),
                    total=100,
                    message=step_messages.get(step, "正在处理中...")
                )
            
            result = await pipeline(
                character_assets=request_body.character_assets,
                mode=request_body.mode,
                goods_assets=request_body.goods_assets,
                goods_title=request_body.goods_title,
                goods_text=request_body.goods_text,
                llm_model=request_body.llm_model,
                source=request_body.source,
                tts_voice=request_body.tts_voice,
                tts_speed=request_body.tts_speed,
                tts_inference_mode=request_body.tts_inference_mode,
                tts_workflow=request_body.tts_workflow,
                ref_audio=request_body.ref_audio,
                subtitle_enabled=request_body.subtitle_enabled,
                subtitle_output=request_body.subtitle_output,
                subtitle_language=request_body.subtitle_language,
                task_id=task.task_id,
                progress_callback=on_progress,
            )
            
            file_size = result.file_size
            final_urls, local_urls = upload_outputs_to_s3_or_fallback(
                request,
                {
                    "video": result.video_path,
                    "subtitle": result.subtitle_path,
                },
            )
            cleanup_after_uploads(final_urls, local_urls, result.task_dir)
            
            return {
                "video_url": final_urls["video"],
                "duration": result.duration,
                "file_size": file_size,
                "subtitle_enabled": result.subtitle_enabled,
                "subtitle_format": result.subtitle_format,
                "subtitle_url": final_urls.get("subtitle"),
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
