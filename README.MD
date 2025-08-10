# StreamCode

StreamCode adalah Encoder & Transcoder untuk **HLS** dan **MPEG-DASH** dengan GUI modern (PySide6), preview web (Plyr + hls.js + dash.js), watcher folder otomatis, riwayat job, subtitle SRT/VTT, poster, sprite thumbnails, serta opsi enkripsi **AES-128 HLS**. Target utama Windows.

> Tujuan StreamCode adalah agar proses Encoding & Transcoding menjadi lebih muda dan cepat via lokal yang akan di upload ke Storage.

## Daftar Isi

- [StreamCode](#streamcode)
  - [Daftar Isi](#daftar-isi)
  - [Ringkasan](#ringkasan)
  - [Fitur Utama](#fitur-utama)
  - [Arsitektur Singkat](#arsitektur-singkat)
  - [Persyaratan Sistem](#persyaratan-sistem)
  - [Struktur Proyek](#struktur-proyek)
  - [Setup Pengembangan](#setup-pengembangan)
  - [Menjalankan Aplikasi](#menjalankan-aplikasi)
  - [Menggunakan GUI](#menggunakan-gui)
    - [Toolbar](#toolbar)
    - [Pengaturan Encode](#pengaturan-encode)
    - [Daftar Video](#daftar-video)
    - [Log](#log)
  - [Output \& Struktur Berkas](#output--struktur-berkas)
  - [Subtitle \& Thumbnails](#subtitle--thumbnails)
  - [Enkripsi HLS (AES-128)](#enkripsi-hls-aes-128)
  - [Preview Web (Plyr)](#preview-web-plyr)
  - [Watcher (Folder Otomatis)](#watcher-folder-otomatis)
  - [Riwayat (History)](#riwayat-history)
  - [Konfigurasi \& Preset](#konfigurasi--preset)
  - [Tips Kinerja](#tips-kinerja)
  - [Build Distribusi (cx\_Freeze)](#build-distribusi-cx_freeze)
  - [Troubleshooting](#troubleshooting)
  - [Roadmap Pendek](#roadmap-pendek)
  - [Kredit](#kredit)

## Ringkasan

StreamCode mempermudah pembuatan paket **Adaptive Bitrate Streaming** yang siap disajikan ke web/app:

- **Codec**: H.264 (AVC), HEVC (H.265)
- **Output**: HLS (m3u8) dan/atau MPEG-DASH (mpd)
- **Ladder default**: 2160p, 1440p, 1080p, 720p, 480p (pilih sesuka hati)
- **GPU Accel**: NVENC (opsional)
- **Artifacts**: poster, preview 5–7 dtk, sprite thumbnails VTT
- **Subtitle**: input SRT, auto konversi ke WebVTT; ekstrak subtitle teks dari media kalau diminta
- **Enkripsi**: HLS AES-128 (kunci lokal)
- **Preview**: GUI menyediakan server lokal dan player modern (Plyr) dengan selector kualitas, subtitle, dan thumbnail.
- **Watcher & History**: proses otomatis dari folder; arsip job lengkap bisa dipreview ulang.

## Fitur Utama

- GUI PySide6 tema gelap/terang
- Batch encoding multi-resolusi
- Pilihan HLS, DASH, atau keduanya
- Penamaan rapi + folder per judul
- Notifikasi native Windows
- Log realtime yang enak dibaca
- **Tidak mengubah aspect ratio**: letterbox/pillarbox otomatis bila perlu (tanpa crop)
- Mode portable & build EXE/Folder siap pakai

## Arsitektur Singkat

```bash
main.py         → Aplikasi utama (PySide6)
encode.py      → Worker encoding (FFmpeg orchestration)
server.py      → HTTP preview server (FastAPI/Uvicorn)
utils/
  ├─ thumbs.py     → poster, sprite, VTT thumbnails
  ├─ subtitles.py  → konversi SRT→VTT, ekstrak subtitle embed
  └─ ...
assets/       → app.ico, ikon, dll.
player.html   → halaman plyr + hls.js + dash.js
```

**Catatan path tulis** (mode frozen/EXE): semua data tulis disimpan di
`%LOCALAPPDATA%\StreamCode\` (logs, cache, history DB).

## Persyaratan Sistem

- Windows 10/11 64-bit
- Python 3.12–3.13 (untuk mode dev)
- FFmpeg & FFprobe (disarankan lokasi `C:\ffmpeg\ffmpeg.exe`, `C:\ffmpeg\ffprobe.exe`).
  Bisa dibundel ke folder build.
- GPU NVIDIA (opsional) untuk **NVENC**

## Struktur Proyek

```bash
StreamCode/
├─ assets/
│   └─ app.ico
├─ utils/
│   ├─ thumbs.py
│   └─ subtitles.py
├─ encode.py
├─ server.py
├─ main.py
├─ player.html
├─ requirements.txt
└─ build.py                 # build EXE portable
```

Output pengguna (default): `C:\Users\<Nama>\Documents\StreamCode\...`

## Setup Pengembangan

1. Buat virtual env dan instal dependensi:

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt
```

2. Pastikan FFmpeg terpasang:

```powershell
ffmpeg -hide_banner -version
```

Jika tidak ada, taruh `ffmpeg.exe` dan `ffprobe.exe` ke `C:\ffmpeg\`.

## Menjalankan Aplikasi

**Mode dev (direkomendasikan saat pengembangan):**

```powershell
# terminal 1 (GUI)
python main.py
```

GUI otomatis menyalakan preview server lokal. Bila perlu manual:

```powershell
# terminal 2 (opsional)
python server.py --root "$env:USERPROFILE\Documents\StreamCode" --port 8787
```

## Menggunakan GUI

### Toolbar

- **Pilih Output**: folder hasil encode
- **Tema Gelap/Terang**, **Bersihkan Selesai**, **Bersihkan Log**
- Statistik: Queued | Running | Done | Failed

### Pengaturan Encode

- **Codec**: h264, hevc
- **GPU Accel (NVENC)**: centang jika punya NVIDIA
- **Path SRT (opsional)**: subtitle eksternal
- **Resolusi**: centang yang diinginkan
- **Output**: HLS, DASH
- **Enkripsi AES-128 (HLS)**: opsional
- **Ekstrak subtitle embed**: ambil subtitle teks dari file sumber

### Daftar Video

- Tambah video (button/drag-drop)
- **Mulai Proses**, **Batalkan**, **Preview HLS**, **Preview DASH**
- Tabel menampilkan status, progres %, outputs, codec

### Log

Panel bawah menampilkan log rapi tahap demi tahap.



## Output & Struktur Berkas

Contoh untuk file `Video A.mp4`:

```bash
<OUTPUT_ROOT>\
  Video A\
    job.json
    player.json
    poster.jpg
    thumbs.vtt
    thumbs\
      sprite_0001.jpg
      ...
    HLS\
      2160\segment-*.ts
      1440\...
      1080\...
      720\...
      480\...
      Video A.m3u8
      key.key                 # jika AES-128
      key.keyinfo             # info kunci (lokal)
    DASH\
      2160\chunk_00001.m4s
      1440\...
      1080\...
      720\...
      480\...
      Video A.mpd
    Video A.vtt               # jika ada subtitle
```

`player.json` menyimpan metadata untuk preview (path relatif, daftar kualitas, subtitle, thumbnails).

## Subtitle & Thumbnails

- **SRT eksternal** akan dikonversi otomatis ke **WebVTT**.
- **Ekstrak subtitle embed** (teks) dari sumber akan dicoba terlebih dulu.
- Sprite thumbnails dibuat interval 5–7 dtk dan dirujuk oleh `thumbs.vtt`.
- Plyr akan menampilkan tooltips thumbnail saat hover/seek.

## Enkripsi HLS (AES-128)

- Opsi **Enkripsi AES-128 (HLS)** membuat:

  - `HLS/key.key` dan `HLS/key.keyinfo`
  - M3U8 dirujuk ke key lokal (untuk uji lokal).
- Manajemen kunci **lokal** (dev/testing). Untuk produksi, gantilah mekanisme distribusi kunci sesuai origin/CDN.

## Preview Web (Plyr)

`server.py` menyajikan:

- `/player` dengan query:

  - `?hls=/out/<Base>/HLS/<Base>.m3u8`
  - `?dash=/out/<Base>/DASH/<Base>.mpd`
  - `&vtt=/out/<Base>/<Base>.vtt`
  - `&thumbs=/out/<Base>/thumbs.vtt`
  - `&poster=/out/<Base>/poster.jpg`

Plyr menampilkan selector kualitas (level HLS/DASH), kontrol subtitle, thumbnails, dll.

**Catatan kompatibilitas:**

- **HEVC** di **HLS** tidak diputar Chrome (kebijakan codec). Untuk HEVC, gunakan **DASH** atau pastikan fallback H.264 tersedia.

## Watcher (Folder Otomatis)

Tab **Watcher**:

- Pilih **Folder Input**.
- Pilih codec/resolusi/output seperti encoder.
- Klik **Start Watcher**. Berkas baru yang stabil (tidak berubah ukuran beberapa siklus) akan otomatis diantrikan.

## Riwayat (History)

- Disimpan di SQLite: `%LOCALAPPDATA%\StreamCode\history.db`.
- Tabel menampilkan waktu selesai, judul, codec, outputs, durasi, ukuran total, root.
- **Preview** punya menu **HLS/DASH** (dropdown).
- **Buka Folder** untuk membuka direktori output job.

## Konfigurasi & Preset

Parameter default bit-rate per resolusi (kasar, sesuaikan selera/konten):

| Resolusi | Target Video Bitrate                       |
| -------- | ------------------------------------------ |
| 2160p    | 12–20 Mbps (H.264) / 8–14 Mbps (HEVC)      |
| 1440p    | 8–12 Mbps (H.264) / 6–9 Mbps (HEVC)        |
| 1080p    | 4–6 Mbps (H.264) / 3–5 Mbps (HEVC)         |
| 720p     | 2–3 Mbps (H.264) / 1.5–2.5 Mbps (HEVC)     |
| 480p     | 0.8–1.2 Mbps (H.264) / 0.6–0.9 Mbps (HEVC) |

GOP/Keyint default sekitar **2 detik** (misal 48 frame @24fps). Seluruh preset bisa disesuaikan di `encode.py` bila perlu.

## Tips Kinerja

1. **NVENC** untuk H.264/HEVC mempercepat encoding; kualitas bisa disetel lewat preset NVENC.
2. Untuk **CPU** gunakan x264/x265 preset sesuai kebutuhan (`medium` default cukup aman).
3. Hindari scaling berulang di luar FFmpeg filtergraph; gunakan filter split + scale sekali jalan.
4. Simpan output di SSD NVMe untuk I/O segment.

## Build Distribusi (cx\_Freeze)

Folder portable (tanpa PyInstaller).

1. Instal alat:

```powershell
python -m pip install -U "cx-Freeze>=7.2"
```

2. **setup\_cxfreeze.py** sudah disediakan. Bangun:

```powershell
Remove-Item -Recurse -Force build -ErrorAction SilentlyContinue
python .\setup_cxfreeze.py build
```

3. Jalankan:

```powershell
.\build\exe.win-amd64-3.12\StreamCode.exe
```

**Catatan build:**

- Semua executable dibuat **Win32GUI** agar tidak membuka console.
- Proses anak (server/encoder/FFmpeg) dijalankan **tanpa console**.
- Plugin Qt dan `opengl32sw.dll` dibundel agar aman di mesin target.
- File tulis diarahkan ke `%LOCALAPPDATA%\StreamCode`, bukan ke `lib\library.zip`.

## Troubleshooting

**Aplikasi muncul console saat encode**
Pastikan patch “no console” di `main.py` dan *monkey-patch* `subprocess` di `encode.py` aktif. Rebuild.

**Port 8787 sudah dipakai / `WinError 10048`**
Ubah port di GUI (otomatis memilih bebas), atau kill proses server sebelumnya.

**`WinError 740` (minta elevasi) saat FFmpeg**
Jangan pasang `ffmpeg.exe` sebagai “Run as administrator”. Taruh di lokasi biasa (`C:\ffmpeg`) atau biarkan aplikasi memakainya dari PATH.

**`manifestIncompatibleCodecsError` di preview HLS**
Browser tidak mendukung codec (misal HEVC di HLS di Chrome). Gunakan H.264 untuk HLS atau preview **DASH**.

**DASH: `Conflicting stream aspect ratios`**
Semua rendisi sudah diset **setsar=1** dan **format=yuv420p** di filtergraph. Jika kamu modifikasi filter, pastikan semua rendisi punya SAR identik.

**Aspect ratio salah (crop terpotong)**
Filter yang dipakai **force\_original\_aspect\_ratio=decrease** + letterbox/pillarbox, tidak crop. Pastikan tidak menimpa filter default di `encode.py`.

**`'charmap' codec can't encode character`**
Nama file berisi karakter yang tidak bisa masuk codepage konsol. Semua I/O sudah pakai UTF-8; pastikan environment tidak menimpa encoding. Atau ganti nama file.

**Qt “platform plugin windows”**
Pastikan folder `PySide6/plugins` ikut terbawa. Script build sudah menyalin otomatis.

## Roadmap Pendek

- Preset ladder adaptif per konten
- DRM tingkat lanjut (FairPlay/Widevine/CENC)
- Template job/presets yang dapat disimpan
- Ekspor laporan batch (CSV/JSON)
- Headless CLI mode terpisah (tanpa GUI)

## Kredit

- **FFmpeg** untuk proses transcode
- **PySide6** untuk GUI
- **FastAPI/Uvicorn** untuk preview server
- **Plyr**, **hls.js**, **dash.js** untuk pemutar web
