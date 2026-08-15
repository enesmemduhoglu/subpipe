"""Aşama 6: ffmpeg burn-in.

Windows tuzağı: `subtitles=` filtresi `C:\\...` yolunu parse edemez — `:` filtre
argüman ayracı. Mutlak yolu escape'lemeye çalışmak yerine ffmpeg'i ASS dosyasının
dizininde çalıştırıp GÖRELİ dosya adı veriyoruz. Fontlar da oraya kopyalanıyor.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ..config import Config

FONTS_SRC = Path("assets/fonts")


def _stage_fonts(workdir: Path, font_name: str) -> str | None:
    """Fontları çalışma dizinine kopyala ve GÖRELİ fontsdir adı döndür."""
    fonts = (
        [p for p in FONTS_SRC.iterdir() if p.suffix.lower() in {".ttf", ".otf", ".ttc"}]
        if FONTS_SRC.is_dir()
        else []
    )
    if not fonts:
        # libass font bulamazsa SESSİZCE başka bir fonta düşer — uyaralım
        print(
            f"  uyarı: assets/fonts/ boş, '{font_name}' sistem fontlarından aranacak. "
            "Bulunamazsa libass sessizce başka bir fonta düşer."
        )
        return None
    dest = workdir / "fonts"
    shutil.copytree(FONTS_SRC, dest, dirs_exist_ok=True)
    return "fonts"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def measure_loudness(ffmpeg: str, video: Path, cfg: Config, cache: Path | None) -> dict | None:
    """loudnorm birinci geçiş: kaynağın gerçek yüksekliğini ölç.

    İki geçişli normalizasyon tek geçişliden daha doğru ve pompalamıyor: önce
    ölçüp sonra ölçülen değerlerle düzeltiyoruz. Sonuç kaynağa bağlı olduğu için
    cache'leniyor.
    """
    if cache is not None and cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    r = cfg.render
    proc = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-i", str(video.resolve()), "-vn",
            "-af", f"loudnorm=I={r.target_lufs}:TP={r.target_peak}:LRA=11:print_format=json",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    # JSON stderr'in SONUNDA basılıyor; son { ... } bloğunu al
    start = proc.stderr.rfind("{")
    end = proc.stderr.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(proc.stderr[start : end + 1])
    except json.JSONDecodeError:
        return None

    if cache is not None:
        cache.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def _audio_args(ffmpeg: str, video: Path, cfg: Config, cache: Path | None) -> list[str]:
    r = cfg.render
    if not r.normalize_audio:
        return ["-c:a", "copy"]

    m = measure_loudness(ffmpeg, video, cfg, cache)
    if not m:
        print("  uyarı: ses yüksekliği ölçülemedi, normalizasyon atlanıyor")
        return ["-c:a", "copy"]

    measured = float(m["input_i"])
    print(f"  ses: {measured:.1f} LUFS -> {r.target_lufs:.1f} LUFS "
          f"({r.target_lufs - measured:+.1f} dB)")

    af = (
        f"loudnorm=I={r.target_lufs}:TP={r.target_peak}:LRA=11"
        f":measured_I={m['input_i']}:measured_LRA={m['input_lra']}"
        f":measured_TP={m['input_tp']}:measured_thresh={m['input_thresh']}"
        f":offset={m['target_offset']}:linear=true"
    )
    # loudnorm 192kHz'e yükseltiyor; aresample ile orijinal orana geri dön
    return ["-af", f"{af},aresample=48000", "-c:a", "aac", "-b:a", r.audio_bitrate]


def render(
    video: Path, ass_path: Path, out_path: Path, cfg: Config, preview: bool = False
) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg bulunamadı. Kur: winget install --id Gyan.FFmpeg -e\n"
            "(kurulumdan sonra yeni terminal aç)"
        )

    workdir = ass_path.parent
    fontsdir = _stage_fonts(workdir, cfg.style.font_name)

    vf = f"subtitles={ass_path.name}"
    if fontsdir:
        vf += f":fontsdir={fontsdir}"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    base = [ffmpeg, "-y", "-i", str(video.resolve())]
    if preview:
        base += ["-t", str(cfg.render.preview_seconds)]

    audio = _audio_args(ffmpeg, video, cfg, workdir / "loudness.json")
    tail = ["-vf", vf, "-pix_fmt", "yuv420p", *audio,
            "-movflags", "+faststart", str(out_path.resolve())]

    if preview:
        cmd = base + ["-c:v", cfg.render.preview_encoder, "-cq", str(cfg.render.preview_cq),
                      "-preset", "p4"] + tail
        proc = _run(cmd, workdir)
        if proc.returncode != 0:
            print("  NVENC kullanılamadı, libx264'e düşülüyor")
            cmd = base + ["-c:v", "libx264", "-crf", "23", "-preset", "veryfast"] + tail
            proc = _run(cmd, workdir)
    else:
        cmd = base + ["-c:v", "libx264", "-crf", str(cfg.render.crf),
                      "-preset", cfg.render.preset] + tail
        proc = _run(cmd, workdir)

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg başarısız:\n{proc.stderr[-3000:]}")
    return out_path
