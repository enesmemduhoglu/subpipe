"""Aşamalar arasında dolaşan veri tipleri.

Her aşama JSON'a serialize edilip work/<hash>/ altına yazılır, sonraki aşama
oradan okur. Böylece aşamalar tek tek çalıştırılabilir.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Word(BaseModel):
    word: str
    start: float
    end: float


class VideoMeta(BaseModel):
    width: int
    height: int
    fps: float
    duration: float


class Sentence(BaseModel):
    """Kelimelerden gruplanmış tam cümle. Çeviri birimi budur."""

    id: int
    text: str
    start: float
    end: float
    words: list[Word]


class Cue(BaseModel):
    """Ekranda tek seferde görünecek altyazı bloğu (tek dil)."""

    start: float
    end: float
    lines: list[str]
    sentence_id: int

    @property
    def text(self) -> str:
        return " ".join(self.lines)

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def cps(self) -> float:
        d = self.duration
        return len(self.text) / d if d > 0 else float("inf")


class BilingualCue(BaseModel):
    """EN ve TR aynı zaman aralığında — hardsub için tek zaman çizelgesi."""

    start: float
    end: float
    en_lines: list[str]
    tr_lines: list[str] = Field(default_factory=list)
    sentence_id: int

    @property
    def duration(self) -> float:
        return self.end - self.start


class Dropped(BaseModel):
    """Halüsinasyon filtresinin attığı cümle — QA raporunda listelenir."""

    text: str
    start: float
    end: float
    reason: str


class TranscriptDoc(BaseModel):
    words: list[Word]
    meta: VideoMeta


class SegmentDoc(BaseModel):
    cues: list[Cue]
    sentences: list[Sentence]
    dropped: list[Dropped] = Field(default_factory=list)


class TranslateDoc(BaseModel):
    cues: list[BilingualCue]


class CaptionDoc(BaseModel):
    """Instagram post metni. out/<video>.caption.md olarak yazılır."""

    hook: str
    body: str
    cta: str
    hashtags: list[str] = Field(default_factory=list)
    alt_text: str
