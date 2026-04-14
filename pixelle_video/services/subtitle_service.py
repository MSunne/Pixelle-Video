"""
Subtitle generation service for digital human videos.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import ffmpeg
from loguru import logger
from PIL import ImageFont
from pydantic import BaseModel

from pixelle_video.services.tts_service import TTSBoundary


@dataclass
class AlignmentUnit:
    """Timed ASR/alignment fragment."""
    start: float
    end: float
    text: str


@dataclass
class SubtitleCue:
    """Single subtitle cue."""
    index: int
    start: float
    end: float
    zh_text: str
    en_text: str = ""


@dataclass
class SubtitleArtifacts:
    """Generated subtitle files."""
    cues: list[SubtitleCue]
    srt_path: str
    ass_path: str
    json_path: str


@dataclass
class ResolvedFont:
    """Resolved font metadata for ASS rendering and width measurement."""
    font_name: str
    fontsdir: str
    font_path: str


@dataclass
class SubtitleLayoutProfile:
    """Layout profile derived from the actual video aspect ratio."""
    name: str
    zh_font: int
    en_font: int
    min_zh_font: int
    min_en_font: int
    margin_lr: int
    margin_v: int
    max_zh_lines: int = 2
    max_en_lines: int = 2


@dataclass
class RenderedSubtitleCue:
    """Cue prepared for SRT/ASS rendering."""
    cue: SubtitleCue
    zh_lines: list[str]
    en_lines: list[str]
    zh_font: int
    en_font: int


class SubtitleTranslationResponse(BaseModel):
    translations: list[str]


class SubtitleService:
    """Build bilingual subtitle assets for digital human videos."""

    MAX_ZH_CUE_CHARS = 26
    ASR_MODEL_SIZE = "small"
    FONT_SHRINK_STEP = 2
    MAX_LAYOUT_SPLIT_DEPTH = 4

    _MAJOR_BREAK_RE = re.compile(r"[^。！？；!?;]+[。！？；!?;]*")
    _MINOR_BREAK_RE = re.compile(r"[^，：、,]+[，：、,]*")
    _NORMALIZE_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")
    _LAYOUT_MINOR_BREAKS = "，：、,"
    _LAYOUT_MAJOR_BREAKS = "。！？；!?;"

    def __init__(self, core):
        self.core = core
        self._asr_model = None
        self._asr_model_key: Optional[tuple[str, str]] = None
        self._pil_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    async def generate_subtitle_assets(
        self,
        text: str,
        audio_path: str,
        video_path: str,
        output_dir: str,
        subtitle_language: str = "zh_en",
        llm_model: Optional[str] = None,
        tts_boundaries: Optional[list[TTSBoundary]] = None,
    ) -> SubtitleArtifacts:
        """Generate SRT/ASS/JSON subtitle assets."""
        normalized_text = self._normalize_input_text(text)
        if not normalized_text:
            raise ValueError("Subtitle text cannot be empty")

        cue_texts, sentence_groups = self._build_cue_groups(normalized_text)
        en_texts = await self._build_translations(
            cue_texts,
            subtitle_language=subtitle_language,
            llm_model=llm_model,
        )

        audio_duration = self._probe_audio_duration(audio_path)
        if tts_boundaries:
            cues = self._align_from_boundaries(sentence_groups, en_texts, tts_boundaries, audio_duration)
        else:
            units = self._transcribe_alignment_units(audio_path)
            cues = self._align_from_units(cue_texts, en_texts, units, audio_duration)

        width, height = self._probe_video_resolution(video_path)
        font = self._resolve_font()
        profile = self._build_layout_profile(width, height)
        cues, rendered_cues = await self._fit_cues_for_layout(
            cues,
            width=width,
            height=height,
            font=font,
            profile=profile,
            subtitle_language=subtitle_language,
            llm_model=llm_model,
        )

        output_root = Path(output_dir)
        srt_path = str(output_root / "subtitles.srt")
        ass_path = str(output_root / "subtitles.ass")
        json_path = str(output_root / "subtitles.json")

        self._write_text(srt_path, self._render_srt(rendered_cues))
        self._write_text(ass_path, self._render_ass(rendered_cues, width, height, font.font_name, profile))
        self._write_json(
            json_path,
            {
                "cues": [asdict(cue) for cue in cues],
                "fontsdir": font.fontsdir,
                "font_name": font.font_name,
                "layout_profile": asdict(profile),
            },
        )

        return SubtitleArtifacts(
            cues=cues,
            srt_path=srt_path,
            ass_path=ass_path,
            json_path=json_path,
        )

    def get_fontsdir(self) -> str:
        """Resolve the fonts directory used for ASS burning."""
        return self._resolve_font().fontsdir

    def _normalize_input_text(self, text: str) -> str:
        text = (text or "").strip()
        text = re.sub(r"\s+", "", text)
        return text

    def _build_cue_groups(self, text: str) -> tuple[list[str], list[list[str]]]:
        sentence_groups: list[list[str]] = []
        all_cues: list[str] = []
        for sentence in self._split_sentences(text):
            group = self._split_sentence(sentence)
            if not group:
                continue
            sentence_groups.append(group)
            all_cues.extend(group)
        return all_cues, sentence_groups

    def _split_sentences(self, text: str) -> list[str]:
        sentences = [part.strip() for part in self._MAJOR_BREAK_RE.findall(text) if part.strip()]
        return sentences or [text]

    def _split_sentence(self, sentence: str) -> list[str]:
        sentence = sentence.strip()
        if not sentence:
            return []

        chunks = [part.strip() for part in self._MINOR_BREAK_RE.findall(sentence) if part.strip()]
        if not chunks:
            chunks = [sentence]

        cues: list[str] = []
        current = ""

        for chunk in chunks:
            if len(self._normalize_for_alignment(chunk)) > self.MAX_ZH_CUE_CHARS:
                if current:
                    cues.append(current)
                    current = ""
                cues.extend(self._split_long_chunk(chunk))
                continue

            candidate = f"{current}{chunk}" if current else chunk
            if len(self._normalize_for_alignment(candidate)) <= self.MAX_ZH_CUE_CHARS:
                current = candidate
            else:
                if current:
                    cues.append(current)
                current = chunk

        if current:
            cues.append(current)

        return cues or [sentence]

    def _split_long_chunk(self, chunk: str) -> list[str]:
        pieces: list[str] = []
        current = ""
        for char in chunk:
            candidate = f"{current}{char}"
            if len(self._normalize_for_alignment(candidate)) <= self.MAX_ZH_CUE_CHARS:
                current = candidate
            else:
                if current:
                    pieces.append(current)
                current = char
        if current:
            pieces.append(current)
        return pieces

    async def _build_translations(
        self,
        cue_texts: list[str],
        subtitle_language: str,
        llm_model: Optional[str],
    ) -> list[str]:
        if subtitle_language != "zh_en":
            return [""] * len(cue_texts)

        prompt = (
            "Translate each Chinese subtitle cue into concise, natural, subtitle-ready English.\n"
            "Keep each translation brief and conversational, ideally within 12 English words.\n"
            "Keep the order unchanged. Preserve brand names, numbers, and product terms.\n"
            "Do not merge or split cues. Return JSON only.\n\n"
            f"Cues:\n{json.dumps(cue_texts, ensure_ascii=False)}"
        )

        try:
            kwargs = {"response_type": SubtitleTranslationResponse}
            if llm_model:
                kwargs["model"] = llm_model
            response = await self.core.llm(prompt, **kwargs)
            translations = [item.strip() for item in response.translations]
            if len(translations) != len(cue_texts):
                raise ValueError(
                    f"Expected {len(cue_texts)} translations, got {len(translations)}"
                )
            return translations
        except Exception as exc:
            logger.warning(f"[Subtitle] Translation failed, falling back to Chinese only: {exc}")
            return [""] * len(cue_texts)

    def _align_from_boundaries(
        self,
        sentence_groups: list[list[str]],
        en_texts: list[str],
        boundaries: list[TTSBoundary],
        audio_duration: float,
    ) -> list[SubtitleCue]:
        sentence_boundaries = [item for item in boundaries if item.type == "SentenceBoundary"]
        if len(sentence_boundaries) != len(sentence_groups):
            logger.warning(
                "[Subtitle] Sentence boundary count mismatch, using proportional timing fallback"
            )
            cue_texts = [cue for group in sentence_groups for cue in group]
            return self._fallback_proportional_alignment(cue_texts, en_texts, audio_duration)

        cues: list[SubtitleCue] = []
        en_idx = 0
        cue_idx = 1

        for group, boundary in zip(sentence_groups, sentence_boundaries):
            start = boundary.start_seconds
            end = boundary.end_seconds
            spans = self._distribute_spans(group, start, end)
            for cue_text, cue_start, cue_end in spans:
                cues.append(
                    SubtitleCue(
                        index=cue_idx,
                        start=cue_start,
                        end=cue_end,
                        zh_text=cue_text,
                        en_text=en_texts[en_idx],
                    )
                )
                cue_idx += 1
                en_idx += 1

        return self._normalize_cue_boundaries(cues, audio_duration)

    def _align_from_units(
        self,
        cue_texts: list[str],
        en_texts: list[str],
        units: list[AlignmentUnit],
        audio_duration: float,
    ) -> list[SubtitleCue]:
        char_spans = self._expand_units_to_char_spans(units)
        if not char_spans:
            logger.warning("[Subtitle] No ASR timing units found, using proportional timing fallback")
            return self._fallback_proportional_alignment(cue_texts, en_texts, audio_duration)

        cues: list[SubtitleCue] = []
        char_idx = 0

        for idx, (cue_text, en_text) in enumerate(zip(cue_texts, en_texts), start=1):
            target_chars = max(1, len(self._normalize_for_alignment(cue_text)))
            cue_chars = char_spans[char_idx:char_idx + target_chars]
            if not cue_chars:
                break
            cues.append(
                SubtitleCue(
                    index=idx,
                    start=cue_chars[0][0],
                    end=cue_chars[-1][1],
                    zh_text=cue_text,
                    en_text=en_text,
                )
            )
            char_idx += target_chars

        if len(cues) != len(cue_texts):
            logger.warning("[Subtitle] ASR alignment incomplete, using proportional timing fallback")
            return self._fallback_proportional_alignment(cue_texts, en_texts, audio_duration)

        return self._normalize_cue_boundaries(cues, audio_duration)

    def _fallback_proportional_alignment(
        self,
        cue_texts: list[str],
        en_texts: list[str],
        audio_duration: float,
    ) -> list[SubtitleCue]:
        spans = self._distribute_spans(cue_texts, 0.0, audio_duration)
        return [
            SubtitleCue(
                index=idx,
                start=start,
                end=end,
                zh_text=text,
                en_text=en_texts[idx - 1],
            )
            for idx, (text, start, end) in enumerate(spans, start=1)
        ]

    def _distribute_spans(
        self,
        texts: list[str],
        start: float,
        end: float,
    ) -> list[tuple[str, float, float]]:
        total = max(end - start, 0.05)
        weights = [max(1, len(self._normalize_for_alignment(text))) for text in texts]
        weight_sum = sum(weights)
        cursor = start
        spans: list[tuple[str, float, float]] = []

        for idx, (text, weight) in enumerate(zip(texts, weights)):
            if idx == len(texts) - 1:
                span_end = end
            else:
                span_end = cursor + total * (weight / weight_sum)
            spans.append((text, cursor, span_end))
            cursor = span_end

        return spans

    def _normalize_cue_boundaries(self, cues: list[SubtitleCue], audio_duration: float) -> list[SubtitleCue]:
        min_gap = 0.05
        for idx, cue in enumerate(cues):
            cue.start = max(0.0, cue.start)
            cue.end = max(cue.start + min_gap, cue.end)
            if idx == len(cues) - 1:
                cue.end = min(max(cue.end, audio_duration), audio_duration or cue.end)
            else:
                next_start = max(cue.end + min_gap, cues[idx + 1].start)
                cues[idx + 1].start = next_start if next_start < audio_duration else cues[idx + 1].start
        if cues and audio_duration:
            cues[-1].end = max(cues[-1].end, audio_duration)
        return cues

    def _expand_units_to_char_spans(self, units: list[AlignmentUnit]) -> list[tuple[float, float, str]]:
        spans: list[tuple[float, float, str]] = []
        for unit in units:
            normalized = self._normalize_for_alignment(unit.text)
            if not normalized:
                continue
            duration = max(unit.end - unit.start, 0.05)
            per_char = duration / len(normalized)
            for idx, char in enumerate(normalized):
                spans.append(
                    (
                        unit.start + idx * per_char,
                        unit.start + (idx + 1) * per_char,
                        char,
                    )
                )
        return spans

    def _transcribe_alignment_units(self, audio_path: str) -> list[AlignmentUnit]:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is required for ComfyUI subtitle alignment. "
                "Install it with `uv sync` or `pip install faster-whisper`."
            ) from exc

        device, compute_type = self._pick_asr_runtime()
        model_key = (device, compute_type)
        if self._asr_model is None or self._asr_model_key != model_key:
            self._asr_model = WhisperModel(
                self.ASR_MODEL_SIZE,
                device=device,
                compute_type=compute_type,
            )
            self._asr_model_key = model_key

        logger.info(
            f"[Subtitle] Running faster-whisper alignment model={self.ASR_MODEL_SIZE} "
            f"device={device} compute_type={compute_type}"
        )
        segments, _ = self._asr_model.transcribe(
            audio_path,
            beam_size=1,
            word_timestamps=True,
            vad_filter=True,
            language="zh",
        )

        units: list[AlignmentUnit] = []
        for segment in segments:
            words = getattr(segment, "words", None) or []
            if words:
                for word in words:
                    if word.start is None or word.end is None:
                        continue
                    units.append(
                        AlignmentUnit(
                            start=float(word.start),
                            end=float(word.end),
                            text=word.word,
                        )
                    )
            else:
                units.append(
                    AlignmentUnit(
                        start=float(segment.start),
                        end=float(segment.end),
                        text=segment.text,
                    )
                )
        return units

    def _pick_asr_runtime(self) -> tuple[str, str]:
        if shutil.which("nvidia-smi"):
            return "cuda", "float16"
        return "cpu", "int8"

    def _probe_audio_duration(self, audio_path: str) -> float:
        probe = ffmpeg.probe(audio_path)
        return float(probe["format"]["duration"])

    def _probe_video_resolution(self, video_path: str) -> tuple[int, int]:
        probe = ffmpeg.probe(video_path)
        stream = next(item for item in probe["streams"] if item["codec_type"] == "video")
        return int(stream["width"]), int(stream["height"])

    async def _fit_cues_for_layout(
        self,
        cues: list[SubtitleCue],
        width: int,
        height: int,
        font: ResolvedFont,
        profile: SubtitleLayoutProfile,
        subtitle_language: str,
        llm_model: Optional[str],
    ) -> tuple[list[SubtitleCue], list[RenderedSubtitleCue]]:
        pending: list[tuple[SubtitleCue, int]] = [(cue, 0) for cue in cues]
        rendered: list[RenderedSubtitleCue] = []

        while pending:
            cue, depth = pending.pop(0)
            layout = self._layout_cue(cue, width, font.font_path, profile)
            if layout is not None:
                rendered.append(layout)
                continue

            split_texts = self._split_zh_text_for_layout(cue.zh_text)
            if depth >= self.MAX_LAYOUT_SPLIT_DEPTH or len(split_texts) <= 1:
                rendered.append(self._render_fallback_layout(cue, width, font.font_path, profile))
                continue

            split_translations = await self._build_translations(
                split_texts,
                subtitle_language=subtitle_language,
                llm_model=llm_model,
            )
            split_spans = self._distribute_spans(split_texts, cue.start, cue.end)
            split_cues = [
                SubtitleCue(
                    index=0,
                    start=start,
                    end=end,
                    zh_text=text,
                    en_text=split_translations[idx],
                )
                for idx, (text, start, end) in enumerate(split_spans)
            ]
            pending = [(item, depth + 1) for item in split_cues] + pending

        normalized_cues: list[SubtitleCue] = []
        normalized_rendered: list[RenderedSubtitleCue] = []
        for idx, item in enumerate(rendered, start=1):
            item.cue.index = idx
            normalized_cues.append(item.cue)
            normalized_rendered.append(item)

        if normalized_cues:
            self._normalize_cue_boundaries(normalized_cues, normalized_cues[-1].end)

        return normalized_cues, normalized_rendered

    def _build_layout_profile(self, width: int, height: int) -> SubtitleLayoutProfile:
        if height / max(width, 1) >= 1.4:
            return SubtitleLayoutProfile(
                name="portrait",
                zh_font=self._clamp(round(height * 0.032), 36, 60),
                en_font=self._clamp(round(self._clamp(round(height * 0.032), 36, 60) * 0.70), 24, 42),
                min_zh_font=34,
                min_en_font=24,
                margin_lr=self._clamp(round(width * 0.08), 48, 96),
                margin_v=self._clamp(round(height * 0.045), 44, 84),
            )
        if width / max(height, 1) >= 1.4:
            zh_font = self._clamp(round(height * 0.036), 30, 44)
            return SubtitleLayoutProfile(
                name="landscape",
                zh_font=zh_font,
                en_font=self._clamp(round(zh_font * 0.72), 22, 32),
                min_zh_font=28,
                min_en_font=20,
                margin_lr=self._clamp(round(width * 0.09), 80, 180),
                margin_v=self._clamp(round(height * 0.05), 40, 72),
            )

        zh_font = self._clamp(round(height * 0.034), 32, 50)
        return SubtitleLayoutProfile(
            name="square",
            zh_font=zh_font,
            en_font=self._clamp(round(zh_font * 0.70), 22, 36),
            min_zh_font=30,
            min_en_font=22,
            margin_lr=self._clamp(round(width * 0.085), 56, 120),
            margin_v=self._clamp(round(height * 0.048), 40, 78),
        )

    def _layout_cue(
        self,
        cue: SubtitleCue,
        width: int,
        font_path: str,
        profile: SubtitleLayoutProfile,
    ) -> Optional[RenderedSubtitleCue]:
        available_width = max(160, width - profile.margin_lr * 2)
        zh_font = profile.zh_font
        en_font = profile.en_font

        while True:
            zh_lines = self._wrap_zh_text_by_width(cue.zh_text, font_path, zh_font, available_width)
            en_lines = (
                self._wrap_en_text_by_width(cue.en_text, font_path, en_font, available_width)
                if cue.en_text
                else []
            )
            zh_ok = self._lines_fit(zh_lines, font_path, zh_font, available_width, profile.max_zh_lines)
            en_ok = self._lines_fit(en_lines, font_path, en_font, available_width, profile.max_en_lines)
            if zh_ok and en_ok:
                return RenderedSubtitleCue(
                    cue=cue,
                    zh_lines=zh_lines,
                    en_lines=en_lines,
                    zh_font=zh_font,
                    en_font=en_font,
                )

            can_shrink_zh = zh_font > profile.min_zh_font
            can_shrink_en = bool(cue.en_text and en_font > profile.min_en_font)
            if not can_shrink_zh and not can_shrink_en:
                return None

            zh_font = max(profile.min_zh_font, zh_font - self.FONT_SHRINK_STEP)
            if cue.en_text:
                en_font = max(profile.min_en_font, en_font - self.FONT_SHRINK_STEP)

    def _render_fallback_layout(
        self,
        cue: SubtitleCue,
        width: int,
        font_path: str,
        profile: SubtitleLayoutProfile,
    ) -> RenderedSubtitleCue:
        available_width = max(160, width - profile.margin_lr * 2)
        zh_lines = self._wrap_zh_text_by_width(cue.zh_text, font_path, profile.min_zh_font, available_width)
        en_lines = (
            self._wrap_en_text_by_width(cue.en_text, font_path, profile.min_en_font, available_width)
            if cue.en_text
            else []
        )
        return RenderedSubtitleCue(
            cue=cue,
            zh_lines=zh_lines,
            en_lines=en_lines,
            zh_font=profile.min_zh_font,
            en_font=profile.min_en_font,
        )

    def _split_zh_text_for_layout(self, text: str) -> list[str]:
        stripped = (text or "").strip()
        if len(self._normalize_for_alignment(stripped)) <= 1:
            return [stripped]

        split_index = self._find_layout_split_index(stripped)
        if split_index <= 0 or split_index >= len(stripped):
            return [stripped]

        left = stripped[:split_index].strip()
        right = stripped[split_index:].strip()
        if not left or not right:
            return [stripped]
        return [left, right]

    def _find_layout_split_index(self, text: str) -> int:
        midpoint = len(text) // 2
        for separators in (self._LAYOUT_MINOR_BREAKS, self._LAYOUT_MAJOR_BREAKS):
            candidates = [
                idx + 1
                for idx, char in enumerate(text[:-1])
                if char in separators
            ]
            if candidates:
                return min(candidates, key=lambda pos: abs(pos - midpoint))

        normalized = self._normalize_for_alignment(text)
        target = max(1, len(normalized) // 2)
        count = 0
        for idx, char in enumerate(text[:-1], start=1):
            if self._normalize_for_alignment(char):
                count += 1
            if count >= target:
                return idx
        return midpoint

    def _wrap_zh_text_by_width(
        self,
        text: str,
        font_path: str,
        font_size: int,
        max_width: int,
    ) -> list[str]:
        if not text:
            return []

        lines: list[str] = []
        current = ""
        for char in text:
            candidate = f"{current}{char}"
            if current and self._measure_text_width(candidate, font_path, font_size) > max_width:
                lines.append(current)
                current = char
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines

    def _wrap_en_text_by_width(
        self,
        text: str,
        font_path: str,
        font_size: int,
        max_width: int,
    ) -> list[str]:
        if not text:
            return []

        words = text.split()
        if not words:
            return [text]

        lines: list[str] = []
        current = ""
        for word in words:
            for segment in self._split_token_by_width(word, font_path, font_size, max_width):
                candidate = f"{current} {segment}".strip() if current else segment
                if current and self._measure_text_width(candidate, font_path, font_size) > max_width:
                    lines.append(current)
                    current = segment
                else:
                    current = candidate
        if current:
            lines.append(current)
        return lines

    def _split_token_by_width(
        self,
        token: str,
        font_path: str,
        font_size: int,
        max_width: int,
    ) -> list[str]:
        if self._measure_text_width(token, font_path, font_size) <= max_width:
            return [token]

        pieces: list[str] = []
        current = ""
        for char in token:
            candidate = f"{current}{char}"
            if current and self._measure_text_width(candidate, font_path, font_size) > max_width:
                pieces.append(current)
                current = char
            else:
                current = candidate
        if current:
            pieces.append(current)
        return pieces or [token]

    def _lines_fit(
        self,
        lines: list[str],
        font_path: str,
        font_size: int,
        max_width: int,
        max_lines: int,
    ) -> bool:
        if not lines:
            return True
        if len(lines) > max_lines:
            return False
        return all(self._measure_text_width(line, font_path, font_size) <= max_width for line in lines)

    def _measure_text_width(self, text: str, font_path: str, font_size: int) -> int:
        if not text:
            return 0

        font = self._load_measure_font(font_path, font_size)
        if font is None:
            return self._estimate_text_width(text, font_size)

        if hasattr(font, "getlength"):
            return int(font.getlength(text))

        bbox = font.getbbox(text)
        return int(bbox[2] - bbox[0])

    def _load_measure_font(self, font_path: str, font_size: int) -> Optional[ImageFont.FreeTypeFont]:
        cache_key = (font_path, font_size)
        if cache_key in self._pil_font_cache:
            return self._pil_font_cache[cache_key]

        try:
            font = ImageFont.truetype(font_path, size=font_size)
        except OSError as exc:
            logger.warning(f"[Subtitle] Failed to load font for width measurement: {font_path} ({exc})")
            return None

        self._pil_font_cache[cache_key] = font
        return font

    def _estimate_text_width(self, text: str, font_size: int) -> int:
        width = 0.0
        for char in text:
            if char.isspace():
                width += font_size * 0.33
            elif ord(char) < 128:
                width += font_size * 0.56
            else:
                width += font_size
        return int(round(width))

    def _resolve_font(self) -> ResolvedFont:
        resources_dir = Path.cwd() / "resources" / "fonts"
        if resources_dir.exists():
            for file in resources_dir.iterdir():
                if file.suffix.lower() in {".ttf", ".otf", ".ttc"}:
                    return ResolvedFont(
                        font_name=self._guess_font_name(file),
                        fontsdir=str(resources_dir),
                        font_path=str(file),
                    )

        fc_match = shutil.which("fc-match")
        if fc_match:
            result = subprocess.run(
                [fc_match, ":lang=zh-cn", "-f", "%{family}\n%{file}\n"],
                capture_output=True,
                text=True,
                check=False,
            )
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if len(lines) >= 2 and Path(lines[1]).exists():
                return ResolvedFont(
                    font_name=lines[0].split(",")[0],
                    fontsdir=str(Path(lines[1]).parent),
                    font_path=lines[1],
                )

        for candidate in (
            Path("/System/Library/Fonts/PingFang.ttc"),
            Path("/Library/Fonts/Arial Unicode.ttf"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
            Path("C:/Windows/Fonts/msyh.ttc"),
        ):
            if candidate.exists():
                return ResolvedFont(
                    font_name=self._guess_font_name(candidate),
                    fontsdir=str(candidate.parent),
                    font_path=str(candidate),
                )

        raise RuntimeError(
            "No CJK font found for subtitle burning. Install Noto CJK fonts or add fonts to resources/fonts."
        )

    def _guess_font_name(self, font_path: Path) -> str:
        lower_name = font_path.name.lower()
        if "pingfang" in lower_name:
            return "PingFang SC"
        if "noto" in lower_name:
            return "Noto Sans CJK SC"
        if "msyh" in lower_name or "yahei" in lower_name:
            return "Microsoft YaHei"
        if "wqy" in lower_name:
            return "WenQuanYi Zen Hei"
        return font_path.stem

    def _render_srt(self, rendered_cues: list[RenderedSubtitleCue]) -> str:
        blocks = []
        for rendered in rendered_cues:
            cue = rendered.cue
            lines = list(rendered.zh_lines)
            if rendered.en_lines:
                lines.extend(rendered.en_lines)
            blocks.append(
                f"{cue.index}\n"
                f"{self._format_srt_time(cue.start)} --> {self._format_srt_time(cue.end)}\n"
                f"{chr(10).join(lines)}"
            )
        return "\n\n".join(blocks) + "\n"

    def _render_ass(
        self,
        rendered_cues: list[RenderedSubtitleCue],
        width: int,
        height: int,
        font_name: str,
        profile: SubtitleLayoutProfile,
    ) -> str:
        header = (
            "[Script Info]\n"
            "ScriptType: v4.00+\n"
            f"PlayResX: {width}\n"
            f"PlayResY: {height}\n"
            "WrapStyle: 0\n"
            "ScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Default,{font_name},{profile.zh_font},&H00FFFFFF,&H00FFFFFF,&H00000000,&H66000000,"
            f"1,0,0,0,100,100,0,0,4,2,1,2,{profile.margin_lr},{profile.margin_lr},{profile.margin_v},1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )
        events = []
        for rendered in rendered_cues:
            cue = rendered.cue
            zh = self._escape_ass_text("\n".join(rendered.zh_lines))
            text = f"{{\\fs{rendered.zh_font}}}{zh}"
            if rendered.en_lines:
                en = self._escape_ass_text("\n".join(rendered.en_lines))
                text = f"{text}\\N{{\\fs{rendered.en_font}}}{en}"
            events.append(
                f"Dialogue: 0,{self._format_ass_time(cue.start)},{self._format_ass_time(cue.end)},"
                f"Default,,0,0,0,,{text}"
            )
        return header + "\n".join(events) + "\n"

    def _escape_ass_text(self, text: str) -> str:
        return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")

    def _normalize_for_alignment(self, text: str) -> str:
        return self._NORMALIZE_RE.sub("", text or "")

    def _clamp(self, value: int, minimum: int, maximum: int) -> int:
        return max(minimum, min(value, maximum))

    def _format_srt_time(self, seconds: float) -> str:
        total_ms = max(0, int(round(seconds * 1000)))
        hours, rem = divmod(total_ms, 3_600_000)
        minutes, rem = divmod(rem, 60_000)
        secs, millis = divmod(rem, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def _format_ass_time(self, seconds: float) -> str:
        total_cs = max(0, int(round(seconds * 100)))
        hours, rem = divmod(total_cs, 360_000)
        minutes, rem = divmod(rem, 6_000)
        secs, cs = divmod(rem, 100)
        return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"

    def _write_text(self, output_path: str, content: str) -> None:
        Path(output_path).write_text(content, encoding="utf-8")

    def _write_json(self, output_path: str, payload: dict) -> None:
        Path(output_path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
