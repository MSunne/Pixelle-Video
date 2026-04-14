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
Shared helper functions for API routers.

Consolidates path_to_url, S3 upload, and cleanup logic
that was previously duplicated across multiple router files.
"""

import os
from pathlib import Path

from fastapi import Request
from loguru import logger


def path_to_url(request: Request, file_path: str) -> str:
    """
    Convert file path to accessible URL.
    
    Handles both absolute and relative paths, extracting the path relative
    to the output directory for URL construction.
    
    Args:
        request: FastAPI Request object (provides base_url from actual request)
        file_path: Absolute or relative file path
    
    Returns:
        Full URL to access the file
    
    Examples:
        Windows: G:\\...\\output\\20251205_233630_c939\\final.mp4
              -> http://localhost:8000/api/files/20251205_233630_c939/final.mp4
        
        Linux:   /home/user/.../output/20251205_233630_c939/final.mp4
              -> http://localhost:8000/api/files/20251205_233630_c939/final.mp4
        
        Domain:  With domain request -> https://your-domain.com/api/files/...
    """
    # Normalize path separators to forward slashes first (for cross-platform compatibility)
    file_path = file_path.replace("\\", "/")
    
    # Check if it's an absolute path (works for both Windows and Linux)
    is_absolute = os.path.isabs(file_path) or Path(file_path).is_absolute()
    
    if is_absolute:
        # Find "output" in the path and get everything after it
        parts = file_path.split("/")
        try:
            output_idx = parts.index("output")
            relative_parts = parts[output_idx + 1:]
            file_path = "/".join(relative_parts)
        except ValueError:
            # If "output" not in path, use the filename only
            file_path = Path(file_path).name
    else:
        # If relative path starting with "output/", remove it
        if file_path.startswith("output/"):
            file_path = file_path[7:]  # Remove "output/"
    
    # Build URL using request's base_url (automatically matches the request host)
    base_url = str(request.base_url).rstrip('/')
    return f"{base_url}/api/files/{file_path}"


def upload_file_to_s3_or_fallback(local_path: str, fallback_url: str) -> str:
    """
    Upload file to S3 if available, return S3 public URL.
    Falls back to local API URL if S3 is not configured.
    After successful S3 upload, local file is deleted to save disk space.
    
    Args:
        local_path: Absolute path to the local file
        fallback_url: URL to return if S3 is not available
    
    Returns:
        S3 URL or fallback URL
    """
    try:
        from pixelle_video.storage import get_s3_storage
        s3 = get_s3_storage()
        if s3.is_available() and os.path.exists(local_path):
            return s3.upload_file(local_path, cleanup_local=True)
    except Exception as e:
        logger.warning(f"S3 upload failed, using local URL: {e}")
    return fallback_url


def upload_to_s3_or_fallback(local_path: str, fallback_url: str) -> str:
    """Backward-compatible wrapper for generic file uploads."""
    return upload_file_to_s3_or_fallback(local_path, fallback_url)


def upload_outputs_to_s3_or_fallback(
    request: Request,
    output_paths: dict[str, str | None],
) -> tuple[dict[str, str], dict[str, str]]:
    """
    Upload multiple output files and return both local and final URLs.
    """
    final_urls: dict[str, str] = {}
    local_urls: dict[str, str] = {}

    for key, local_path in output_paths.items():
        if not local_path:
            continue
        local_url = path_to_url(request, local_path)
        local_urls[key] = local_url
        final_urls[key] = upload_file_to_s3_or_fallback(local_path, local_url)

    return final_urls, local_urls


def cleanup_after_uploads(final_urls: dict[str, str], local_urls: dict[str, str], task_dir: str):
    """
    Clean up local task directory after all exposed outputs were uploaded to remote storage.
    """
    if not task_dir or not final_urls:
        return

    all_remote = all(final_urls.get(key) != local_urls.get(key) for key in final_urls)
    if not all_remote:
        return

    try:
        from pixelle_video.utils.os_util import cleanup_task_dir
        cleanup_task_dir(str(task_dir))
    except Exception as e:
        logger.warning(f"Task directory cleanup failed: {e}")


def cleanup_after_upload(video_url: str, local_url: str, task_dir: str):
    """
    Clean up local task directory after successful S3 upload.
    
    Only cleans up if the video_url differs from local_url (meaning S3 upload
    succeeded and the video is now hosted remotely).
    
    Args:
        video_url: The final video URL (S3 or local)
        local_url: The local fallback URL
        task_dir: Path to the local task directory to clean up
    """
    cleanup_after_uploads({"video": video_url}, {"video": local_url}, task_dir)
