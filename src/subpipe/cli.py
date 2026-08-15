"""subpipe CLI.

Aşamalar sırayla çalışır ve her biri cache'lenir. Fingerprint zinciri:
audio -> transcribe -> segment -> translate -> ass -> render

Font boyutu değiştirdiğinde sadece ass + render çalışır; transkripsiyon ve
çeviri (para + dakikalar) atlanır.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv

# Windows konsolu varsayılan olarak cp1254/cp857 kullanıyor ve Türkçe
# karakterlerde (ı, ş, ğ) UnicodeEncodeError atıyor. UTF-8'e sabitle.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from . import qa as qa_mod
from .cache import Stage, file_hash, workdir
from .cache import fingerprint as fp_of
from .config import Config, load_config
from .models import BilingualCue, SegmentDoc, TranscriptDoc, TranslateDoc, VideoMeta, Word
from .stages import ass as ass_mod
from .stages import audio as audio_mod
from .stages import render as render_mod
from .stages import segment as segment_mod
from .stages import translate as translate_mod

app = typer.Typer(add_completion=False, help="Video -> iki dilli (EN+TR) hardsub MP4")

ORDER = ["audio", "transcribe", "segment", "translate", "ass", "render"]


def _echo(stage: str, msg: str) -> None:
    typer.echo(f"[{stage}] {msg}")


def _skip(stage: str) -> None:
    typer.secho(f"[{stage}] cache'ten alındı (atlandı)", fg=typer.colors.BRIGHT_BLACK)


def run_pipeline(
    video: Path,
    cfg: Config,
    upto: str = "render",
    force: Optional[str] = None,
    preview: bool = False,
) -> None:
    if not video.exists():
        raise typer.BadParameter(f"Video bulunamadı: {video}")

    wd = workdir(video)
    stop = ORDER.index(upto)
    force_from = ORDER.index(force) if force else len(ORDER)
    stem = video.stem

    def stale(name: str) -> bool:
        return ORDER.index(name) >= force_from

    # ---- 1. audio -------------------------------------------------------
    st_audio = Stage(wd, "audio", "wav")
    st_meta = Stage(wd, "meta")
    fp_audio = fp_of(file_hash(video), cfg.audio.model_dump())
    if st_audio.is_fresh(fp_audio) and st_meta.out.exists() and not stale("audio"):
        _skip("audio")
        meta = VideoMeta.model_validate(st_meta.read_json())
    else:
        _echo("audio", "ses çıkarılıyor + ffprobe")
        meta = audio_mod.probe(video)
        audio_mod.extract_audio(video, st_audio.out, cfg)
        st_audio.commit(fp_audio)
        st_meta.write_json(meta, fp_audio)
        _echo("audio", f"{meta.width}x{meta.height} @ {meta.fps}fps, {meta.duration:.1f}s")
    if stop == 0:
        return

    # ---- 2. transcribe --------------------------------------------------
    st_tr = Stage(wd, "transcript")
    fp_tr = fp_of(fp_audio, cfg.transcribe.model_dump(), cfg.source_language)
    if st_tr.is_fresh(fp_tr) and not stale("transcribe"):
        _skip("transcribe")
        doc = TranscriptDoc.model_validate(st_tr.read_json())
        words = doc.words
    else:
        _echo("transcribe", f"fal.ai {cfg.transcribe.model} (chunk_level={cfg.transcribe.chunk_level})")
        words = transcribe_mod_call(st_audio.out, cfg, wd)
        st_tr.write_json(TranscriptDoc(words=words, meta=meta), fp_tr)
        _echo("transcribe", f"{len(words)} kelime")
    if stop == 1:
        return

    # ---- 3. segment -----------------------------------------------------
    st_seg = Stage(wd, "segment")
    fp_seg = fp_of(fp_tr, cfg.cues.model_dump(), cfg.hallucination.model_dump())
    if st_seg.is_fresh(fp_seg) and not stale("segment"):
        _skip("segment")
        seg = SegmentDoc.model_validate(st_seg.read_json())
    else:
        _echo("segment", "yeniden segmentasyon")
        seg = segment_mod.segment(words, meta, cfg)
        st_seg.write_json(seg, fp_seg)
        _echo("segment", f"{len(seg.sentences)} cümle -> {len(seg.cues)} cue, "
                         f"{len(seg.dropped)} halüsinasyon atıldı")
    if stop == 2:
        return

    # ---- 4. translate ---------------------------------------------------
    st_tx = Stage(wd, "translate")
    fp_tx = fp_of(fp_seg, cfg.translate.model_dump(), cfg.target_language)
    if st_tx.is_fresh(fp_tx) and not stale("translate"):
        _skip("translate")
        bi = TranslateDoc.model_validate(st_tx.read_json()).cues
    else:
        _echo("translate", f"{cfg.translate.model} ile {len(seg.sentences)} cümle")
        translations = translate_mod.translate_sentences(seg.sentences, cfg)
        bi = translate_mod.build_bilingual(seg.cues, seg.sentences, translations, cfg)
        st_tx.write_json(TranslateDoc(cues=bi), fp_tx)
        _echo("translate", f"{len(bi)} iki dilli cue")
    if stop == 3:
        return

    # ---- 5. ass ---------------------------------------------------------
    st_ass = Stage(wd, "subs", "ass")
    fp_ass = fp_of(fp_tx, cfg.style.model_dump(), cfg.primary_language,
                   meta.width, meta.height)
    if st_ass.is_fresh(fp_ass) and not stale("ass"):
        _skip("ass")
    else:
        _echo("ass", f"ASS + SRT/VTT (PlayRes {meta.width}x{meta.height})")
        ass_mod.write_all(bi, meta, cfg, st_ass.out, Path("out"), stem)
        st_ass.commit(fp_ass)

    # QA her zaman çalışır — ucuz ve render'dan önce görülmeli
    report = qa_mod.report(bi, seg.dropped, cfg)
    (wd / "qa.md").write_text(report, encoding="utf-8")
    issues = qa_mod.check(bi, seg.dropped, cfg)
    color = typer.colors.YELLOW if issues else typer.colors.GREEN
    typer.secho(f"[qa] {len(issues)} ihlal — rapor: {wd / 'qa.md'}", fg=color)
    if stop == 4:
        return

    # ---- 6. render ------------------------------------------------------
    suffix = "_preview" if preview else "_sub"
    out_path = Path("out") / f"{stem}{suffix}.mp4"
    _echo("render", f"{'önizleme (' + str(cfg.render.preview_seconds) + 's)' if preview else 'final'} -> {out_path}")
    render_mod.render(video, st_ass.out, out_path, cfg, preview=preview)
    typer.secho(f"[render] tamam: {out_path}", fg=typer.colors.GREEN)


def transcribe_mod_call(audio_path: Path, cfg: Config, wd: Path) -> list[Word]:
    from .stages import transcribe as transcribe_mod

    return transcribe_mod.transcribe(audio_path, cfg, raw_dump=wd / "wizper_raw.json")


# --------------------------------------------------------------------------
# Komutlar
# --------------------------------------------------------------------------

VideoArg = typer.Argument(..., help="Girdi video dosyası")
ConfigOpt = typer.Option("config.yaml", "--config", "-c", help="Yapılandırma dosyası")
ForceOpt = typer.Option(None, "--force", "-f", help=f"Bu aşamadan itibaren cache'i yok say: {ORDER}")


def _load(config: Path) -> Config:
    load_dotenv()
    return load_config(config)


@app.command()
def run(
    video: Path = VideoArg,
    config: Path = ConfigOpt,
    force: Optional[str] = ForceOpt,
    preview: bool = typer.Option(False, "--preview", help="İlk N saniye, NVENC ile hızlı render"),
):
    """Tüm pipeline'ı çalıştır."""
    run_pipeline(video, _load(config), "render", force, preview)


@app.command()
def transcribe(video: Path = VideoArg, config: Path = ConfigOpt, force: Optional[str] = ForceOpt):
    """Sadece ses çıkarma + Wizper transkripsiyonu."""
    run_pipeline(video, _load(config), "transcribe", force)


@app.command()
def segment(video: Path = VideoArg, config: Path = ConfigOpt, force: Optional[str] = ForceOpt):
    """Transkripti altyazı cue'larına böl."""
    run_pipeline(video, _load(config), "segment", force)


@app.command()
def translate(video: Path = VideoArg, config: Path = ConfigOpt, force: Optional[str] = ForceOpt):
    """Cümleleri Türkçeye çevir."""
    run_pipeline(video, _load(config), "translate", force)


@app.command()
def build(video: Path = VideoArg, config: Path = ConfigOpt, force: Optional[str] = ForceOpt):
    """ASS + SRT/VTT üret ve QA raporu çıkar."""
    run_pipeline(video, _load(config), "ass", force)


@app.command()
def render(
    video: Path = VideoArg,
    config: Path = ConfigOpt,
    preview: bool = typer.Option(False, "--preview"),
):
    """Altyazıyı videoya yak."""
    run_pipeline(video, _load(config), "render", None, preview)


@app.command()
def qa(video: Path = VideoArg, config: Path = ConfigOpt):
    """QA raporunu ekrana yazdır."""
    cfg = _load(config)
    wd = workdir(video)
    st = Stage(wd, "translate")
    st_seg = Stage(wd, "segment")
    if not st.out.exists():
        raise typer.BadParameter("Önce `translate` aşamasını çalıştır.")
    cues = [BilingualCue.model_validate(c) for c in st.read_json()["cues"]]
    dropped = SegmentDoc.model_validate(st_seg.read_json()).dropped if st_seg.out.exists() else []
    typer.echo(qa_mod.report(cues, dropped, cfg))


if __name__ == "__main__":
    app()
