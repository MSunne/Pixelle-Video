"""
File upload API schemas
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class UploadedFileInfo(BaseModel):
    """Single uploaded file metadata."""

    original_name: str = Field(..., description="Original uploaded filename")
    saved_name: str = Field(..., description="Saved filename on server")
    path: str = Field(..., description="Absolute local path saved on the server")
    size: int = Field(..., description="File size in bytes")
    content_type: Optional[str] = Field(None, description="Detected content type")


class FileUploadResponse(BaseModel):
    """Response for multipart file uploads."""

    success: bool = True
    message: str = "上传成功"
    files: List[UploadedFileInfo] = Field(default_factory=list)
