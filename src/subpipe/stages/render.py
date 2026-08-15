"""Aşama 6: ffmpeg burn-in.

Windows tuzağı: `subtitles=` filtresi `C:\\...` yolunu parse edemez — `:` filtre
argüman ayracı. Mutlak yolu escape'lemeye çalışmak yerine ffmpeg'i ASS dosyasının
dizininde çalıştırıp GÖRELİ dosya adı veriyoruz. Fontlar da oraya kopyalanıyor.
"""

from __future__ import annotations

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

    tail = ["-vf", vf, "-pix_fmt", "yuv420p", "-c:a", "copy",
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
