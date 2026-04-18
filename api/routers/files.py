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
File service endpoints

Provides access to generated files (videos, images, audio) and resource files.
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from loguru import logger

from api.config import api_config
from api.schemas.files import FileUploadResponse, UploadedFileInfo

router = APIRouter(prefix="/files", tags=["文件服务"])


@router.post("/upload", response_model=FileUploadResponse, include_in_schema=False)
async def upload_files(
    files: list[UploadFile] = File(..., description="One or more files to upload"),
    subdir: str = Form("general", description="Optional logical upload folder name"),
):
    """
    Upload one or more files for subsequent API usage.

    Typical workflow:
    1. `POST /api/files/upload`
    2. Use returned `path` values in `/api/asset-video/generate/async`
    3. Poll `/api/asset-video/tasks/{task_id}` or `/api/tasks/{task_id}`
    """
    try:
        if not files:
            raise HTTPException(status_code=400, detail="No files uploaded")

        safe_subdir = Path(subdir).name or "general"
        upload_dir = Path.cwd() / "temp" / "uploads" / safe_subdir / uuid.uuid4().hex[:12]
        upload_dir.mkdir(parents=True, exist_ok=True)

        uploaded_files: list[UploadedFileInfo] = []
        for upload in files:
            original_name = Path(upload.filename or "upload.bin").name
            file_bytes = await upload.read()
            file_size = len(file_bytes)
            if file_size > api_config.max_upload_size:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"File '{original_name}' exceeds max upload size "
                        f"({api_config.max_upload_size} bytes)"
                    ),
                )

            saved_name = f"{uuid.uuid4().hex[:8]}_{original_name}"
            file_path = upload_dir / saved_name
            file_path.write_bytes(file_bytes)

            uploaded_files.append(
                UploadedFileInfo(
                    original_name=original_name,
                    saved_name=saved_name,
                    path=str(file_path.resolve()),
                    size=file_size,
                    content_type=upload.content_type,
                )
            )

        return FileUploadResponse(
            message=f"成功上传 {len(uploaded_files)} 个文件",
            files=uploaded_files,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"File upload error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{file_path:path}")
async def get_file(file_path: str):
    """
    Get file by path
    
    Serves files from allowed directories:
    - output/ - Generated files (videos, images, audio)
    - workflows/ - ComfyUI workflow files
    - templates/ - HTML templates
    - bgm/ - Background music
    - data/bgm/ - Custom background music
    - data/templates/ - Custom templates
    - resources/ - Other resources (images, fonts, etc.)
    
    - **file_path**: File path relative to allowed directories
    
    Examples:
    - "abc123.mp4" → output/abc123.mp4
    - "workflows/runninghub/image_flux.json" → workflows/runninghub/image_flux.json
    - "templates/1080x1920/default.html" → templates/1080x1920/default.html
    - "bgm/default.mp3" → bgm/default.mp3
    - "resources/example.png" → resources/example.png
    
    Returns file for download or preview.
    """
    try:
        # Define allowed directories (in priority order)
        allowed_prefixes = [
            "output/",
            "workflows/",
            "templates/",
            "bgm/",
            "data/bgm/",
            "data/templates/",
            "resources/",
        ]
        
        # Check if path starts with allowed prefix, otherwise try output/
        full_path = None
        for prefix in allowed_prefixes:
            if file_path.startswith(prefix):
                full_path = file_path
                break
        
        # If no prefix matched, assume it's in output/ (backward compatibility)
        if full_path is None:
            full_path = f"output/{file_path}"
        
        abs_path = Path.cwd() / full_path
        
        if not abs_path.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
        
        if not abs_path.is_file():
            raise HTTPException(status_code=400, detail=f"Path is not a file: {file_path}")
        
        # Security: only allow access to specified directories
        try:
            rel_path = abs_path.relative_to(Path.cwd())
            rel_path_str = str(rel_path)
            
            # Check if path starts with any allowed prefix
            is_allowed = any(rel_path_str.startswith(prefix.rstrip('/')) for prefix in allowed_prefixes)
            
            if not is_allowed:
                raise HTTPException(
                    status_code=403, 
                    detail=f"Access denied: only {', '.join(p.rstrip('/') for p in allowed_prefixes)} directories are accessible"
                )
        except ValueError:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Determine media type
        suffix = abs_path.suffix.lower()
        media_types = {
            '.mp4': 'video/mp4',
            '.mp3': 'audio/mpeg',
            '.wav': 'audio/wav',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.html': 'text/html',
            '.json': 'application/json',
            '.srt': 'text/plain; charset=utf-8',
            '.ass': 'text/plain; charset=utf-8',
        }
        media_type = media_types.get(suffix, 'application/octet-stream')
        
        # Use inline disposition for browser preview
        return FileResponse(
            path=str(abs_path),
            media_type=media_type,
            headers={
                "Content-Disposition": f'inline; filename="{abs_path.name}"'
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"File access error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
