# subpipe

İngilizce videoya **hem İngilizce hem Türkçe** altyazı ekleyen otomatik pipeline.
9:16 dikey (Reels/TikTok) için ayarlı, altyazılar videoya yakılır (hardsub).

```
video → ses → fal.ai Whisper → yeniden segmentasyon → Claude çeviri → ASS → ffmpeg → MP4
```

---

## Kurulum

```powershell
winget install --id Gyan.FFmpeg -e     # sonra YENİ terminal aç (PATH güncellenir)

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .

copy .env.example .env                 # anahtarları doldur
```

`.env`:

```
FAL_KEY=...                 # https://fal.ai/dashboard/keys
ANTHROPIC_API_KEY=...       # https://console.anthropic.com/settings/keys
```

## Kullanım

```powershell
python -m subpipe run video.mp4              # tüm pipeline
python -m subpipe run video.mp4 --preview    # ilk 60 sn, NVENC ile hızlı
```

Çıktılar `out/` altına düşer: `video_sub.mp4`, `video.en.srt`, `video.tr.srt`, `.vtt`.

### Aşamayı tek tek çalıştır

```powershell
python -m subpipe transcribe video.mp4    # ses + ASR
python -m subpipe segment   video.mp4     # cue'lara böl
python -m subpipe translate video.mp4     # Claude ile çevir
python -m subpipe build     video.mp4     # ASS/SRT/VTT + QA raporu
python -m subpipe render    video.mp4     # videoya yak
python -m subpipe qa        video.mp4     # QA raporunu ekrana bas
```

Her aşama cache'lenir. `config.yaml`'da font boyutunu değiştirip `build` çalıştırırsan
**sadece ASS yeniden üretilir** — transkripsiyon ve çeviri (para + dakikalar) atlanır.
Cache'i yok saymak için: `--force segment` (o aşamadan itibaren her şey yeniden çalışır).

## Önerilen akış

1. `python -m subpipe build video.mp4` — ASS ve QA raporu çıkar
2. `work/<hash>/qa.md` dosyasına bak: okuma hızı, satır uzunluğu, atılan halüsinasyonlar
3. Gerekirse `work/<hash>/subs.ass` dosyasını **Aegisub / Subtitle Edit** ile elle düzelt
4. `python -m subpipe render video.mp4 --preview` — 60 saniyelik önizleme, telefonda kontrol et
5. `python -m subpipe render video.mp4` — final

---

## Nasıl çalışıyor

### Yeniden segmentasyon (`stages/segment.py`)

Whisper.ın kendi segmentleri ASR için optimize edilmiş, **okuma** için değil. Doğrudan
ASS'e dökülürse okunamayacak hızda geçen, cümle ortasından bölünmüş cue'lar çıkar.
Bu modül kelime timestamp'lerinden altyazı kurallarına uyan cue'ları yeniden kurar:

- satır başına 30 karakter, max 2 satır (dikey format için; yatayda 42 yapılabilir)
- 0.85–7.0 s süre, max 17 CPS (EN) / 20 CPS (TR)
- bölme noktaları puanlanır: noktalama > uzun duraklama > bağlaç öncesi.
  Artikel/edat sonrası bölme (`the |`, `to |`) cezalandırılır
- **halüsinasyon filtresi**: hosted ASR.de VAD düğmesi yok, o yüzden sessizlikte üretilen
  "Thanks for watching" / "Altyazı M.K." tipi cue'lar burada kalıp, tekrar ve
  süre/kelime oranı sezgisiyle atılır. Atılanlar QA raporunda listelenir.

### Çeviri (`stages/translate.py`)

Cue bazlı **çevirmiyoruz**. Türkçe SOV, İngilizce SVO — üç cue'ya yayılmış bir cümleyi
parça parça çevirmek anlamsız metin üretir.

Cue'lar `sentence_id` üzerinden tam cümleye toplanır, komşu cümleler salt-bağlam olarak
eklenip Claude'a batch halinde gönderilir (structured output → ID hizalaması garanti),
dönen Türkçe metin o cümlenin EN cue'larına karakter payına göre dağıtılır.

Zaman çizelgesinin sahibi EN cue'ları — hardsub'da iki dil aynı anda ekranda olacağı
için ortak zaman çizelgesi şart.

Sistem promptu sabit ve `cache_control: ephemeral` ile cache'leniyor → batch'ler arası
input maliyeti düşer. `config.yaml`'daki `video_context`, `tone` ve `glossary` alanları
çeviri kalitesini en çok etkileyen ayarlar — doldur.

### ASS (`stages/ass.py`)

İki dil **tek Dialogue satırında**, inline stil reset (`{\rTR}`) ile. İki ayrı Dialogue +
`MarginV` hesabı yerine bu tercih edildi: satır sayısı değiştiğinde stack kaymaz,
`Alignment: 2` sayesinde satırlar aşağıdan yukarı yığılır (EN üstte, TR altta).

- `PlayResX/PlayResY` ffprobe'dan gelir — videonunkiyle eşleşmezse tüm ölçekleme kayar.
  **Döndürme matrisi hesaba katılır:** telefonla çekilen dikey videolar genelde yatay
  depolanıp 90° döndürülerek gösterilir; ffprobe depolanan boyutu verir, ffmpeg ise
  decode ederken otomatik döndürür. `probe()` bunu tespit edip en/boyu takas eder
- `WrapStyle: 2` — libass'in otomatik sarmasını kapatır, satırları biz böldük
- `MarginV: 380` — Reels/TikTok alt UI'ı ~250–320 px kaplar, 380 güvenli alanın üstünde

**Punto ve kenar boşlukları otomatik ölçeklenir.** `config.yaml`'daki değerler
`style.reference_height` (1920) için yazılmıştır; farklı çözünürlükte video verirsen
`video_height / reference_height` oranıyla ölçeklenir. 1080×1920 için ayarladığın stil
720×1280'de de aynı görünür — elle bir şey değiştirmen gerekmez.

### Render (`stages/render.py`)

Windows tuzağı: `subtitles=` filtresi `C:\...` yolunu parse edemez (`:` filtre ayracı).
Mutlak yolu escape'lemek yerine ffmpeg ASS dosyasının dizininde çalıştırılıp **göreli**
dosya adı veriliyor; fontlar da oraya kopyalanıyor.

Final: `libx264 -crf 18 -preset medium`, ses `-c:a copy` (yeniden encode edilmez),
`-movflags +faststart`. Önizleme NVENC ile.

---

## Font

Varsayılan **Arial** — her Windows'ta var, Türkçe ş ğ ı İ ö ü ç destekler, kutudan
çıkar çıkmaz çalışır.

Daha iyisi için `.ttf` dosyasını `assets/fonts/` altına at ve `config.yaml`'da
`style.font_name` değerini font adıyla değiştir (ör. `Montserrat SemiBold`).

> Font bulunamazsa libass **sessizce** başka bir fonta düşer. `assets/fonts/` boşsa
> render bir uyarı basar — yine de önizlemeden gözle kontrol et.

## Yatay (16:9) videoya geçmek

`config.yaml`:

Punto otomatik ölçeklendiği için sadece iki şey değişir:

```yaml
cues:
  max_chars_per_line: 42     # 30 -> 42 (yatayda satır daha geniş)
style:
  margin_v: 60               # 380 -> 60 (Reels UI yok, dibe yaklaş)
```

## Sorun giderme

| Belirti | Sebep / çözüm |
|---|---|
| `ffmpeg bulunamadı` | winget kurulumundan sonra yeni terminal açmadın |
| `FAL_KEY tanımlı değil` | `.env` yok veya boş |
| Transkript segment seviyesinde | `config.yaml` → `transcribe.chunk_level: word` |
| ASR alan adları tutmuyor | Ham yanıt `work/<hash>/asr_raw.json`'da; normalize tek yerde: `transcribe.py:normalize_chunks` |
| Çeviri `max_tokens`'a takılıyor | `translate.batch_size`'ı düşür |
| Türkçe karakterler kutu görünüyor | Font Türkçe desteklemiyor — Arial'a dön veya uygun `.ttf` koy |
| Altyazı Reels UI'ının altında kalıyor | `style.margin_v` değerini artır |
| QA'da çok fazla CPS ihlali | Konuşma hızlı; `cues.max_cps_en` değerini yükselt ya da çeviriyi kısalt (`tone`'a "daha kısa" ekle) |
