"""
Image-to-Video generation endpoints

Generates videos from images using ComfyUI workflows.
Supports asynchronous generation with progress tracking.
"""

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from api.dependencies import PixelleVideoDep
from api.schemas.i2v import I2VRequest, I2VAsyncResponse
from api.tasks import task_manager, TaskType
from api.utils.helpers import path_to_url, upload_to_s3_or_fallback, cleanup_after_upload

router = APIRouter(prefix="/i2v", tags=["图生视频"])


@router.post("/generate/async", response_model=I2VAsyncResponse)
async def generate_i2v_async(
    request_body: I2VRequest,
    pixelle_video: PixelleVideoDep,
    request: Request,
):
    """
    异步图生视频

    上传图片 + 提示词，使用 ComfyUI 工作流生成视频。

    **工作流：**
    1. 通过 `/api/files` 接口上传图片
    2. 使用图片路径调用此接口
    3. 在返回结果中获得 `task_id`
    4. 轮询调用 `/api/tasks/{task_id}` 来检查任务状态
    5. 当状态变为 "completed" 时，从结果中获取视频 URL
    """
    try:
        logger.info(f"[I2V] Async generation: images={len(request_body.image_paths)}, "
                     f"workflow={request_body.workflow_key}")

        # Create task
        task = task_manager.create_task(
            task_type=TaskType.I2V_VIDEO,
            request_params=request_body.model_dump(),
        )

        # Define async execution function
        async def execute_i2v():
            from pixelle_video.pipelines.i2v import I2VPipeline

            pipeline = I2VPipeline(pixelle_video)

            # Progress callback
            step_messages = {
                "generation": "正在初始化工作流...",
                "video_synthesis": "正在生成视频...",
                "downloading": "正在下载生成结果...",
                "completed": "视频生成已完成！",
            }

            def on_progress(data: dict):
                step = data.get("step", "")
                progress_val = data.get("progress", 0.0)
                task_manager.update_progress(
                    task.task_id,
                    current=int(progress_val * 100),
                    total=100,
                    message=step_messages.get(step, "正在处理中..."),
                )

            result = await pipeline(
                image_paths=request_body.image_paths,
                prompt=request_body.prompt,
                workflow_key=request_body.workflow_key,
                progress_callback=on_progress,
            )

            local_url = path_to_url(request, result.video_path)
            video_url = upload_to_s3_or_fallback(result.video_path, local_url)
            cleanup_after_upload(video_url, local_url, result.task_dir)

            return {
                "video_url": video_url,
                "file_size": result.file_size,
            }

        # Start execution
        await task_manager.execute_task(
            task_id=task.task_id,
            coro_func=execute_i2v,
        )

        return I2VAsyncResponse(task_id=task.task_id)

    except Exception as e:
        logger.error(f"[I2V] Async generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
