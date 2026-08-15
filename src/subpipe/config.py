"""config.yaml şeması ve yükleyici."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class AudioConfig(BaseModel):
    sample_rate: int = 16000
    channels: int = 1


class TranscribeConfig(BaseModel):
    model: str = "fal-ai/wizper"
    chunk_level: str = "word"
    version: str = "3"


class CueConfig(BaseModel):
    max_chars_per_line: int = 30
    max_lines: int = 2
    min_duration: float = 0.85
    max_duration: float = 7.0
    max_cps_en: float = 17.0
    max_cps_tr: float = 20.0
    min_gap: float = 0.08
    sentence_pause: float = 0.6

    @property
    def capacity(self) -> int:
        """Tek cue'ya sığan maksimum karakter."""
        return self.max_chars_per_line * self.max_lines


class HallucinationConfig(BaseModel):
    patterns: list[str] = Field(default_factory=list)
    max_repeats: int = 3
    long_short_duration: float = 8.0
    long_short_words: int = 5


class TranslateConfig(BaseModel):
    model: str = "claude-opus-5"
    max_tokens: int = 16000
    batch_size: int = 40
    context_window: int = 3
    video_context: str = ""
    tone: str = ""
    glossary: dict[str, str] = Field(default_factory=dict)


class LangStyle(BaseModel):
    fontsize: int
    primary_colour: str = "&H00FFFFFF"
    outline_colour: str = "&H00000000"
    bold: int = 0
    outline: float = 3.0
    shadow: float = 1.0


class StyleConfig(BaseModel):
    font_name: str = "Arial"
    en: LangStyle = LangStyle(fontsize=62, bold=1, outline=3.5)
    tr: LangStyle = LangStyle(fontsize=50, primary_colour="&H00A0E0FF")
    margin_l: int = 80
    margin_r: int = 80
    margin_v: int = 380


class RenderConfig(BaseModel):
    crf: int = 18
    preset: str = "medium"
    preview_seconds: int = 60
    preview_encoder: str = "h264_nvenc"
    preview_cq: int = 23


class Config(BaseModel):
    source_language: str = "en"
    target_language: str = "tr"
    primary_language: str = "en"
    audio: AudioConfig = AudioConfig()
    transcribe: TranscribeConfig = TranscribeConfig()
    cues: CueConfig = CueConfig()
    hallucination: HallucinationConfig = HallucinationConfig()
    translate: TranslateConfig = TranslateConfig()
    style: StyleConfig = StyleConfig()
    render: RenderConfig = RenderConfig()


def _drop_nones(obj: object) -> object:
    """Tamamı yoruma alınmış YAML bölümleri None olarak gelir; onları at ki
    pydantic varsayılanları devreye girsin."""
    if isinstance(obj, dict):
        return {k: _drop_nones(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_drop_nones(v) for v in obj if v is not None]
    return obj


def load_config(path: Path | str = "config.yaml") -> Config:
    p = Path(path)
    if not p.exists():
        return Config()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return Config.model_validate(_drop_nones(data))
