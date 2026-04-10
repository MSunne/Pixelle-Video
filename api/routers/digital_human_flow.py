"""
Guided step-by-step Digital Human Video Generation workflow router.
Provides a consolidated, linear Swagger experience.
"""

import os
from pathlib import Path
import uuid
import shutil
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from loguru import logger

from api.dependencies import PixelleVideoDep
from api.schemas.digital_human_flow import (
    Step1UploadResponse,
    Step2TTSRequest,
    Step2TTSResponse,
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


@router.post("/step1-upload", response_model=Step1UploadResponse, tags=["Step 1"], summary="第一步：上传人物或商品形象图片")
async def step1_upload_asset(
    file: UploadFile = File(..., description="要上传的图片或音频文件"),
    request: Request = None
):
    """
    上传数字人形象图片或商品图片素材。
    
    返回文件的服务器路径（传给后续接口使用）以及可以点击预览的 URL。
    """
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="Empty filename")
            
        ext = Path(file.filename).suffix.lower()
        if ext not in [".jpg", ".jpeg", ".png", ".webp", ".mp3", ".wav", ".m4a"]:
            raise HTTPException(status_code=400, detail="Unsupported file type")
            
        # Ensure output directory exists
        upload_dir = Path("output/uploads/dh_flow")
        upload_dir.mkdir(parents=True, exist_ok=True)
        
        # Unique file name to prevent collision
        unique_name = f"{uuid.uuid4().hex[:8]}_{file.filename}"
        final_path = upload_dir / unique_name
        
        # Save file to disk
        with open(final_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # Build URL
        file_url = _path_to_url(request, str(final_path))
        
        return Step1UploadResponse(
            file_path=str(final_path.absolute()),
            file_url=file_url
        )
    except Exception as e:
        logger.error(f"[Flow Upload] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/step2-synthesize-tts", response_model=Step2TTSResponse, tags=["Step 2 (可选)"], summary="第二步：合成配音（预览音频）")
async def step2_synthesize_tts(
    request_body: Step2TTSRequest,
    pixelle_video: PixelleVideoDep,
    request: Request
):
    """
    【可选步骤】根据输入的文案旁白，提前合成配音音频。
    用户可用此接口提前试听声音效果是否满意，再决定是否进行完整的视频生成。
    """
    try:
        from pixelle_video.utils.tts_util import get_audio_duration
        
        tts_kwargs = {
            "text": request_body.text,
            "inference_mode": "comfyui",
            "voice": "zh-CN-YunjianNeural",
            "speed": 1.2,
        }
        
        if request_body.ref_audio:
            tts_kwargs["ref_audio"] = request_body.ref_audio
            
        audio_path = await pixelle_video.tts(**tts_kwargs)
        duration = get_audio_duration(audio_path)
        audio_url = _path_to_url(request, audio_path)
        
        return Step2TTSResponse(
            audio_path=audio_path,
            audio_url=audio_url,
            duration=duration
        )
    except Exception as e:
        logger.error(f"[Flow TTS] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/step3-generate-video", response_model=Step3GenerateResponse, tags=["Step 3"], summary="第三步：开始生成最终数字人口播视频")
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
            video_url = _upload_to_s3_if_available(result.video_path, f"/api/files/{Path(result.video_path).name}")
            
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


@router.get("/step4-check-status/{task_id}", response_model=Task, tags=["Step 4"], summary="第四步：轮询任务执行结果")
async def step4_check_status(task_id: str):
    """
    检查第三步生成的数字人视频进度。
    当 status 变为 "completed" 时，result.video_url 将包含生成的视频下载地址。
    """
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task
