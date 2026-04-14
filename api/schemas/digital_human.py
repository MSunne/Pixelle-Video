"""
Digital Human Video Generation API schemas
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class DigitalHumanVideoRequest(BaseModel):
    """数字人视频生成请求（支持口播和带货两种模式）"""
    
    # === 人物素材 ===
    character_assets: List[str] = Field(
        ...,
        description="人物形象图片路径列表（至少需要一张）。先通过 /api/files 接口上传文件，然后在这里传入路径。",
        min_length=1
    )
    
    # === LLM 模型选择 ===
    llm_model: Optional[str] = Field(
        None,
        description="指定使用的 LLM 模型名称（可选）。不填则使用系统全局配置的模型。"
                    "可通过 GET /api/llm/models 获取可用模型列表。"
    )
    
    # === 模式 ===
    mode: Literal["digital", "customize"] = Field(
        "customize",
        description="生成模式: "
                    "'customize' = 口播模式：人物图片 + 自定义文案 → 数字人朗读视频; "
                    "'digital' = 带货模式：人物图片 + 商品图片 → AI 生成带货视频"
    )
    
    # === 商品素材 (用于 digital 模式) ===
    goods_assets: Optional[List[str]] = Field(
        None,
        description="商品图片路径列表（'digital' 模式必选）"
    )
    goods_title: Optional[str] = Field(
        None,
        description="商品标题/名称（在 'digital' 模式下会被 AI 用来生成解说词）"
    )
    
    # === 旁白文案 ===
    goods_text: str = Field(
        "",
        description="视频文案。口播模式下，这是数字人完整的口播逐字稿。"
                    "带货模式下，如果提供，则按提供的内容读；"
                    "如果为空，AI 会根据商品图片和标题自动生成一段带货话术。"
    )
    
    # === 工作流源 ===
    source: str = Field(
        "runninghub",
        description="云端/本地模式配置: 'runninghub' (云端接口) 或 'selfhost' (本地部署的 ComfyUI 工作流)"
    )
    
    # === TTS (语音) ===
    tts_voice: Optional[str] = Field(
        None,
        description="TTS 语音音色 ID (例如 'zh-CN-YunjianNeural', 'zh-CN-XiaoxiaoNeural')"
    )
    tts_speed: Optional[float] = Field(
        None,
        ge=0.5,
        le=2.0,
        description="TTS 语速"
    )
    tts_inference_mode: str = Field(
        "comfyui",
        description="配音合成模式: 'local' (本地 Edge TTS) 或 'comfyui' (声音克隆) [推荐 comfyui]"
    )
    tts_workflow: Optional[str] = Field(
        None,
        description="TTS 工作流 JSON 路径 (仅在 inference_mode 为 comfyui 时有效)"
    )
    ref_audio: Optional[str] = Field(
        None,
        description="用于声音克隆的参考音频路径（可选）"
    )

    subtitle_enabled: bool = Field(
        True,
        description="是否为数字人视频生成双语字幕并烧录到成片中"
    )
    subtitle_output: Literal["burned", "sidecar", "both"] = Field(
        "both",
        description="字幕输出方式: burned=仅硬字幕, sidecar=仅外挂字幕, both=硬字幕+SRT"
    )
    subtitle_language: Literal["zh", "zh_en", "source"] = Field(
        "zh_en",
        description="字幕语言: zh=仅中文, zh_en=中英双语, source=跟随原始输入语言"
    )
    
    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "summary": "口播模式 - 数字人朗读固定文案",
                    "value": {
                        "character_assets": ["/path/to/character.jpg"],
                        "mode": "customize",
                        "goods_text": "大家好，今天给大家推荐一款超好用的智能保温杯...",
                        "source": "runninghub",
                        "tts_voice": "zh-CN-YunjianNeural",
                        "tts_speed": 1.2,
                        "subtitle_enabled": True,
                        "subtitle_output": "both",
                        "subtitle_language": "zh_en"
                    }
                },
                {
                    "summary": "带货模式 - 商品图 + AI 自动生成带货话术",
                    "value": {
                        "character_assets": ["/path/to/character.jpg"],
                        "mode": "digital",
                        "goods_assets": ["/path/to/goods.jpg"],
                        "goods_title": "智能保温杯",
                        "goods_text": "",
                        "source": "runninghub",
                        "tts_voice": "zh-CN-YunjianNeural",
                        "tts_speed": 1.2,
                        "subtitle_enabled": True,
                        "subtitle_output": "both",
                        "subtitle_language": "zh_en"
                    }
                }
            ]
        }


class DigitalHumanVideoResponse(BaseModel):
    """同步生成的返回结果"""
    success: bool = True
    message: str = "成功"
    video_url: str = Field(..., description="生成的视频播放/下载地址")
    duration: float = Field(0.0, description="视频实际时长（秒）")
    file_size: int = Field(..., description="视频文件大小（字节）")
    subtitle_enabled: bool = Field(False, description="是否启用了字幕生成")
    subtitle_format: Optional[str] = Field(None, description="字幕格式，当前为 srt")
    subtitle_url: Optional[str] = Field(None, description="外挂字幕文件地址（如果生成）")


class DigitalHumanVideoAsyncResponse(BaseModel):
    """异步生成的返回结果"""
    success: bool = True
    message: str = "任务已成功创建"
    task_id: str = Field(..., description="可以传入 /api/tasks/{task_id} 接口来查询进度的任务 ID")
