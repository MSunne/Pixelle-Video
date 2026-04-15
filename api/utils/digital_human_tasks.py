"""Helpers for digital human task deduplication and backpressure."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def build_digital_human_request_fingerprint(payload: dict[str, Any]) -> str:
    """Build a stable fingerprint for async digital human task deduplication."""
    normalized = {
        "character_assets": payload.get("character_assets") or [],
        "mode": payload.get("mode") or "customize",
        "goods_assets": payload.get("goods_assets") or [],
        "goods_title": payload.get("goods_title") or "",
        "goods_text": payload.get("goods_text") or "",
        "source": payload.get("source") or "runninghub",
        "tts_voice": payload.get("tts_voice") or "",
        "tts_speed": payload.get("tts_speed"),
        "tts_inference_mode": payload.get("tts_inference_mode") or "comfyui",
        "tts_workflow": payload.get("tts_workflow") or "",
        "ref_audio": payload.get("ref_audio") or "",
        "llm_model": payload.get("llm_model") or "",
        "subtitle_enabled": bool(payload.get("subtitle_enabled", True)),
        "subtitle_output": payload.get("subtitle_output") or "both",
        "subtitle_language": payload.get("subtitle_language") or "zh_en",
    }
    normalized_json = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(normalized_json.encode("utf-8")).hexdigest()
