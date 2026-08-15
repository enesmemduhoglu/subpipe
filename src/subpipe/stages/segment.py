"""Aşama 3: yeniden segmentasyon — pipeline'ın en kritik modülü.

Whisper'ın kendi segmentleri ASR için optimize edilmiş, OKUMA için değil.
Doğrudan ASS'e dökülürse çok uzun satırlar, okunamayacak hızda geçen cue'lar ve
cümle ortasından bölünmüş ifadeler çıkar. Burada kelime timestamp'lerinden
altyazı kurallarına uyan cue'lar yeniden kuruluyor.
"""

from __future__ import annotations

import re

from ..config import CueConfig, Config
from ..models import Cue, Dropped, SegmentDoc, Sentence, VideoMeta, Word

SENTENCE_END = re.compile(r"[.!?]['\"’”\)\]]*$")
SOFT_PUNCT = re.compile(r"[,;:—-]['\"’”\)\]]*$")

# Nokta ile biten ama cümleyi BİTİRMEYEN kısaltmalar. Bunlar ayıklanmazsa
# "we are with Mr." tek başına 0.5 sn'lik öksüz bir cue olur.
ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "vs", "etc",
    "inc", "ltd", "co", "corp", "dept", "est", "fig", "approx", "min", "max",
    "e.g", "i.e", "a.m", "p.m", "u.s", "u.k",
}


def is_sentence_end(token: str) -> bool:
    token = token.strip()
    if not SENTENCE_END.search(token):
        return False
    if token.rstrip("'\"’”)]").endswith(("!", "?")):
        return True  # ünlem/soru işareti kısaltma olamaz
    core = token.rstrip("'\"’”)]").rstrip(".").lower()
    if core in ABBREVIATIONS:
        return False
    if len(core) == 1 and core.isalpha():
        return False  # baş harf ("J. K. Rowling")
    return True

# Bu kelimelerden SONRA bölmek kötü — artikel/edat kendinden sonrakine bağlı
NO_BREAK_AFTER = {
    "a", "an", "the", "to", "of", "in", "on", "at", "for", "from", "by", "with",
    "as", "into", "onto", "over", "under", "and", "or", "but", "nor", "is",
    "are", "was", "were", "be", "been", "being", "am", "my", "your", "his",
    "her", "its", "our", "their", "this", "that", "these", "those", "no",
    "not", "very", "so", "too", "more", "most",
    # Özne zamirleri: fiilinden ayırmak "Hello, today we | are with..." gibi
    # kötü bölmeler üretiyor
    "i", "we", "you", "he", "she", "it", "they", "there", "who",
}

# Bu kelimelerden ÖNCE bölmek iyi — yeni bir öbek başlatırlar
BREAK_BEFORE = {
    "and", "but", "because", "that", "which", "who", "when", "while", "if",
    "so", "then", "although", "though", "since", "before", "after", "unless",
    "until", "however", "therefore", "whereas",
}


def join_words(words: list[Word]) -> str:
    text = " ".join(w.word.strip() for w in words)
    text = re.sub(r"\s+([,.!?;:'’\)\]])", r"\1", text)
    text = re.sub(r"([(\[])\s+", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


# --------------------------------------------------------------------------
# 1) Kelimeleri cümlelere grupla
# --------------------------------------------------------------------------


def group_sentences(words: list[Word], pause: float) -> list[Sentence]:
    sentences: list[Sentence] = []
    cur: list[Word] = []
    for i, w in enumerate(words):
        cur.append(w)
        ends = is_sentence_end(w.word)
        gap = words[i + 1].start - w.end if i + 1 < len(words) else float("inf")
        if ends or gap > pause:
            sentences.append(_mk_sentence(len(sentences), cur))
            cur = []
    if cur:
        sentences.append(_mk_sentence(len(sentences), cur))
    return sentences


def _mk_sentence(idx: int, words: list[Word]) -> Sentence:
    return Sentence(
        id=idx,
        text=join_words(words),
        start=words[0].start,
        end=words[-1].end,
        words=list(words),
    )


# --------------------------------------------------------------------------
# 0) Halüsinasyon temizliği
#    Hosted ASR'de VAD düğmesi olmadığı için burada yapılıyor. Atılanlar sessizce
#    yutulmaz — QA raporunda listelenir.
# --------------------------------------------------------------------------


def filter_hallucinations(
    sentences: list[Sentence], cfg: Config
) -> tuple[list[Sentence], list[Dropped]]:
    hc = cfg.hallucination
    kept: list[Sentence] = []
    dropped: list[Dropped] = []
    run_text: str | None = None
    run_len = 0

    for s in sentences:
        norm = re.sub(r"[^a-z0-9ğüşiöç ]+", "", s.text.lower()).strip()

        matched = next((p for p in hc.patterns if p.lower() in norm), None)
        if matched:
            dropped.append(Dropped(text=s.text, start=s.start, end=s.end,
                                   reason=f"halüsinasyon kalıbı: '{matched}'"))
            continue

        if norm and norm == run_text:
            run_len += 1
            if run_len >= hc.max_repeats:
                dropped.append(Dropped(text=s.text, start=s.start, end=s.end,
                                       reason=f"{run_len}x arka arkaya tekrar"))
                continue
        else:
            run_text, run_len = norm, 1

        if (s.end - s.start) > hc.long_short_duration and len(s.words) < hc.long_short_words:
            dropped.append(Dropped(text=s.text, start=s.start, end=s.end,
                                   reason=f"{s.end - s.start:.1f}s süreye yayılmış "
                                          f"{len(s.words)} kelime"))
            continue

        kept.append(s)

    for i, s in enumerate(kept):
        s.id = i
    return kept, dropped


# --------------------------------------------------------------------------
# Satır bölme — dilden bağımsız, TR için de kullanılıyor
# --------------------------------------------------------------------------


def split_lines(text: str, max_chars: int, max_lines: int) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    words = text.split()
    if max_lines >= 2 and len(words) > 1:
        best: tuple[float, list[str]] | None = None
        for k in range(1, len(words)):
            l1, l2 = " ".join(words[:k]), " ".join(words[k:])
            if len(l1) > max_chars or len(l2) > max_chars:
                continue
            # Dengeli olsun; eşitlikte ilk satır biraz uzun tercih edilir
            score = abs(len(l1) - len(l2)) + (2.0 if len(l1) < len(l2) else 0.0)
            if SOFT_PUNCT.search(l1):
                score -= 6
            if words[k].lower().strip(".,!?") in BREAK_BEFORE:
                score -= 4
            if words[k - 1].lower().strip(".,!?;:") in NO_BREAK_AFTER:
                score += 10
            if best is None or score < best[0]:
                best = (score, [l1, l2])
        if best:
            return best[1]

    # Sığmıyor: greedy sar. QA raporu bunu ihlal olarak işaretler.
    lines, cur = [], ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if cur and len(cand) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


# --------------------------------------------------------------------------
# Cümleyi cue'lara böl
# --------------------------------------------------------------------------


def _fits(words: list[Word], c: CueConfig, max_cps: float) -> bool:
    text = join_words(words)
    if len(text) > c.capacity:  # sert sınır
        return False
    dur = words[-1].end - words[0].start
    if dur > c.max_duration:  # sert sınır
        return False

    # CPS yumuşak sınır. Konuşma zaten max_cps'ten hızlıysa bölmek durumu
    # düzeltmez, sadece min_duration'ın altında minik cue'lar üretir
    # ("separately," gibi tek kelimelik 0.5s'lik cue). Bu yüzden CPS
    # yüzünden ancak elimizdeki parça min süreyi zaten karşılıyorsa duruyoruz;
    # aksi halde büyümeye devam ediyoruz ve ihlali QA raporluyor.
    if len(words) > 1 and dur > 0 and len(text) / dur > max_cps:
        prev_dur = words[-2].end - words[0].start
        if prev_dur >= c.min_duration:
            return False
    return True


def _choose_break(words: list[Word], start: int, hard_end: int) -> int:
    """[start, hard_end) aralığında en iyi bölme noktasını seç.

    hard_end kapasite/süre sınırı. Oradan geriye doğru bakıp dilbilgisel olarak
    daha temiz bir nokta varsa oraya çekiyoruz.
    """
    if hard_end - start <= 1:
        return hard_end

    lookback = max(1, int((hard_end - start) * 0.4))
    best_k, best_score = hard_end, float("-inf")

    for k in range(hard_end, max(start + 1, hard_end - lookback) - 1, -1):
        prev = words[k - 1].word.lower().strip(".,!?;:\"'")
        score = 0.0
        if SOFT_PUNCT.search(words[k - 1].word.strip()):
            score += 100
        if is_sentence_end(words[k - 1].word):
            score += 120
        if k < len(words):
            gap = words[k].start - words[k - 1].end
            if gap > 0.30:
                score += 60
            elif gap > 0.15:
                score += 25
            if words[k].word.lower().strip(".,!?") in BREAK_BEFORE:
                score += 40
        if prev in NO_BREAK_AFTER:
            score -= 80
        # Kapasiteyi boş bırakma — hard_end'e yakınlık ödülü
        score += (k - start) * 1.5

        if score > best_score:
            best_score, best_k = score, k

    return best_k


def sentence_to_cues(s: Sentence, c: CueConfig, max_cps: float) -> list[Cue]:
    words, cues, i, n = s.words, [], 0, len(s.words)
    while i < n:
        j = i
        while j < n and _fits(words[i : j + 1], c, max_cps):
            j += 1
        hard_end = max(j, i + 1)  # en az bir kelime ilerle
        k = _choose_break(words, i, hard_end) if hard_end < n else hard_end
        chunk = words[i:k]
        cues.append(
            Cue(
                start=chunk[0].start,
                end=chunk[-1].end,
                lines=split_lines(join_words(chunk), c.max_chars_per_line, c.max_lines),
                sentence_id=s.id,
            )
        )
        i = k
    return cues


# --------------------------------------------------------------------------
# Zamanlama düzeltmeleri
# --------------------------------------------------------------------------


def enforce_timing(cues: list[Cue], c: CueConfig, duration: float) -> None:
    for idx, cue in enumerate(cues):
        if cue.duration < c.min_duration:
            limit = cues[idx + 1].start - c.min_gap if idx + 1 < len(cues) else duration
            cue.end = min(cue.start + c.min_duration, max(limit, cue.end))

    for idx in range(len(cues) - 1):
        latest = cues[idx + 1].start - c.min_gap
        if cues[idx].end > latest:
            cues[idx].end = max(cues[idx].start + 0.1, latest)


# --------------------------------------------------------------------------


def segment(words: list[Word], meta: VideoMeta, cfg: Config) -> SegmentDoc:
    sentences = group_sentences(words, cfg.cues.sentence_pause)
    sentences, dropped = filter_hallucinations(sentences, cfg)

    cues: list[Cue] = []
    for s in sentences:
        cues.extend(sentence_to_cues(s, cfg.cues, cfg.cues.max_cps_en))

    enforce_timing(cues, cfg.cues, meta.duration)
    return SegmentDoc(cues=cues, sentences=sentences, dropped=dropped)
