"""
Action Transfer API schemas
"""

from pydantic import BaseModel, Field


class ActionTransferRequest(BaseModel):
    """动作迁移视频请求"""
    
    video_path: str = Field(
        ...,
        description="参考动作视频路径（运动来源）。先通过 /api/files 接口上传文件，然后在这里传入路径。",
    )
    image_path: str = Field(
        ...,
        description="目标人物图片路径（外观来源）。先通过 /api/files 接口上传文件。",
    )
    prompt: str = Field(
        ...,
        description="提示词，描述期望的视频效果",
    )
    duration: int = Field(
        10,
        description="视频时长（秒）",
        ge=1,
        le=30,
    )
    workflow_key: str = Field(
        ...,
        description="工作流 key，如 'runninghub/af_xxx.json'。可用工作流请查看 workflows/ 目录下以 af_ 开头的 JSON 文件。",
    )
    
    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "summary": "动作迁移 - 基础用法",
                    "value": {
                        "video_path": "/path/to/reference_dance.mp4",
                        "image_path": "/path/to/target_person.jpg",
                        "prompt": "A girl dancing gracefully",
                        "duration": 10,
                        "workflow_key": "runninghub/af_xxx.json",
                    }
                }
            ]
        }


class ActionTransferAsyncResponse(BaseModel):
    """异步生成的返回结果"""
    success: bool = True
    message: str = "任务已成功创建"
    task_id: str = Field(..., description="任务 ID，可通过 /api/tasks/{task_id} 查询进度")
