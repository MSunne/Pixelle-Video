"""
Guided step-by-step Digital Human Video Generation workflow router.
Provides a consolidated, linear Swagger experience.
"""

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from api.config import api_config
from api.dependencies import PixelleVideoDep
from api.schemas.digital_human_flow import (
    Step3GenerateRequest,
    Step3GenerateResponse,
)
from api.tasks import Task, TaskType, task_manager
from api.utils.digital_human_tasks import build_digital_human_request_fingerprint
from api.utils.helpers import cleanup_after_uploads, upload_outputs_to_s3_or_fallback

router = APIRouter()



@router.post("/step3-generate-video", response_model=Step3GenerateResponse, tags=["数字人视频"], summary="开始生成数字人视频（口播/带货）")
async def step3_generate_video(
    request_body: Step3GenerateRequest,
    pixelle_video: PixelleVideoDep,
    request: Request,
):
    """
    提交所有信息，异步开始生成数字人视频。
    
    **支持模式：**
    - `customize` (口播模式): 人物图片 + 自定义文案 → 数字人朗读口播视频
    - `digital` (带货模式): 人物图片 + 商品图片 + 商品标题 → AI 自动生成带货视频
    
    如果提供了 ref_audio，系统会自动克隆声音。
    
    返回 task_id，用于在 Step 4 轮询状态。
    """
    try:
        # Re-pack into standard DigitalHuman pipeline request format
        from api.schemas.digital_human import DigitalHumanVideoRequest
        standard_req = DigitalHumanVideoRequest(
            character_assets=[request_body.character_asset_path],
            llm_model=request_body.llm_model,
            mode=request_body.mode,
            goods_assets=[request_body.goods_asset_path] if request_body.goods_asset_path else None,
            goods_title=request_body.goods_title,
            goods_text=request_body.goods_text,
            source=request_body.source,
            tts_inference_mode="comfyui",
            ref_audio=request_body.ref_audio,
            subtitle_enabled=request_body.subtitle_enabled,
            subtitle_output=request_body.subtitle_output,
            subtitle_language=request_body.subtitle_language,
        )

        fingerprint = build_digital_human_request_fingerprint(standard_req.model_dump())
        existing_task = task_manager.find_active_task_by_fingerprint(
            TaskType.DIGITAL_HUMAN_VIDEO,
            fingerprint,
        )
        if existing_task:
            logger.info(
                "[Flow Gen] Reusing existing task {} for duplicate async request",
                existing_task.task_id,
            )
            return Step3GenerateResponse(task_id=existing_task.task_id)

        active_digital_human_tasks = task_manager.count_active_tasks(TaskType.DIGITAL_HUMAN_VIDEO)
        if active_digital_human_tasks >= api_config.digital_human_max_active_tasks:
            raise HTTPException(
                status_code=429,
                detail=(
                    "Digital human queue is busy. "
                    "Please wait for current jobs to finish before submitting more."
                ),
            )
        
        # Create Task
        task = task_manager.create_task(
            task_type=TaskType.DIGITAL_HUMAN_VIDEO,
            request_params=standard_req.model_dump(),
            request_fingerprint=fingerprint,
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
                        "subtitle_translation": "正在生成双语字幕...",
                        "subtitle_alignment": "正在对齐字幕时间轴...",
                        "subtitle_burn": "正在烧录字幕...",
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
                llm_model=standard_req.llm_model,
                source=standard_req.source,
                tts_inference_mode=standard_req.tts_inference_mode,
                ref_audio=standard_req.ref_audio,
                subtitle_enabled=standard_req.subtitle_enabled,
                subtitle_output=standard_req.subtitle_output,
                subtitle_language=standard_req.subtitle_language,
                task_id=task.task_id,
                progress_callback=on_progress,
            )
            
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
                "file_size": result.file_size,
                "subtitle_enabled": result.subtitle_enabled,
                "subtitle_format": result.subtitle_format,
                "subtitle_url": final_urls.get("subtitle"),
            }
            
        # Execute Background Task
        await task_manager.execute_task(
            task_id=task.task_id,
            coro_func=execute_digital_human_flow
        )
        
        return Step3GenerateResponse(task_id=task.task_id)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Flow Gen] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/step4-check-status/{task_id}", response_model=Task, tags=["数字人视频"], summary="轮询任务执行进度与结果")
async def step4_check_status(task_id: str):
    """
    检查第三步生成的数字人视频进度。
    当 status 变为 "completed" 时，result.video_url 将包含生成的视频下载地址。
    """
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.task_type != TaskType.DIGITAL_HUMAN_VIDEO:
        raise HTTPException(
            status_code=404,
            detail="任务不属于数字人视频流程，请使用对应任务类型的专用进度接口",
        )
    return task
