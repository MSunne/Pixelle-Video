"""
Action Transfer video generation endpoints

Transfers motion from a reference video to a target image
using ComfyUI workflows.
Supports asynchronous generation with progress tracking.
"""

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from api.dependencies import PixelleVideoDep
from api.schemas.action_transfer import ActionTransferRequest, ActionTransferAsyncResponse
from api.tasks import task_manager, TaskType
from api.utils.helpers import path_to_url, upload_to_s3_or_fallback, cleanup_after_upload

router = APIRouter(prefix="/action-transfer", tags=["动作迁移"])


@router.post("/generate/async", response_model=ActionTransferAsyncResponse)
async def generate_action_transfer_async(
    request_body: ActionTransferRequest,
    pixelle_video: PixelleVideoDep,
    request: Request,
):
    """
    异步动作迁移视频生成

    使用参考视频中的动作，结合目标人物图片，生成动作迁移视频。

    **工作流：**
    1. 通过 `/api/files` 接口上传参考视频和目标图片
    2. 使用文件路径调用此接口
    3. 在返回结果中获得 `task_id`
    4. 轮询调用 `/api/tasks/{task_id}` 来检查任务状态
    5. 当状态变为 "completed" 时，从结果中获取视频 URL
    """
    try:
        logger.info(f"[ActionTransfer] Async generation: duration={request_body.duration}s, "
                     f"workflow={request_body.workflow_key}")

        # Create task
        task = task_manager.create_task(
            task_type=TaskType.ACTION_TRANSFER_VIDEO,
            request_params=request_body.model_dump(),
        )

        # Define async execution function
        async def execute_action_transfer():
            from pixelle_video.pipelines.action_transfer import ActionTransferPipeline

            pipeline = ActionTransferPipeline(pixelle_video)

            # Progress callback
            step_messages = {
                "generation": "正在初始化工作流...",
                "video_synthesis": "正在合成动作迁移视频...",
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
                video_path=request_body.video_path,
                image_path=request_body.image_path,
                prompt=request_body.prompt,
                duration=request_body.duration,
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
            coro_func=execute_action_transfer,
        )

        return ActionTransferAsyncResponse(task_id=task.task_id)

    except Exception as e:
        logger.error(f"[ActionTransfer] Async generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
