"""Aşama 1: ses çıkarma + video metadata.

Videoyu doğrudan ASR servisine yükleme — 1 GB MP4 yerine ~30 MB 16 kHz mono WAV
gönderiyoruz. Upload süresi ve maliyet ciddi düşer.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ..config import Config
from ..models import VideoMeta


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise RuntimeError(
            f"{tool} bulunamadı. Kur: winget install --id Gyan.FFmpeg -e\n"
            "(kurulumdan sonra yeni bir terminal aç — PATH güncellenmeli)"
        )
    return path


def probe(video: Path) -> VideoMeta:
    """ffprobe ile çözünürlük/fps/süre. PlayResX/PlayResY buradan gelir —
    ASS'de bu değerler videonunkiyle eşleşmezse tüm ölçekleme kayar."""
    out = subprocess.run(
        [
            _require("ffprobe"), "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate",
            "-show_entries", "stream_side_data=rotation",
            "-show_entries", "format=duration",
            "-of", "json", str(video),
        ],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    stream = data["streams"][0]
    num, _, den = stream["r_frame_rate"].partition("/")
    fps = float(num) / float(den or 1)

    width, height = int(stream["width"]), int(stream["height"])

    # Telefonla çekilen dikey videolar genelde YATAY depolanır ve bir Display
    # Matrix ile 90° döndürülerek gösterilir. ffprobe'un width/height'ı DEPOLANAN
    # boyut; ffmpeg ise decode ederken otomatik döndürdüğü için render edilen kare
    # bunun tersi olur. Takas etmezsek PlayRes gerçek kareyle uyuşmaz ve hem
    # punto ölçeklemesi hem kenar boşlukları kayar.
    rotation = 0.0
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            rotation = float(sd["rotation"])
            break
    if abs(rotation) % 180 == 90:
        width, height = height, width

    return VideoMeta(
        width=width,
        height=height,
        fps=round(fps, 3),
        duration=float(data["format"]["duration"]),
    )


def extract_audio(video: Path, dest: Path, cfg: Config) -> Path:
    subprocess.run(
        [
            _require("ffmpeg"), "-y", "-i", str(video),
            "-vn",
            "-ac", str(cfg.audio.channels),
            "-ar", str(cfg.audio.sample_rate),
            "-c:a", "pcm_s16le",
            str(dest),
        ],
        capture_output=True, text=True, check=True,
    )
    return dest
