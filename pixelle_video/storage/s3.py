"""
S3 Storage Service

Provides S3-compatible object storage for uploading generated videos and images.
After upload, local files can be deleted to save disk space.
When files are needed again, they can be pulled from S3.

Configuration via environment variables:
    OMNIDRIVE_S3_ENDPOINT          - S3 endpoint URL
    OMNIDRIVE_S3_BUCKET            - S3 bucket name
    OMNIDRIVE_S3_ACCESS_KEY        - S3 access key
    OMNIDRIVE_S3_SECRET_KEY        - S3 secret key
    OMNIDRIVE_S3_PUBLIC_BASE_URL   - Public base URL for accessing files
    OMNIDRIVE_S3_IMAGE_STORE_PATH  - S3 path prefix for images
    OMNIDRIVE_S3_VIDEO_STORE_PATH  - S3 path prefix for videos

Usage:
    from pixelle_video.storage import get_s3_storage

    s3 = get_s3_storage()
    if s3.is_available():
        url = s3.upload_video("/local/path/final.mp4", cleanup_local=True)
        # url = "https://qny.sunne.xyz/plus_ai/video/20260409_xxxx/final.mp4"

        # Later, pull file back from S3 when needed
        local_path = s3.download("/local/path/final.mp4", url)
"""

import os
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from loguru import logger


class S3Storage:
    """
    S3-compatible storage client.
    
    Supports upload, download, and local cleanup for Qiniu/AWS/MinIO S3.
    """
    
    def __init__(
        self,
        endpoint: Optional[str] = None,
        bucket: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        public_base_url: Optional[str] = None,
        image_store_path: Optional[str] = None,
        video_store_path: Optional[str] = None,
    ):
        """
        Initialize S3 storage client.
        
        All parameters default to reading from OMNIDRIVE_S3_* environment variables.
        """
        self._endpoint = endpoint or os.environ.get("OMNIDRIVE_S3_ENDPOINT", "")
        self._bucket = bucket or os.environ.get("OMNIDRIVE_S3_BUCKET", "")
        self._access_key = access_key or os.environ.get("OMNIDRIVE_S3_ACCESS_KEY", "")
        self._secret_key = secret_key or os.environ.get("OMNIDRIVE_S3_SECRET_KEY", "")
        self._public_base_url = (
            public_base_url or os.environ.get("OMNIDRIVE_S3_PUBLIC_BASE_URL", "")
        ).rstrip("/")
        self._image_store_path = (
            image_store_path or os.environ.get("OMNIDRIVE_S3_IMAGE_STORE_PATH", "plus_ai/img")
        ).strip("/")
        self._video_store_path = (
            video_store_path or os.environ.get("OMNIDRIVE_S3_VIDEO_STORE_PATH", "plus_ai/video")
        ).strip("/")
        
        self._client = None
        self._available: Optional[bool] = None
    
    def is_available(self) -> bool:
        """Check if S3 storage is configured and accessible."""
        if self._available is not None:
            return self._available
        
        if not all([self._endpoint, self._bucket, self._access_key, self._secret_key]):
            logger.debug("S3 storage: not configured (missing env vars)")
            self._available = False
            return False
        
        try:
            client = self._get_client()
            # Use list_objects_v2 with MaxKeys=1 for compatibility
            # (head_bucket is not supported by all S3-compatible providers like Qiniu)
            client.list_objects_v2(Bucket=self._bucket, MaxKeys=1)
            self._available = True
            logger.info(f"✅ S3 storage: connected to {self._bucket}")
            return True
        except Exception as e:
            self._available = False
            logger.warning(f"⚠️ S3 storage: unavailable ({e})")
            return False
    
    def _get_client(self):
        """Get or create boto3 S3 client."""
        if self._client is not None:
            return self._client
        
        try:
            import boto3
            from botocore.config import Config
        except ImportError:
            raise ImportError(
                "boto3 is required for S3 storage. "
                "Install it with: pip install boto3"
            )
        
        # Auto-detect endpoint format:
        # If endpoint contains bucket name as subdomain (e.g., ueditor-sunne.s3.cn-south-1.qiniucs.com),
        # strip it out and use virtual-hosted style addressing
        endpoint = self._endpoint
        region = None
        
        from urllib.parse import urlparse
        parsed = urlparse(endpoint)
        hostname = parsed.hostname or ""
        
        # Check if hostname starts with bucket name (virtual-hosted format)
        bucket_prefix = f"{self._bucket}."
        if hostname.startswith(bucket_prefix):
            # Strip bucket name from endpoint: ueditor-sunne.s3.cn-south-1.qiniucs.com → s3.cn-south-1.qiniucs.com
            base_host = hostname[len(bucket_prefix):]
            endpoint = f"{parsed.scheme}://{base_host}"
            logger.debug(f"S3 endpoint normalized: {self._endpoint} → {endpoint}")
            
            # Try to extract region from hostname (e.g., s3.cn-south-1.qiniucs.com → cn-south-1)
            parts = base_host.split(".")
            if len(parts) >= 3 and parts[0] == "s3":
                region = parts[1]
        
        client_kwargs = {
            "endpoint_url": endpoint,
            "aws_access_key_id": self._access_key,
            "aws_secret_access_key": self._secret_key,
            "config": Config(
                s3={"addressing_style": "virtual"},
                signature_version="s3v4",
            ),
        }
        if region:
            client_kwargs["region_name"] = region
        
        self._client = boto3.client("s3", **client_kwargs)
        return self._client
    
    def _get_store_path(self, file_ext: str) -> str:
        """Determine S3 store path prefix based on file type."""
        video_exts = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv"}
        if file_ext.lower() in video_exts:
            return self._video_store_path
        return self._image_store_path
    
    def _generate_s3_key(self, local_path: str, store_prefix: Optional[str] = None) -> str:
        """
        Generate S3 object key from local file path.
        
        Strategy: {store_prefix}/{date}/{filename}
        Example:  plus_ai/video/20260409/task_xxxx_final.mp4
        """
        p = Path(local_path)
        ext = p.suffix
        
        if store_prefix is None:
            store_prefix = self._get_store_path(ext)
        
        date_str = datetime.now().strftime("%Y%m%d")
        
        # Use parent directory name + filename to maintain uniqueness
        # e.g., output/20260409_xxxx/final.mp4 → 20260409_xxxx_final.mp4
        parent = p.parent.name
        if parent and parent != "output":
            filename = f"{parent}_{p.name}"
        else:
            filename = p.name
        
        return f"{store_prefix}/{date_str}/{filename}"
    
    def _get_content_type(self, file_path: str) -> str:
        """Determine content type from file extension."""
        content_type, _ = mimetypes.guess_type(file_path)
        return content_type or "application/octet-stream"
    
    def upload_file(
        self,
        local_path: str,
        s3_key: Optional[str] = None,
        cleanup_local: bool = False,
    ) -> str:
        """
        Upload a file to S3.
        
        Args:
            local_path: Local file path to upload
            s3_key: S3 object key (auto-generated if None)
            cleanup_local: If True, delete local file after successful upload
            
        Returns:
            Public URL of the uploaded file
            
        Raises:
            FileNotFoundError: If local file doesn't exist
            RuntimeError: If upload fails
        """
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"File not found: {local_path}")
        
        if s3_key is None:
            s3_key = self._generate_s3_key(local_path)
        
        content_type = self._get_content_type(local_path)
        file_size = os.path.getsize(local_path)
        
        logger.info(f"[S3] Uploading: {local_path} → s3://{self._bucket}/{s3_key} "
                     f"({file_size / 1024 / 1024:.1f} MB)")
        
        try:
            client = self._get_client()
            client.upload_file(
                local_path,
                self._bucket,
                s3_key,
                ExtraArgs={
                    "ContentType": content_type,
                },
            )
            
            public_url = f"{self._public_base_url}/{s3_key}"
            logger.info(f"[S3] Upload complete: {public_url}")
            
            # Cleanup local file after successful upload
            if cleanup_local:
                try:
                    os.remove(local_path)
                    logger.info(f"[S3] Local file cleaned up: {local_path}")
                except OSError as e:
                    logger.warning(f"[S3] Failed to cleanup local file: {e}")
            
            return public_url
            
        except Exception as e:
            logger.error(f"[S3] Upload failed: {e}")
            raise RuntimeError(f"S3 upload failed: {e}")
    
    def upload_video(self, local_path: str, cleanup_local: bool = True) -> str:
        """
        Upload a video file to S3 (convenience method).
        
        Args:
            local_path: Local video file path
            cleanup_local: If True, delete local file after upload (default: True)
            
        Returns:
            Public URL
        """
        s3_key = self._generate_s3_key(local_path, self._video_store_path)
        return self.upload_file(local_path, s3_key=s3_key, cleanup_local=cleanup_local)
    
    def upload_image(self, local_path: str, cleanup_local: bool = True) -> str:
        """
        Upload an image file to S3 (convenience method).
        
        Args:
            local_path: Local image file path
            cleanup_local: If True, delete local file after upload (default: True)
            
        Returns:
            Public URL
        """
        s3_key = self._generate_s3_key(local_path, self._image_store_path)
        return self.upload_file(local_path, s3_key=s3_key, cleanup_local=cleanup_local)
    
    def upload_task_dir(self, task_dir: str, cleanup_local: bool = True) -> dict:
        """
        Upload all files in a task directory to S3.
        
        Walks the task directory, uploads all files, and optionally removes
        the entire local directory.
        
        Args:
            task_dir: Path to task output directory
            cleanup_local: If True, remove entire local directory after upload
            
        Returns:
            Dict mapping original filenames to public URLs
        """
        task_dir_path = Path(task_dir)
        if not task_dir_path.exists():
            raise FileNotFoundError(f"Task directory not found: {task_dir}")
        
        uploaded = {}
        
        for file_path in task_dir_path.rglob("*"):
            if not file_path.is_file():
                continue
            
            try:
                url = self.upload_file(str(file_path), cleanup_local=False)
                uploaded[file_path.name] = url
            except Exception as e:
                logger.warning(f"[S3] Skipping file {file_path}: {e}")
        
        # Cleanup entire directory
        if cleanup_local and uploaded:
            try:
                import shutil
                shutil.rmtree(task_dir)
                logger.info(f"[S3] Task directory cleaned up: {task_dir}")
            except OSError as e:
                logger.warning(f"[S3] Failed to cleanup task directory: {e}")
        
        return uploaded
    
    def download(self, s3_url: str, local_path: str) -> str:
        """
        Download a file from S3 to local path.
        
        Args:
            s3_url: S3 public URL or s3 key
            local_path: Local destination path
            
        Returns:
            Local file path
        """
        # Extract S3 key from public URL
        if s3_url.startswith(self._public_base_url):
            s3_key = s3_url[len(self._public_base_url):].lstrip("/")
        elif s3_url.startswith("http"):
            # Parse URL and extract path
            parsed = urlparse(s3_url)
            s3_key = parsed.path.lstrip("/")
        else:
            s3_key = s3_url
        
        # Ensure local directory exists
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        
        logger.info(f"[S3] Downloading: s3://{self._bucket}/{s3_key} → {local_path}")
        
        try:
            client = self._get_client()
            client.download_file(self._bucket, s3_key, local_path)
            logger.info(f"[S3] Download complete: {local_path}")
            return local_path
        except Exception as e:
            logger.error(f"[S3] Download failed: {e}")
            raise RuntimeError(f"S3 download failed: {e}")
    
    def ensure_local(self, s3_url: str, local_path: str) -> str:
        """
        Ensure a file exists locally, downloading from S3 if needed.
        
        Args:
            s3_url: S3 public URL
            local_path: Expected local path
            
        Returns:
            Local file path (existing or freshly downloaded)
        """
        if os.path.exists(local_path):
            return local_path
        
        return self.download(s3_url, local_path)
    
    def file_exists(self, s3_key: str) -> bool:
        """Check if a file exists in S3."""
        try:
            client = self._get_client()
            client.head_object(Bucket=self._bucket, Key=s3_key)
            return True
        except Exception:
            return False


# ==================== Global Singleton ====================

_s3_instance: Optional[S3Storage] = None


def get_s3_storage() -> S3Storage:
    """
    Get the global S3 storage instance.
    
    Returns:
        S3Storage singleton instance
    """
    global _s3_instance
    if _s3_instance is None:
        _s3_instance = S3Storage()
    return _s3_instance
