"""
Image-to-Video API schemas
"""

from typing import List
from pydantic import BaseModel, Field


class I2VRequest(BaseModel):
    """图生视频请求"""
    
    image_paths: List[str] = Field(
        ...,
        description="图片路径列表（至少需要一张）。先通过 /api/files 接口上传文件，然后在这里传入路径。",
        min_length=1,
    )
    prompt: str = Field(
        ...,
        description="视频生成提示词，描述期望的动态效果",
    )
    workflow_key: str = Field(
        ...,
        description="工作流 key，如 'runninghub/i2v_xxx.json'。可用工作流请查看 workflows/ 目录下以 i2v_ 开头的 JSON 文件。",
    )
    
    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "summary": "图生视频 - 基础用法",
                    "value": {
                        "image_paths": ["/path/to/image.jpg"],
                        "prompt": "A beautiful girl is smiling and waving",
                        "workflow_key": "runninghub/i2v_wan.json",
                    }
                }
            ]
        }


class I2VAsyncResponse(BaseModel):
    """异步生成的返回结果"""
    success: bool = True
    message: str = "任务已成功创建"
    task_id: str = Field(..., description="任务 ID，可通过 /api/tasks/{task_id} 查询进度")
