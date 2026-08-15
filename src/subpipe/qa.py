"""Kalite kapısı: render'dan ÖNCE kural ihlallerini raporla.

Rapor + ASS elde olduğu için ihlalleri Aegisub / Subtitle Edit ile elle düzeltip
sadece `render` aşamasını tekrar çalıştırabilirsin.
"""

from __future__ import annotations

from .config import Config
from .models import BilingualCue, Dropped


def _cps(text: str, dur: float) -> float:
    return len(text) / dur if dur > 0 else float("inf")


def check(cues: list[BilingualCue], dropped: list[Dropped], cfg: Config) -> list[str]:
    c = cfg.cues
    issues: list[str] = []

    for i, cue in enumerate(cues, 1):
        tag = f"#{i} [{cue.start:7.2f}-{cue.end:7.2f}]"
        en, tr = " ".join(cue.en_lines), " ".join(cue.tr_lines)

        if cue.duration < c.min_duration - 1e-6:
            issues.append(f"{tag} süre {cue.duration:.2f}s < {c.min_duration}s")
        if cue.duration > c.max_duration + 1e-6:
            issues.append(f"{tag} süre {cue.duration:.2f}s > {c.max_duration}s")

        for lang, text, limit in (("EN", en, c.max_cps_en), ("TR", tr, c.max_cps_tr)):
            if text:
                v = _cps(text, cue.duration)
                if v > limit:
                    issues.append(f"{tag} {lang} okuma hızı {v:.1f} CPS > {limit}")

        for lang, lines in (("EN", cue.en_lines), ("TR", cue.tr_lines)):
            if len(lines) > c.max_lines:
                issues.append(f"{tag} {lang} {len(lines)} satır > {c.max_lines}")
            for ln in lines:
                if len(ln) > c.max_chars_per_line:
                    issues.append(
                        f"{tag} {lang} satır {len(ln)} karakter > {c.max_chars_per_line}: "
                        f"{ln!r}"
                    )

        if not tr:
            issues.append(f"{tag} TR çevirisi boş: {en!r}")

        if i < len(cues):
            gap = cues[i].start - cue.end
            if gap < c.min_gap - 1e-6:
                issues.append(f"{tag} sonraki cue ile boşluk {gap:.3f}s < {c.min_gap}s")

    return issues


def report(cues: list[BilingualCue], dropped: list[Dropped], cfg: Config) -> str:
    issues = check(cues, dropped, cfg)
    total_dur = cues[-1].end - cues[0].start if cues else 0.0

    lines = [
        "# QA raporu",
        "",
        f"- Cue sayısı: **{len(cues)}**",
        f"- Kapsanan süre: **{total_dur:.1f}s**",
        f"- Kural ihlali: **{len(issues)}**",
        f"- Halüsinasyon filtresiyle atılan: **{len(dropped)}**",
        "",
    ]

    if dropped:
        lines += ["## Atılan cümleler", ""]
        lines += [
            f"- `[{d.start:7.2f}-{d.end:7.2f}]` {d.text!r} — {d.reason}" for d in dropped
        ]
        lines.append("")

    lines += ["## İhlaller", ""]
    lines += [f"- {i}" for i in issues] if issues else ["İhlal yok. ✅"]
    lines.append("")
    return "\n".join(lines)
