"""Aşama bazlı cache.

Her aşama work/<video-hash>/<stage>.json yazar, yanına <stage>.fp dosyası
(girdilerin + ilgili config bölümünün hash'i). Fingerprint değişmemişse aşama
atlanır. Font boyutu değiştirdiğinde transkripsiyon/çeviri tekrar çalışmaz.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

WORK_ROOT = Path("work")


def file_hash(path: Path, chunk_size: int = 1 << 20) -> str:
    """Büyük video dosyaları için baş + son + boyut ile hızlı hash."""
    h = hashlib.sha1()
    size = path.stat().st_size
    h.update(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(chunk_size))
        if size > chunk_size * 2:
            f.seek(-chunk_size, 2)
            h.update(f.read(chunk_size))
    return h.hexdigest()


def workdir(video: Path) -> Path:
    d = WORK_ROOT / file_hash(video)[:12]
    d.mkdir(parents=True, exist_ok=True)
    return d


def fingerprint(*parts: object) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, default=str, ensure_ascii=False).encode())
    return h.hexdigest()


class Stage:
    """Tek aşamanın çıktı + fingerprint yönetimi."""

    def __init__(self, wd: Path, name: str, ext: str = "json"):
        self.out = wd / f"{name}.{ext}"
        self.fp = wd / f"{name}.fp"
        self.name = name

    def is_fresh(self, fp: str) -> bool:
        return self.out.exists() and self.fp.exists() and self.fp.read_text().strip() == fp

    def commit(self, fp: str) -> None:
        self.fp.write_text(fp)

    def write_json(self, data: object, fp: str) -> None:
        text = data.model_dump_json(indent=2) if hasattr(data, "model_dump_json") else json.dumps(
            data, indent=2, ensure_ascii=False
        )
        self.out.write_text(text, encoding="utf-8")
        self.commit(fp)

    def read_json(self) -> dict:
        return json.loads(self.out.read_text(encoding="utf-8"))
