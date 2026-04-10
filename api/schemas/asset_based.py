"""
Asset-Based Video Generation API schemas
"""

from typing import Optional, List
from pydantic import BaseModel, Field


class AssetBasedVideoRequest(BaseModel):
    """自定义素材生成视频请求"""
    
    # === 素材 ===
    assets: List[str] = Field(
        ...,
        description="素材文件路径列表（图片/视频）。先通过 /api/files 接口上传文件，然后在这里传入路径。",
        min_length=1
    )
    
    # === 视频信息 ===
    video_title: str = Field(
        "",
        description="视频标题（可选，如果为空则 AI 会根据 intent 自动生成）"
    )
    intent: Optional[str] = Field(
        None,
        description="视频意图/核心主旨描述。这会指导 AI 生成最适合该主题的旁白文案和场景编排风格。"
    )
    
    # === 时长 ===
    duration: int = Field(
        30,
        ge=15,
        le=120,
        description="期望生成的视频时长（秒）"
    )
    
    # === 工作流源 ===
    source: str = Field(
        "runninghub",
        description="云端/本地模式配置: 'runninghub' (云端接口) 或 'selfhost' (本地部署的 ComfyUI 工作流)"
    )
    
    # === TTS (语音) ===
    voice_id: str = Field(
        "zh-CN-YunjianNeural",
        description="语音音色 ID (Edge TTS)"
    )
    tts_speed: float = Field(
        1.2,
        ge=0.5,
        le=2.0,
        description="TTS 语速"
    )
    
    # === BGM (背景音乐) ===
    bgm_path: Optional[str] = Field(
        None,
        description="背景音乐文件路径（可选）"
    )
    bgm_volume: float = Field(
        0.2,
        ge=0.0,
        le=1.0,
        description="BGM 音量大小 (0.0-1.0)"
    )
    bgm_mode: str = Field(
        "loop",
        description="BGM 播放模式: 'loop' 循环播放 或 'once' 单次播放"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "assets": ["/path/to/img1.jpg", "/path/to/img2.jpg", "/path/to/video1.mp4"],
                "video_title": "宠物店年终大促",
                "intent": "推广宠物店年终促销活动，吸引更多客户，风格要温馨亲切",
                "duration": 30,
                "source": "runninghub",
                "voice_id": "zh-CN-YunjianNeural",
                "tts_speed": 1.2,
                "bgm_volume": 0.2
            }
        }


class AssetBasedVideoResponse(BaseModel):
    """同步生成的返回结果"""
    success: bool = True
    message: str = "成功"
    video_url: str = Field(..., description="生成的视频播放/下载地址")
    duration: float = Field(..., description="视频实际时长（秒）")
    file_size: int = Field(..., description="视频文件大小（字节）")


class AssetBasedVideoAsyncResponse(BaseModel):
    """异步生成的返回结果"""
    success: bool = True
    message: str = "任务已成功创建"
    task_id: str = Field(..., description="可以传入 /api/tasks/{task_id} 接口来查询进度的任务 ID")
