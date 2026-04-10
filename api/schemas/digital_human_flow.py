"""
Digital Human Flow API schemas for the guided step-by-step Swagger.
"""
from typing import Optional, List, Literal
from pydantic import BaseModel, Field


# --- Step 1: Upload Assets ---
class Step1UploadResponse(BaseModel):
    success: bool = True
    message: str = "上传成功"
    file_path: str = Field(..., description="文件的绝对路径，用于传递给后续步骤的参数中（如 character_asset_path, goods_asset_path, ref_audio 等）")
    file_url: str = Field(..., description="可以通过浏览器直接访问的预览地址")


# --- Step 2: TTS Synthesis ---
class Step2TTSRequest(BaseModel):
    text: str = Field(..., description="需要合成的口播文案 / Narration text")
    # ComfyUI Params
    ref_audio: Optional[str] = Field(None, description="参考音频路径 (对应前端上传的参考音频，基于此文件克隆声音)")

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "summary": "合成配音",
                    "value": {
                        "text": "家人们，这款老廖牌香薰简直无敌了。",
                        "ref_audio": "/path/to/uploads/dh_flow/xxx_audio.m4a"
                    }
                }
            ]
        }

class Step2TTSResponse(BaseModel):
    success: bool = True
    message: str = "配音合成成功"
    audio_path: str = Field(..., description="合成的音频本地路径")
    audio_url: str = Field(..., description="可以直接试听预览的音频地址")
    duration: float = Field(..., description="音频时长（秒）")


# --- Step 3: Generation ---
class Step3GenerateRequest(BaseModel):
    # 核心模块对应
    character_asset_path: str = Field(..., description="人物形象图片路径（对应前端的：人物形象上传）")
    
    source: Literal["runninghub", "selfhost"] = Field("runninghub", description="服务配置（对应前端的：服务配置），代表是使用云端还是本地 ComfyUI 执行流")
    
    mode: Literal["digital", "customize"] = Field(
        "digital",
        description="生成模式选择（对应前端的：选择生成模式）。"
                    "'digital' = 带货模式 (需要商品图和AI旁白); "
                    "'customize' = 自定义模式 (仅需固定文案的纯口播)"
    )
    
    # 素材与配置模块对应
    goods_asset_path: Optional[str] = Field(None, description="商品图片素材路径（对应前端的：上传素材。仅带货模式必须）")
    goods_text: str = Field(..., description="口播文案（对应前端的：口播文案。两个模式都会使用这段文本发音）")
    goods_title: Optional[str] = Field(None, description="AI创作旁白/商品标题（对应前端的：AI创作旁白。仅带货模式必填，用于生图）")
    
    # TTS / 声音配置模块对应
    ref_audio: Optional[str] = Field(None, description="参考音频（对应前端上传的：参考音频。系统会自动克隆声音）")

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "summary": "带货模式 - 包含商品和文案",
                    "value": {
                        "character_asset_path": "/path/to/uploads/dh_flow/avatar.jpg",
                        "source": "runninghub",
                        "mode": "digital",
                        "goods_asset_path": "/path/to/uploads/dh_flow/goods.jpg",
                        "goods_text": "家人们，这款老廖牌香薰简直无敌了。",
                        "goods_title": "老廖牌香薰",
                        "ref_audio": "/path/to/uploads/dh_flow/audio.m4a"
                    }
                },
                {
                    "summary": "自定义模式 - 仅需人物的口播",
                    "value": {
                        "character_asset_path": "/path/to/uploads/dh_flow/avatar.jpg",
                        "source": "runninghub",
                        "mode": "customize",
                        "goods_asset_path": None,
                        "goods_text": "今天和大家分享一个关于AI赋能的小技巧...",
                        "goods_title": None,
                        "ref_audio": "/path/to/uploads/dh_flow/audio.m4a"
                    }
                }
            ]
        }

class Step3GenerateResponse(BaseModel):
    success: bool = True
    message: str = "任务已成功提交后台执行"
    task_id: str = Field(..., description="任务 ID，用于在 Step 4 轮询最新进度直至完成")
