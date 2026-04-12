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
LLM (Large Language Model) endpoints
"""

from fastapi import APIRouter, HTTPException
from loguru import logger

from api.dependencies import PixelleVideoDep
from api.schemas.llm import (
    LLMChatRequest,
    LLMChatResponse,
    LLMModelInfo,
    LLMModelListResponse,
)

router = APIRouter(prefix="/llm", tags=["基础服务"])


@router.get("/models", response_model=LLMModelListResponse)
async def list_llm_models():
    """
    获取可用的 LLM 模型列表
    
    返回当前系统配置的 LLM 服务商下所有可用模型。
    前端可以在调用其他 API 时，将列表中的模型 ID 传入 `llm_model` 字段来覆盖默认模型。
    
    - **current_model**: 当前全局默认模型
    - **provider**: 当前 LLM 服务商 Base URL
    - **models**: 可用模型列表
    """
    from pixelle_video.config import config_manager
    
    current_model = config_manager.config.llm.model
    base_url = config_manager.config.llm.base_url
    api_key = config_manager.config.llm.api_key
    
    models = []
    
    if api_key and base_url:
        try:
            from pixelle_video.utils.llm_util import fetch_available_models
            model_ids = fetch_available_models(api_key, base_url)
            models = [
                LLMModelInfo(
                    id=model_id,
                    is_current=(model_id == current_model),
                )
                for model_id in model_ids
            ]
        except Exception as e:
            logger.warning(f"Failed to fetch models from API: {e}")
            # Fallback: return only the current model
            if current_model:
                models = [LLMModelInfo(id=current_model, is_current=True)]
    elif current_model:
        # No API key configured, just return the configured model
        models = [LLMModelInfo(id=current_model, is_current=True)]
    
    return LLMModelListResponse(
        current_model=current_model or "",
        provider=base_url or "",
        models=models,
    )


@router.post("/chat", response_model=LLMChatResponse)
async def llm_chat(
    request: LLMChatRequest,
    pixelle_video: PixelleVideoDep
):
    """
    LLM chat endpoint
    
    Generate text response using configured LLM.
    
    - **prompt**: User prompt/question
    - **temperature**: Creativity level (0.0-2.0, lower = more deterministic)
    - **max_tokens**: Maximum response length
    
    Returns generated text response.
    """
    try:
        logger.info(f"LLM chat request: {request.prompt[:50]}...")
        
        # Build LLM call kwargs, inject model override if specified
        llm_kwargs = {
            "prompt": request.prompt,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.llm_model:
            llm_kwargs["model"] = request.llm_model
        
        # Call LLM service
        response = await pixelle_video.llm(**llm_kwargs)
        
        # Determine model actually used
        model_used = request.llm_model or pixelle_video.llm.active
        
        return LLMChatResponse(
            content=response,
            model_used=model_used,
            tokens_used=None  # Can add token counting if needed
        )
        
    except Exception as e:
        logger.error(f"LLM chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


