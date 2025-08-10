import os
import re
import json
import shutil
import subprocess
from typing import List, Dict


def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def _ffprobe_bin(ffmpeg_bin: str) -> str:
    """
    Cari ffprobe di folder yang sama dengan ffmpeg; jika tidak ada, fallback ke 'ffprobe' di PATH.
    """
    d = os.path.dirname(ffmpeg_bin or "")
    cand = os.path.join(d, "ffprobe.exe" if os.name == "nt" else "ffprobe")
    return cand if os.path.isfile(cand) else "ffprobe"


def srt_to_vtt(
    ffmpeg_bin: str, srt_path: str, outdir: str, basename: str = None
) -> str:
    """
    Konversi SRT eksternal -> WebVTT sidecar.
    Prioritas pakai FFmpeg; kalau gagal, fallback konversi sederhana (tanpa styling ASS).
    Return: path absolut VTT.
    """
    if not os.path.isfile(srt_path):
        raise FileNotFoundError(f"SRT tidak ditemukan: {srt_path}")

    _ensure_dir(outdir)
    if not basename:
        basename = os.path.splitext(os.path.basename(srt_path))[0]
    dst = os.path.join(outdir, f"{basename}.vtt")

    # 1) Coba via FFmpeg
    cmd = [
        ffmpeg_bin or "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        srt_path,
        "-c:s",
        "webvtt",
        dst,
    ]
    try:
        subprocess.run(
            cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT
        )
        return os.path.abspath(dst)
    except Exception:
        # 2) Fallback pure-Python
        with open(srt_path, "r", encoding="utf-8", errors="replace") as f:
            s = f.read()

        # Normalisasi newline
        s = s.replace("\r\n", "\n").replace("\r", "\n")

        # Hapus nomor index di awal blok
        # Contoh:
        # 12
        # 00:00:01,500 --> 00:00:03,000
        s = re.sub(r"(?m)^\s*\d+\s*\n(?=\d{2}:\d{2}:\d{2},\d{3}\s-->)", "", s)

        # Ganti comma -> dot di timestamp
        s = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", s)

        vtt = "WEBVTT\n\n" + s
        with open(dst, "w", encoding="utf-8") as f:
            f.write(vtt)
        return os.path.abspath(dst)


def list_embedded_subs(ffmpeg_bin: str, input_path: str) -> List[Dict]:
    """
    Deteksi subtitle stream embed dengan ffprobe.
    Return list stream dict (index, codec_name, tags).
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input tidak ditemukan: {input_path}")

    ffprobe = _ffprobe_bin(ffmpeg_bin)
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-select_streams",
        "s",
        input_path,
    ]
    out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
    data = json.loads(out or "{}")
    subs = []
    for s in data.get("streams", []):
        subs.append(
            {
                "index": s.get("index"),
                "codec_name": (s.get("codec_name") or "").lower(),
                "tags": s.get("tags", {}) or {},
            }
        )
    return subs


_TEXT_CODECS = {"subrip", "ass", "webvtt", "mov_text"}


def extract_embedded_subs_to_vtt(
    ffmpeg_bin: str, input_path: str, outdir: str, basename: str
) -> List[Dict]:
    """
    Ekstrak subtitle embed TEXT menjadi WebVTT.
    Subtitle image (pgs/dvd_subtitle) di-skip.
    Return daftar dict: {"lang","label","vtt"} nama file relatif terhadap outdir.
    """
    _ensure_dir(outdir)
    subs = list_embedded_subs(ffmpeg_bin, input_path)
    written = []

    for idx, s in enumerate(subs):
        codec = (s.get("codec_name") or "").lower()
        if codec not in _TEXT_CODECS:
            # Skip image-based atau unknown codec
            continue

        lang = (s.get("tags", {}).get("language") or "und").strip()
        label = (s.get("tags", {}).get("title") or lang or "Sub").strip()

        # Nama file unik per bahasa (kalau duplicate lang, tambahkan suffix numerik)
        out_name = f"{basename}.{lang}.vtt"
        dst = os.path.join(outdir, out_name)
        if os.path.isfile(dst):
            # Tambahkan counter biar tidak timpa
            k = 2
            while os.path.isfile(os.path.join(outdir, f"{basename}.{lang}.{k}.vtt")):
                k += 1
            out_name = f"{basename}.{lang}.{k}.vtt"
            dst = os.path.join(outdir, out_name)

        # map argumen stream
        # Prefer index absolut (0:<index>), fallback 0:s:<order>
        map_arg = f"0:{s['index']}" if s.get("index") is not None else f"0:s:{idx}"
        cmd = [
            ffmpeg_bin or "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            input_path,
            "-map",
            map_arg,
            "-c:s",
            "webvtt",
            dst,
        ]
        subprocess.run(cmd, check=True)

        written.append({"lang": lang, "label": label, "vtt": out_name})

    # Kompat: jika hanya ada 1 VTT, salin menjadi <basename>.vtt untuk player generik
    if len(written) == 1:
        single = os.path.join(outdir, written[0]["vtt"])
        compat = os.path.join(outdir, f"{basename}.vtt")
        try:
            if os.path.abspath(single) != os.path.abspath(compat):
                shutil.copyfile(single, compat)
        except Exception:
            pass

    return written
