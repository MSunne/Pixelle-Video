"""
Storage Package

Provides unified storage abstraction with S3 support.
"""

from pixelle_video.storage.s3 import S3Storage, get_s3_storage

__all__ = [
    "S3Storage",
    "get_s3_storage",
]
