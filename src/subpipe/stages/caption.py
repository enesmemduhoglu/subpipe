"""Aşama 5: Instagram caption üretimi.

Videonun transkripti ve Türkçe çevirisi elde olduğu için post metnini de aynı
bağlamdan üretiyoruz. Çıktı `out/<video>.caption.md` — kopyala/yapıştır hazır.

Format sayfanın mevcut düzenine uyumlu: Açıklama (kanca + gövde + CTA),
Hashtag, Alt text.
"""

from __future__ import annotations

import json
import os

from ..config import Config
from ..models import BilingualCue, CaptionDoc

SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string"},
        "body": {"type": "string"},
        "cta": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "alt_text": {"type": "string"},
    },
    "required": ["hook", "body", "cta", "hashtags", "alt_text"],
    "additionalProperties": False,
}

RULES = """Bir Instagram İngilizce öğrenme sayfası için Reels post metni (caption) yazıyorsun.
Sana videonun İngilizce transkripti ve Türkçe çevirisi veriliyor.

Kurallar:
- "hook": ilk satır. Kaydırmayı durduracak kadar somut olsun. Soru ya da net bir iddia.
  Klişe açılış yok ("Biliyor muydunuz?", "İşte size...", "Bu videoda..." gibi).
- "body": 2-3 satır. Videonun ne öğrettiğini ve kime yaradığını söyle. Videoda
  GEÇMEYEN bilgi uydurma; anlatılanın dışına çıkma.
- "cta": son satır. Kaydetmeye, yorum yapmaya ya da arkadaşına göndermeye çağır.
  Tek bir eylem iste, üç şey birden isteme.
- "hashtags": {n} etiket. Başına # KOYMA, sadece kelimeyi ver. Karışım olsun:
  genel İngilizce öğrenme + videonun konusu + Türkçe etiketler. Alakasız popüler
  etiket ekleme.
- "alt_text": görme engelli kullanıcılar için videonun tek cümlelik betimlemesi.
  Ne görüldüğünü anlat, konuyu tekrar etme.

Dil: Türkçe, sen dili, samimi. Emoji en fazla 1 tane ve sadece CTA satırında.
Abartılı pazarlama dili ve ünlem yığını yok."""


def _client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY tanımlı değil. .env dosyasına ekle.")
    import anthropic

    return anthropic.Anthropic()


def build_system(cfg: Config) -> str:
    parts = [RULES.replace("{n}", str(cfg.caption.hashtag_count))]
    if cfg.translate.video_context:
        parts.append(f"\n## Sayfa ve hedef kitle\n{cfg.translate.video_context.strip()}")
    if cfg.caption.extra:
        parts.append(f"\n## Ek talimat\n{cfg.caption.extra.strip()}")
    return "\n".join(parts)


def generate(cues: list[BilingualCue], cfg: Config) -> CaptionDoc:
    client = _client()

    en = " ".join(" ".join(c.en_lines) for c in cues if c.en_lines)
    tr = " ".join(" ".join(c.tr_lines) for c in cues if c.tr_lines)
    payload = json.dumps(
        {"transcript_en": en, "transcript_tr": tr}, ensure_ascii=False, indent=2
    )

    resp = client.messages.create(
        model=cfg.caption.model,
        max_tokens=cfg.caption.max_tokens,
        system=[
            {"type": "text", "text": build_system(cfg), "cache_control": {"type": "ephemeral"}}
        ],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "medium"},
        messages=[{"role": "user", "content": payload}],
    )

    # stop_reason'ı content'ten ÖNCE kontrol et
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"Claude caption üretmeyi reddetti: {getattr(resp, 'stop_details', None)}")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Caption max_tokens'a takıldı; caption.max_tokens'ı artır.")

    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        raise RuntimeError(f"Claude metin döndürmedi (stop_reason={resp.stop_reason}).")

    d = json.loads(text)
    tags = [t.lstrip("#").strip() for t in d["hashtags"] if t.strip()]
    return CaptionDoc(
        hook=d["hook"].strip(),
        body=d["body"].strip(),
        cta=d["cta"].strip(),
        hashtags=tags,
        alt_text=d["alt_text"].strip(),
    )


def to_markdown(doc: CaptionDoc) -> str:
    tags = " ".join(f"#{t}" for t in doc.hashtags)
    return "\n".join(
        [
            "## Açıklama",
            "",
            doc.hook,
            "",
            doc.body,
            "",
            doc.cta,
            "",
            "## Hashtag",
            "",
            tags,
            "",
            "## Alt text",
            "",
            doc.alt_text,
            "",
        ]
    )
