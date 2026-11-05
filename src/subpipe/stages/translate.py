"""Aşama 4: bağlamlı batch çeviri (Claude).

Cue bazlı ÇEVİRME. Türkçe SOV, İngilizce SVO — üç cue'ya yayılmış bir cümleyi
parça parça çevirmek anlamsız metin üretir.

Akış: cue'ları sentence_id üzerinden tam cümleye topla -> komşu cümleleri
salt-bağlam olarak ekleyip batch halinde çevir -> dönen TR metni o cümlenin EN
cue'larına karakter payına göre dağıt.

Zaman çizelgesinin sahibi EN cue'ları. Hardsub'da iki dil aynı anda ekranda
olacağı için ortak bir zaman çizelgesi şart.
"""

from __future__ import annotations

import json
import os
import re

from ..config import Config
from ..models import BilingualCue, Cue, Sentence
from .segment import split_lines

SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "tr": {"type": "string"},
                },
                "required": ["id", "tr"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["translations"],
    "additionalProperties": False,
}

RULES = """Sen deneyimli bir altyazı çevirmenisin. İngilizce transkript cümlelerini Türkçeye çeviriyorsun.

Altyazı çevirisi kuralları:
- Kısa ve okunabilir tut. Altyazı ekranda saniyeler kalır; okuyucu duraklatamaz.
- Dolgu kelimelerini at: "um", "uh", "you know", "like", "I mean", "sort of".
- Deyimleri birebir çevirme, Türkçe karşılığını ver.
- Konuşma dili doğallığını koru; kitabi veya çeviri kokan yapı kurma.
- Noktalama Türkçe kurallarına göre olsun.
- Sayı, birim ve özel isimleri koru.
- Cümle tek başına anlamsızsa bile bağlamdaki komşu cümlelere göre çevir.
- Kendi yorumunu ekleme, açıklama parantezi açma.
- ÇIKTIDA SATIR SONU KULLANMA — düz tek satır metin döndür, satır bölmeyi program yapacak.

Sana verilen her cümlenin bir id'si var. Her id için tam olarak bir çeviri döndür.
"context" olarak işaretlenen cümleler sadece bağlam içindir — onları ÇEVİRME, çıktıya ekleme."""


def _client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY tanımlı değil. .env dosyasına ekle.")
    import anthropic  # lazy

    return anthropic.Anthropic()


def build_system(cfg: Config) -> str:
    parts = [RULES]
    if cfg.translate.video_context:
        parts.append(f"\n## Video bağlamı\n{cfg.translate.video_context.strip()}")
    if cfg.translate.tone:
        parts.append(f"\n## Ton\n{cfg.translate.tone.strip()}")
    if cfg.translate.glossary:
        terms = "\n".join(f"- {k} → {v}" for k, v in cfg.translate.glossary.items())
        parts.append(f"\n## Terim sözlüğü (bu karşılıkları kullan)\n{terms}")
    return "\n".join(parts)


def _batch_payload(
    batch: list[Sentence], all_sentences: list[Sentence], window: int
) -> str:
    by_id = {s.id: s for s in all_sentences}
    lo, hi = batch[0].id, batch[-1].id
    ctx_before = [by_id[i] for i in range(max(0, lo - window), lo) if i in by_id]
    ctx_after = [by_id[i] for i in range(hi + 1, hi + 1 + window) if i in by_id]

    return json.dumps(
        {
            "context_before": [s.text for s in ctx_before],
            "translate": [{"id": s.id, "en": s.text} for s in batch],
            "context_after": [s.text for s in ctx_after],
        },
        ensure_ascii=False,
        indent=2,
    )


def _call(client, cfg: Config, system: str, payload: str) -> dict[int, str]:
    resp = client.messages.create(
        model=cfg.translate.model,
        max_tokens=cfg.translate.max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        output_config={
            "format": {"type": "json_schema", "schema": SCHEMA},
            "effort": "medium",
        },
        messages=[{"role": "user", "content": payload}],
    )

    # stop_reason'ı content'ten ÖNCE kontrol et — refusal'da content boş olabilir
    if resp.stop_reason == "refusal":
        detail = getattr(resp, "stop_details", None)
        raise RuntimeError(f"Claude çeviriyi reddetti: {detail}")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError(
            "Çeviri max_tokens'a takıldı. config.yaml'da translate.batch_size'ı "
            "düşür veya max_tokens'ı artır."
        )

    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        raise RuntimeError(f"Claude metin döndürmedi (stop_reason={resp.stop_reason}).")

    data = json.loads(text)
    return {int(t["id"]): t["tr"].strip() for t in data["translations"]}


def translate_sentences(sentences: list[Sentence], cfg: Config) -> dict[int, str]:
    client = _client()
    system = build_system(cfg)
    out: dict[int, str] = {}

    size = cfg.translate.batch_size
    batches = [sentences[i : i + size] for i in range(0, len(sentences), size)]

    for n, batch in enumerate(batches, 1):
        print(f"  çeviri batch {n}/{len(batches)} ({len(batch)} cümle)")
        got = _call(client, cfg, system, _batch_payload(batch, sentences, cfg.translate.context_window))
        out.update(got)

        # Eksik id varsa tek tek yeniden iste (structured output hizalamayı
        # garantiliyor ama model bir cümleyi atlarsa yakalayalım)
        missing = [s for s in batch if s.id not in out]
        for s in missing:
            print(f"    eksik id={s.id}, tekrar isteniyor")
            out.update(_call(client, cfg, system, _batch_payload([s], sentences, cfg.translate.context_window)))

    return out


# TR dağıtımında bölme noktası puanlaması. Karakter payı tek başına yetmiyor:
# "Ne kadar verirsen o kadar | alırsın." teknik olarak doğru orandadır ama
# okunmaz. Oranı hedef alıp yakınındaki dilbilgisel sınırlara çekiyoruz.
_TR_STRONG_END = re.compile(r"[.!?…][\"'’”\)\]]*$")
_TR_SOFT_END = re.compile(r"[,;:][\"'’”\)\]]*$")

# Bu kelimelerden SONRA bölmek kötü — kendilerinden sonrakine bağlılar.
# Türkçe sona doğru ağırlaşan bir dil; belirteç ve bağlaçları fiilinden
# ayırmak cue'yu anlamsız bırakıyor.
_TR_NO_BREAK_AFTER = {
    "bir", "bu", "şu", "o", "her", "hiç", "en", "daha", "çok", "az", "kadar",
    "ne", "ve", "veya", "ya", "ama", "fakat", "ancak", "yani", "hem", "de",
    "da", "ki", "için", "gibi", "ile", "göre", "diye", "bütün", "tüm",
    "başka", "kendi", "böyle", "şöyle", "öyle", "hangi", "kaç", "birkaç",
}


# Bu kelimelerden ÖNCE bölmek kötü — kendilerinden öncekine bağlılar.
# "...ayırsa | bile" gibi bölmeler cue'yu havada bırakıyor.
_TR_NO_BREAK_BEFORE = {
    "bile", "de", "da", "ki", "mi", "mı", "mu", "mü", "ise", "için", "gibi",
    "kadar", "göre", "ile", "diye", "olarak", "sonra", "önce", "üzere",
    "dolayı", "beri", "rağmen", "yerine", "boyunca", "hakkında",
}


def _tr_break_score(
    words: list[str], k: int, prev_cut: int, target: float, cum: list[int],
    capacity: int | None, last: bool,
) -> float:
    """words[:k] bir parçayı bitirirse bu bölmenin puanı."""
    # Karakter payından sapma — ana terim, karakter başına ceza
    score = -abs(cum[k - 1] - target) * 1.5

    prev_w = words[k - 1]
    if _TR_STRONG_END.search(prev_w):
        score += 42
    elif _TR_SOFT_END.search(prev_w):
        score += 22
    if prev_w.lower().strip(".,;:!?\"'’”") in _TR_NO_BREAK_AFTER:
        score -= 50
    if k < len(words) and words[k].lower().strip(".,;:!?\"'’”") in _TR_NO_BREAK_BEFORE:
        score -= 50

    # Öksüz parça: tek kelimelik cue hem kötü görünür hem bilgi taşımaz
    if k - prev_cut == 1:
        score -= 20
    if last and len(words) - k == 1:
        score -= 30

    # Kapasiteyi aşan parça split_lines'ta greedy sarmaya düşer, QA ihlali olur
    if capacity is not None:
        # cum[prev_cut-1] sonrasındaki ayırıcı boşluk parçaya ait değil (-1)
        chunk_len = cum[k - 1] - (cum[prev_cut - 1] + 1 if prev_cut else 0)
        if chunk_len > capacity:
            score -= 20 + (chunk_len - capacity) * 4
    return score


def distribute_over_cues(
    tr_text: str, en_cues: list[Cue], capacity: int | None = None
) -> list[str]:
    """TR cümle metnini o cümlenin EN cue'larına dağıt.

    Hedef bölme noktaları EN cue'larının karakter payından geliyor — zaman
    çizelgesinin sahibi İngilizce. Ama hedefe körü körüne uyulmuyor: her hedefin
    etrafında en iyi puanlı kelime sınırı aranıyor, böylece TR parçaları cümle
    ve yan cümle sınırlarına oturuyor.
    """
    if len(en_cues) == 1:
        return [tr_text]

    words = tr_text.split()
    n = len(en_cues)
    if not words:
        return [""] * n
    if len(words) <= n:
        # Kelime sayısı cue sayısından az: baştan doldur, kalanı boş bırak
        return [words[i] if i < len(words) else "" for i in range(n)]

    # cum[i] = words[:i+1] birleştirildiğinde oluşan karakter sayısı
    cum: list[int] = []
    acc = 0
    for w in words:
        acc += len(w) + (1 if cum else 0)
        cum.append(acc)
    total_chars = acc

    weights = [max(len(c.text), 1) for c in en_cues]
    total_w = sum(weights)
    targets: list[float] = []
    running = 0
    for w in weights[:-1]:
        running += w
        targets.append(total_chars * running / total_w)

    cuts: list[int] = []
    prev_cut = 0
    for i, target in enumerate(targets):
        remaining = len(targets) - i - 1
        lo = prev_cut + 1
        hi = len(words) - remaining - 1  # sonraki her parçaya en az 1 kelime
        if lo > hi:
            cuts.append(min(lo, len(words)))
            prev_cut = cuts[-1]
            continue
        best_k, best = lo, float("-inf")
        for k in range(lo, hi + 1):
            sc = _tr_break_score(
                words, k, prev_cut, target, cum, capacity, last=(remaining == 0)
            )
            if sc > best:
                best, best_k = sc, k
        cuts.append(best_k)
        prev_cut = best_k

    chunks: list[str] = []
    start = 0
    for k in cuts:
        chunks.append(" ".join(words[start:k]))
        start = k
    chunks.append(" ".join(words[start:]))
    return chunks


def build_bilingual(
    cues: list[Cue], sentences: list[Sentence], translations: dict[int, str], cfg: Config
) -> list[BilingualCue]:
    by_sentence: dict[int, list[Cue]] = {}
    for c in cues:
        by_sentence.setdefault(c.sentence_id, []).append(c)

    result: dict[int, BilingualCue] = {}
    for sid, group in by_sentence.items():
        group.sort(key=lambda c: c.start)
        tr_text = translations.get(sid, "")
        capacity = cfg.cues.max_chars_per_line * cfg.cues.max_lines
        chunks = (
            distribute_over_cues(tr_text, group, capacity)
            if tr_text
            else [""] * len(group)
        )
        for cue, chunk in zip(group, chunks):
            result[id(cue)] = BilingualCue(
                start=cue.start,
                end=cue.end,
                en_lines=cue.lines,
                tr_lines=split_lines(chunk, cfg.cues.max_chars_per_line, cfg.cues.max_lines)
                if chunk
                else [],
                sentence_id=sid,
            )

    return sorted(result.values(), key=lambda c: c.start)
