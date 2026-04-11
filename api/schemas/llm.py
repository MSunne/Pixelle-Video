# Copyright (C) 2025 AIDC-AI
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
LLM API schemas
"""

from typing import Optional, List
from pydantic import BaseModel, Field


class LLMChatRequest(BaseModel):
    """LLM chat request"""
    prompt: str = Field(..., description="User prompt")
    llm_model: Optional[str] = Field(
        None,
        description="指定使用的 LLM 模型名称（可选）。不填则使用系统全局配置的模型。"
                    "可通过 GET /api/llm/models 获取可用模型列表。"
    )
    temperature: float = Field(0.7, ge=0.0, le=2.0, description="Temperature (0.0-2.0)")
    max_tokens: int = Field(2000, ge=1, le=32000, description="Maximum tokens")
    
    class Config:
        json_schema_extra = {
            "example": {
                "prompt": "Explain the concept of atomic habits in 3 sentences",
                "llm_model": None,
                "temperature": 0.7,
                "max_tokens": 2000
            }
        }


class LLMChatResponse(BaseModel):
    """LLM chat response"""
    success: bool = True
    message: str = "Success"
    content: str = Field(..., description="Generated response")
    model_used: str = Field("", description="实际使用的模型名称")
    tokens_used: Optional[int] = Field(None, description="Tokens used (if available)")


# ============================================================================
# Model List
# ============================================================================

class LLMModelInfo(BaseModel):
    """LLM model information"""
    id: str = Field(..., description="模型 ID（可直接传入 llm_model 字段使用）")
    is_current: bool = Field(False, description="是否为当前全局配置的默认模型")


class LLMModelListResponse(BaseModel):
    """LLM model list response"""
    success: bool = True
    message: str = "Success"
    current_model: str = Field(..., description="当前全局配置的默认模型")
    provider: str = Field("", description="当前 LLM 服务商 Base URL")
    models: List[LLMModelInfo] = Field(default_factory=list, description="可用模型列表")

