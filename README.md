# subpipe

İngilizce videoya **hem Türkçe hem İngilizce** altyazıyı otomatik ekleyip videoya yakan pipeline.
9:16 dikey (Reels / TikTok / Shorts) için ayarlı.

```
video → ses → fal.ai Whisper → yeniden segmentasyon → Claude çeviri → ASS → ffmpeg → MP4
```

Türkçe üstte beyaz ve kalın, İngilizce altta altın sarısı destek satırı olarak yakılır.
Her aşama cache'lenir, böylece stil denemeleri transkripsiyon/çeviri parasını tekrar ödetmez.

---

## Neden bir araç gerekiyor

Whisper'ı çağırıp çıktısını SRT'ye dökmek 20 satırlık bir iş. Sonuç ise kullanılamaz olur:

| Sorun | subpipe ne yapıyor |
|---|---|
| ASR segmentleri okuma için değil, tanıma için optimize | Kelime timestamp'lerinden altyazı kurallarına uyan cue'lar **yeniden kuruluyor** |
| Cue bazlı çeviri Türkçede bozuluyor (SOV vs SVO) | Cümle bazlı, bağlamlı çeviri; sonuç cue'lara **oransal dağıtılıyor** |
| Sessizlikte "Thanks for watching" halüsinasyonu | Kalıp + tekrar + süre sezgisiyle **filtreleniyor**, QA raporunda listeleniyor |
| Telefon videosu -24 LUFS, platform -14 bekliyor | İki geçişli **EBU R128 normalizasyonu** |
| Telefon dikey videoyu yatay depolayıp döndürüyor | Display Matrix okunup **en/boy takas ediliyor** |
| Stil değiştirmek her seferinde baştan işlem | Aşama bazlı **fingerprint cache** |

---

## Kurulum

Gereksinimler: Python 3.11+, ffmpeg, bir fal.ai ve bir Anthropic API anahtarı.

```powershell
winget install --id Gyan.FFmpeg -e     # sonra YENİ terminal aç (PATH güncellenir)

git clone https://github.com/enesmemduhoglu/subpipe.git
cd subpipe
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

Çıktılar `out/` altına düşer: `video_sub.mp4` + `video.tr.srt` / `video.en.srt` / `.vtt`.

### Aşamalar tek tek

```powershell
python -m subpipe transcribe video.mp4    # ses çıkarma + ASR
python -m subpipe segment   video.mp4     # cue'lara böl
python -m subpipe translate video.mp4     # Claude ile çevir
python -m subpipe build     video.mp4     # ASS/SRT/VTT + QA raporu
python -m subpipe render    video.mp4     # videoya yak
python -m subpipe qa        video.mp4     # QA raporunu ekrana bas
```

`config.yaml`'da punto değiştirip `render` çalıştırırsan **sadece ASS yeniden üretilir** —
transkripsiyon ve çeviri cache'ten gelir. Cache'i yok saymak için `--force segment`.

### Önerilen akış

1. `build` — ASS ve QA raporu çıkar
2. `work/<hash>/qa.md`'ye bak: okuma hızı, satır uzunluğu, atılan halüsinasyonlar
3. Gerekirse `work/<hash>/subs.ass`'i Aegisub / Subtitle Edit ile elle düzelt
4. `render --preview` — telefonda kontrol et
5. `render` — final

---

## Nasıl çalışıyor

### Yeniden segmentasyon — `stages/segment.py`

Pipeline'ın en kritik modülü. Whisper'ın kendi segmentleri doğrudan ASS'e dökülürse
okunamayacak hızda geçen, cümle ortasından bölünmüş cue'lar çıkar. Burada kelime
timestamp'lerinden yeniden kuruluyor:

- satır başına 26 karakter, en fazla 2 satır
- 0.85–7.0 s süre, max 20 CPS
- bölme noktaları puanlanıyor: noktalama > uzun duraklama > bağlaç öncesi.
  Artikel, edat ve özne zamiri sonrası bölme cezalandırılıyor (`the |`, `today we |`)
- `Mr.` / `Dr.` / `vs.` gibi kısaltmalar cümle sonu sayılmıyor
- halüsinasyon filtresi: hosted ASR'de VAD düğmesi yok, bu yüzden burada

### Çeviri — `stages/translate.py`

Cue bazlı **çevirmiyoruz.** Türkçe SOV, İngilizce SVO — üç cue'ya yayılmış bir cümleyi
parça parça çevirmek anlamsız metin üretir.

Cue'lar `sentence_id` üzerinden tam cümleye toplanıyor, komşu cümleler salt-bağlam olarak
eklenip Claude'a batch halinde gönderiliyor (structured output → ID hizalaması garanti),
dönen Türkçe metin o cümlenin cue'larına karakter payına göre dağıtılıyor.

Zaman çizelgesinin sahibi İngilizce cue'ları — iki dil aynı anda ekranda olacağı için
ortak zaman çizelgesi şart.

Sistem promptu sabit ve `cache_control: ephemeral` ile cache'leniyor. `config.yaml`'daki
`video_context`, `tone` ve `glossary` çeviri kalitesini en çok etkileyen ayarlar.

### ASS — `stages/ass.py`

İki dil **tek Dialogue satırında**, inline stil reset (`{\rEN}`) ile. İki ayrı Dialogue +
`MarginV` hesabı yerine bu tercih edildi: satır sayısı değiştiğinde stack kaymıyor.

- `PlayResX/PlayResY` ffprobe'dan, **döndürme matrisi hesaba katılarak**
- `WrapStyle: 2` — libass'in otomatik sarması kapalı, satırları biz bölüyoruz
- `style.gap` — iki dil arası boşluk (ASS'de satır aralığı etiketi yok; araya boş satır
  konup yüksekliği `\fs` ile ayarlanıyor)
- `style.fade_in/out` — `\fad` ile yumuşak geçiş

Punto ve kenar boşlukları `style.reference_height`'e (1920) göre yazılıyor ve gerçek video
yüksekliğine otomatik ölçekleniyor.

> ⚠️ `WrapStyle: 2` sarma yapmadığı için **taşan satır kesilir.** Punto büyütürken
> `cues.max_chars_per_line` düşmeli — `ass` aşaması hesaplayıp uyarı basar.

### Render — `stages/render.py`

Windows tuzağı: `subtitles=` filtresi `C:\...` yolunu parse edemez (`:` filtre ayracı).
Mutlak yolu escape'lemek yerine ffmpeg ASS dosyasının dizininde çalıştırılıp **göreli**
dosya adı veriliyor; fontlar da oraya kopyalanıyor.

Final: `libx264 -crf 18 -preset medium`, `-movflags +faststart`. Önizleme NVENC ile.

**Ses normalizasyonu.** Telefon videoları genelde -24 LUFS civarında; Instagram/TikTok
~-14 LUFS'a göre karıştırdığı için akışta "sesi yok" gibi duyuluyor. İki geçişli
`loudnorm` kullanılıyor (ölçüm `work/<hash>/loudness.json`'a cache'lenir), tek geçişliden
daha doğru ve pompalamıyor. `render.normalize_audio: false` ile kapatılır.

---

## Font

Varsayılan **Arial** — her Windows'ta var, Türkçe ş ğ ı İ ö ü ç destekler, kutudan çıkar
çıkmaz çalışır. Daha iyisi için `.ttf` dosyasını `assets/fonts/` altına at ve
`style.font_name`'i değiştir (ör. `Montserrat SemiBold`).

> Font bulunamazsa libass **sessizce** başkasına düşer. `assets/fonts/` boşsa render
> uyarı basar — yine de önizlemeden gözle kontrol et.

## Yatay (16:9) videoya geçmek

Punto otomatik ölçeklendiği için iki ayar yeter:

```yaml
cues:
  max_chars_per_line: 42     # yatayda satır daha geniş
style:
  margin_v: 60               # Reels UI yok, dibe yaklaş
```

---

## Sorun giderme

| Belirti | Sebep / çözüm |
|---|---|
| `ffmpeg bulunamadı` | winget kurulumundan sonra yeni terminal açmadın |
| `FAL_KEY tanımlı değil` | `.env` yok veya boş |
| `credit balance is too low` | Anthropic hesabında kredi yok |
| Transkript tek dev cue | `transcribe.model` `fal-ai/wizper` olmuş; `fal-ai/whisper` yap |
| Videoda ses çok kısık | Kaynak muhtemelen -24 LUFS. `render.normalize_audio: true` (varsayılan) |
| Altyazı yanlış boyutta / yerde | Döndürme matrisi: `ffprobe -show_entries stream_side_data=rotation` |
| Satır ekrandan taşıyor | `ass` aşamasının uyarısındaki `max_chars_per_line` değerini kullan |
| Satır sayısı `max_lines`'ı aşıyor | `max_chars_per_line`'ı 1–2 artır |
| Özel isimler yanlış | `transcribe.prompt`; yetmezse `transcribe.replacements` |
| Türkçe karakterler kutu | Font Türkçe desteklemiyor — Arial'a dön |
| Kod değişti, cache eski sonucu veriyor | `cli.py`'deki `STAGE_VERSION`'ı artır |

## Not

`fal-ai/wizper` **kullanma.** `chunk_level` için sadece `"segment"` kabul ediyor ve
pratikte tüm videoyu tek chunk olarak döndürüyor — altyazı için kullanılamaz. Kelime
seviyesi timestamp veren endpoint `fal-ai/whisper`.

## Lisans

MIT
