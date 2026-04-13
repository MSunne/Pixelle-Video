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
Async helper functions for web UI
"""

import asyncio
import tomllib
from pathlib import Path

from loguru import logger


import threading
import concurrent.futures

# Start a background event loop for Streamlit so that all async calls 
# share the same loop. This prevents 'attached to a different loop'
# errors when global singletons (like ComfyKit's HTTP clients) bind
# to the current loop.
_shared_loop = asyncio.new_event_loop()
_loop_thread = threading.Thread(target=_shared_loop.run_forever, daemon=True, name="AsyncRunner")
_loop_thread.start()


def run_async(coro):
    """Run async coroutine in the shared background event loop context"""
    future = asyncio.run_coroutine_threadsafe(coro, _shared_loop)
    return future.result()


def get_project_version():
    """Get project version from pyproject.toml"""
    try:
        # Get project root (web parent directory)
        web_dir = Path(__file__).resolve().parent.parent
        project_root = web_dir.parent
        pyproject_path = project_root / "pyproject.toml"
        
        if pyproject_path.exists():
            with open(pyproject_path, "rb") as f:
                pyproject_data = tomllib.load(f)
                return pyproject_data.get("project", {}).get("version", "Unknown")
    except Exception as e:
        logger.warning(f"Failed to read version from pyproject.toml: {e}")
    return "Unknown"

