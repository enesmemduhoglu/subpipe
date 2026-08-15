"""Aşama 5: ASS (+ SRT/VTT) üretimi.

İki dil TEK Dialogue satırında, inline stil reset ({\\rTR}) ile. İki ayrı
Dialogue + MarginV hesabı yerine bunu tercih ediyoruz: satır sayısı değiştiğinde
stack kaymaz, Alignment 2 (alt-orta) sayesinde satırlar aşağıdan yukarı yığılır.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config, LangStyle
from ..models import BilingualCue, VideoMeta

STYLE_FORMAT = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
    "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
    "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
)
EVENT_FORMAT = (
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
)


def ass_time(t: float) -> str:
    # Saniye kısmını ayrı yuvarlamak 59.999 -> "59.100" taşmasına yol açıyor;
    # tamamını santisaniye tamsayısına çevirip bölüyoruz.
    cs = max(0, int(round(t * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def srt_time(t: float) -> str:
    ms = max(0, int(round(t * 1000)))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def vtt_time(t: float) -> str:
    return srt_time(t).replace(",", ".")


# Arial bold için ölçülen ortalama karakter genişliği / punto oranı.
# Farklı fontta biraz kayar ama taşma uyarısı için yeterince isabetli.
CHAR_WIDTH_RATIO = 0.43


def check_line_width(meta: VideoMeta, cfg: Config) -> list[str]:
    """Punto x satır uzunluğu kullanılabilir genişliği aşıyor mu?

    WrapStyle 2 otomatik sarmayı kapattığı için taşan satır SARILMAZ, ekrandan
    taşar. Bu sessiz bir hata: 3:4 test videosunda görünmeyip 9:16 hedefte
    patlayabiliyor, çünkü dikeyde punto yüksekliğe göre büyürken satır genişliği
    dar kalıyor.
    """
    st = cfg.style.scaled(meta.height)
    usable = meta.width - st.margin_l - st.margin_r
    warnings = []
    for name, lang in (("TR", st.tr), ("EN", st.en)):
        need = cfg.cues.max_chars_per_line * CHAR_WIDTH_RATIO * lang.fontsize
        if need > usable:
            safe = int(usable / (CHAR_WIDTH_RATIO * lang.fontsize))
            warnings.append(
                f"{name} satırı taşabilir: {cfg.cues.max_chars_per_line} karakter x "
                f"{lang.fontsize} punto ≈ {need:.0f}px, kullanılabilir {usable}px. "
                f"cues.max_chars_per_line'ı {safe} yap ya da puntoyu küçült."
            )
    return warnings


def _style_line(name: str, st: LangStyle, cfg: Config) -> str:
    return ", ".join(
        [
            f"Style: {name}",
            cfg.style.font_name,
            str(st.fontsize),
            st.primary_colour,
            st.primary_colour,          # SecondaryColour (karaoke — kullanılmıyor)
            st.outline_colour,
            "&H00000000",               # BackColour
            str(st.bold), "0", "0", "0",  # Bold, Italic, Underline, StrikeOut
            "100", "100",               # ScaleX, ScaleY
            "0", "0",                   # Spacing, Angle
            "1",                        # BorderStyle: 1 = outline+shadow
            f"{st.outline:g}", f"{st.shadow:g}",
            "2",                        # Alignment: alt-orta
            str(cfg.style.margin_l), str(cfg.style.margin_r), str(cfg.style.margin_v),
            "1",                        # Encoding: 1 = Default (UTF-8 ile sorunsuz)
        ]
    )


def build_ass(cues: list[BilingualCue], meta: VideoMeta, cfg: Config) -> str:
    primary, secondary = ("EN", "TR") if cfg.primary_language == "en" else ("TR", "EN")

    # Punto/kenar boşlukları reference_height'e göre yazıldı; ASS koordinatları
    # PlayRes birimindedir, o yüzden gerçek video yüksekliğine ölçekle.
    cfg = cfg.model_copy(update={"style": cfg.style.scaled(meta.height)})
    st = cfg.style

    head = [
        "[Script Info]",
        "; subpipe tarafından üretildi",
        "ScriptType: v4.00+",
        # PlayRes videonun GERÇEK çözünürlüğüyle eşleşmeli, yoksa ölçekleme kayar
        f"PlayResX: {meta.width}",
        f"PlayResY: {meta.height}",
        "WrapStyle: 2",  # otomatik sarmayı kapat — satırları biz böldük
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        STYLE_FORMAT,
        _style_line("EN", cfg.style.en, cfg),
        _style_line("TR", cfg.style.tr, cfg),
        "",
        "[Events]",
        EVENT_FORMAT,
    ]

    lines = list(head)
    for cue in cues:
        top = cue.en_lines if primary == "EN" else cue.tr_lines
        bottom = cue.tr_lines if primary == "EN" else cue.en_lines
        if not top and not bottom:
            continue

        text = "\\N".join(top)
        if bottom:
            joined = f"{{\\r{secondary}}}" + "\\N".join(bottom)
            if not text:
                text = joined
            elif st.gap:
                # Diller arası boşluk: araya BOŞ bir satır koyuyoruz. Satırın
                # yüksekliği o noktadaki puntoya göre belirlendiği için \fs ile
                # boşluğu kontrol ediyoruz. ASS'de doğrudan satır aralığı etiketi
                # yok, bu standart yöntem.
                text = f"{text}\\N{{\\fs{st.gap}}}\\N{joined}"
            else:
                text = f"{text}\\N{joined}"

        if st.fade_in or st.fade_out:
            text = f"{{\\fad({st.fade_in},{st.fade_out})}}{text}"

        lines.append(
            f"Dialogue: 0,{ass_time(cue.start)},{ass_time(cue.end)},{primary},,0,0,0,,{text}"
        )

    return "\n".join(lines) + "\n"


def build_srt(cues: list[BilingualCue], lang: str) -> str:
    out, n = [], 0
    for cue in cues:
        body = cue.en_lines if lang == "en" else cue.tr_lines
        if not body:
            continue
        n += 1
        out.append(f"{n}\n{srt_time(cue.start)} --> {srt_time(cue.end)}\n" + "\n".join(body) + "\n")
    return "\n".join(out)


def build_vtt(cues: list[BilingualCue], lang: str) -> str:
    out = ["WEBVTT", ""]
    for cue in cues:
        body = cue.en_lines if lang == "en" else cue.tr_lines
        if not body:
            continue
        out.append(f"{vtt_time(cue.start)} --> {vtt_time(cue.end)}")
        out.extend(body)
        out.append("")
    return "\n".join(out)


def write_all(cues: list[BilingualCue], meta: VideoMeta, cfg: Config, ass_path: Path,
              out_dir: Path, stem: str) -> None:
    # BOM'suz UTF-8 — libass BOM'la da çalışır ama bazı araçlar takılır
    ass_path.write_text(build_ass(cues, meta, cfg), encoding="utf-8", newline="\n")
    out_dir.mkdir(parents=True, exist_ok=True)
    for lang in ("en", "tr"):
        (out_dir / f"{stem}.{lang}.srt").write_text(
            build_srt(cues, lang), encoding="utf-8", newline="\n"
        )
        (out_dir / f"{stem}.{lang}.vtt").write_text(
            build_vtt(cues, lang), encoding="utf-8", newline="\n"
        )
