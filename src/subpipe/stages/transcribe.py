"""Aşama 2: fal.ai Wizper ile transkripsiyon.

chunk_level="word" ŞART — varsayılan "segment" timestamp'leri altyazı
segmentasyonu için fazla kaba.

Wizper hosted olduğu için vad_filter / condition_on_previous_text gibi düğmeler
yok; halüsinasyon temizliği segment.py'ye taşındı.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..config import Config
from ..models import Word


def _client():
    if not os.environ.get("FAL_KEY"):
        raise RuntimeError("FAL_KEY tanımlı değil. .env dosyasına ekle: FAL_KEY=...")
    import fal_client  # lazy: CLI --help için import maliyeti ödemeyelim

    return fal_client


def normalize_chunks(payload: dict) -> list[Word]:
    """Wizper yanıtını Word listesine çevir.

    Şema değişirse düzeltilecek TEK yer burası — ham yanıt wizper_raw.json'a
    ayrıca yazılıyor.
    """
    chunks = payload.get("chunks") or []
    words: list[Word] = []
    for ch in chunks:
        text = (ch.get("text") or "").strip()
        if not text:
            continue
        ts = ch.get("timestamp") or [None, None]
        start, end = (ts + [None, None])[:2]
        if start is None:
            continue
        if end is None:  # son chunk bazen açık uçlu gelir
            end = float(start) + 0.3
        words.append(Word(word=text, start=float(start), end=float(end)))

    # Monotonluk garantisi: hizalama hataları sonraki aşamaları bozar
    for i in range(1, len(words)):
        if words[i].start < words[i - 1].end:
            words[i].start = words[i - 1].end
        if words[i].end <= words[i].start:
            words[i].end = words[i].start + 0.05
    return words


def transcribe(audio: Path, cfg: Config, raw_dump: Path | None = None) -> list[Word]:
    fal_client = _client()

    url = fal_client.upload_file(str(audio))
    result = fal_client.subscribe(
        cfg.transcribe.model,
        arguments={
            "audio_url": url,
            "task": "transcribe",
            "language": cfg.source_language,
            "chunk_level": cfg.transcribe.chunk_level,
            "version": cfg.transcribe.version,
        },
        with_logs=False,
    )

    if raw_dump is not None:
        raw_dump.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    words = normalize_chunks(result)
    if not words:
        raise RuntimeError(
            "Wizper kelime döndürmedi. Ham yanıtı kontrol et: "
            f"{raw_dump}\nchunk_level='{cfg.transcribe.chunk_level}' doğru mu?"
        )
    return words
