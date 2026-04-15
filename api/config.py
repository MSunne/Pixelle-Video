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
API Configuration
"""

import os
from typing import Optional

from pydantic import BaseModel


class APIConfig(BaseModel):
    """API configuration"""
    
    # Server settings
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False
    
    # CORS settings
    cors_enabled: bool = True
    cors_origins: list[str] = ["*"]
    
    # Task settings
    max_concurrent_tasks: int = int(os.getenv("PIXELLE_API_MAX_CONCURRENT_TASKS", "5"))
    task_cleanup_interval: int = int(os.getenv("PIXELLE_API_TASK_CLEANUP_INTERVAL", "3600"))
    task_retention_time: int = int(os.getenv("PIXELLE_API_TASK_RETENTION_TIME", "86400"))
    task_timeout: int = int(os.getenv("PIXELLE_API_TASK_TIMEOUT", "1800"))
    task_max_retries: int = int(os.getenv("PIXELLE_API_TASK_MAX_RETRIES", "2"))
    digital_human_max_concurrent_tasks: int = int(
        os.getenv("PIXELLE_API_DIGITAL_HUMAN_MAX_CONCURRENT_TASKS", "1")
    )
    digital_human_max_active_tasks: int = int(
        os.getenv("PIXELLE_API_DIGITAL_HUMAN_MAX_ACTIVE_TASKS", "2")
    )
    
    # File upload settings
    max_upload_size: int = 100 * 1024 * 1024  # 100MB
    
    # API settings
    api_prefix: str = "/api"
    docs_url: Optional[str] = "/docs"
    redoc_url: Optional[str] = "/redoc"
    openapi_url: Optional[str] = "/openapi.json"


# Global config instance
api_config = APIConfig()
