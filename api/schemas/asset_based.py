"""
Asset-Based Video Generation API schemas
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class AssetBasedVideoRequest(BaseModel):
    """自定义素材生成视频请求"""
    
    # === 素材 ===
    assets: List[str] = Field(
        ...,
        description="素材文件路径列表（图片/视频）。先通过 /api/files 接口上传文件，然后在这里传入路径。",
        min_length=1
    )
    
    # === LLM 模型选择 ===
    llm_model: Optional[str] = Field(
        None,
        description="指定使用的 LLM 模型名称（可选）。不填则使用系统全局配置的模型。"
                    "可通过 GET /api/llm/models 获取可用模型列表。"
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
    content_mode: Literal["intent", "script"] = Field(
        "intent",
        description="内容生成模式: 'intent' = 基于视频意图自动写稿; 'script' = 使用用户提供的固定文案"
    )
    script_text: Optional[str] = Field(
        None,
        description="固定视频文案（content_mode='script' 时必填，系统会按原文分段并匹配素材）"
    )
    script_split_mode: Literal["paragraph", "line", "sentence"] = Field(
        "paragraph",
        description="固定文案切分方式（content_mode='script' 时生效）"
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
    tts_inference_mode: Literal["local", "comfyui"] = Field(
        "local",
        description="配音合成模式: 'local' (本地 Edge TTS) 或 'comfyui' (参考语音克隆)"
    )
    tts_voice: Optional[str] = Field(
        "zh-CN-YunjianNeural",
        description="TTS 语音音色 ID（local 模式下生效）"
    )
    tts_workflow: Optional[str] = Field(
        None,
        description="TTS 工作流 JSON 路径（comfyui 模式下可选；如果上传 ref_audio 且未指定，系统会自动选择 tts_index2 工作流）"
    )
    tts_speed: float = Field(
        1.2,
        ge=0.5,
        le=2.0,
        description="TTS 语速（local 模式下生效）"
    )
    ref_audio: Optional[str] = Field(
        None,
        description="参考音频路径（comfyui 模式下用于参考语音合成）"
    )
    voice_id: Optional[str] = Field(
        None,
        description="旧版语音音色字段（已弃用，仅用于兼容旧调用）",
        deprecated=True,
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

    @model_validator(mode="after")
    def validate_script_mode(self):
        """Validate fixed-script mode specific requirements."""
        if isinstance(self.intent, str) and not self.intent.strip():
            self.intent = None

        if self.content_mode != "script":
            return self

        if not self.script_text or not self.script_text.strip():
            raise ValueError("script_text is required when content_mode='script'")

        if self.tts_inference_mode != "comfyui":
            raise ValueError("tts_inference_mode must be 'comfyui' when content_mode='script'")

        if not self.ref_audio or not self.ref_audio.strip():
            raise ValueError("ref_audio is required when content_mode='script'")

        return self
    
    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "summary": "本地 TTS 合成",
                    "value": {
                        "assets": ["/path/to/img1.jpg", "/path/to/img2.jpg", "/path/to/video1.mp4"],
                        "video_title": "宠物店年终大促",
                        "intent": "推广宠物店年终促销活动，吸引更多客户，风格要温馨亲切",
                        "duration": 30,
                        "source": "runninghub",
                        "tts_inference_mode": "local",
                        "tts_voice": "zh-CN-YunjianNeural",
                        "tts_speed": 1.2,
                        "bgm_volume": 0.2
                    }
                },
                {
                    "summary": "参考语音克隆",
                    "value": {
                        "assets": ["/path/to/img1.jpg", "/path/to/video1.mp4"],
                        "video_title": "新品口播混剪",
                        "intent": "突出新品卖点，节奏轻快，适合短视频传播",
                        "duration": 30,
                        "source": "runninghub",
                        "tts_inference_mode": "comfyui",
                        "ref_audio": "/path/to/reference.wav",
                        "bgm_volume": 0.2
                    }
                },
                {
                    "summary": "固定文案 + 参考语音克隆",
                    "value": {
                        "assets": ["/path/to/living-room.jpg", "/path/to/render.mp4"],
                        "content_mode": "script",
                        "script_text": "第一段文案。\n\n第二段文案。",
                        "script_split_mode": "paragraph",
                        "source": "runninghub",
                        "tts_inference_mode": "comfyui",
                        "ref_audio": "/path/to/reference.wav"
                    }
                }
            ]
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
