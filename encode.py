import os, sys, argparse, json, subprocess, shutil
from pathlib import Path
from datetime import datetime
import xml.etree.ElementTree as ET
from fractions import Fraction


def _install_no_console_wrapper():
    if os.name != "nt":
        return

    CREATE_NO_WINDOW = 0x08000000
    STARTF_USESHOWWINDOW = 0x00000001
    SW_HIDE = 0

    _si = subprocess.STARTUPINFO()
    _si.dwFlags |= STARTF_USESHOWWINDOW
    _si.wShowWindow = SW_HIDE

    _orig_popen = subprocess.Popen
    _orig_run = subprocess.run

    def _popen_no_window(*args, **kwargs):
        kwargs.setdefault("startupinfo", _si)
        kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
        return _orig_popen(*args, **kwargs)

    def _run_no_window(*args, **kwargs):
        kwargs.setdefault("startupinfo", _si)
        kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
        return _orig_run(*args, **kwargs)

    subprocess.Popen = _popen_no_window
    subprocess.run = _run_no_window


_install_no_console_wrapper()


# -------- console UTF-8 (Windows) --------
def _force_utf8_stdio():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


_force_utf8_stdio()

# -------- optional subtitle helpers --------
try:
    from utils.subtitles import srt_to_vtt, extract_embedded_subs_to_vtt  # noqa
except Exception:

    def srt_to_vtt(ffmpeg_bin, srt_path, outdir, basename=None):
        return None

    def extract_embedded_subs_to_vtt(ffmpeg_bin, input_path, outdir, basename):
        return []


# -------- ladder default (16:9) --------
RES_MAP = {
    "2160": (3840, 2160, 12000_000),
    "1440": (2560, 1440, 8000_000),
    "1080": (1920, 1080, 5000_000),
    "720": (1280, 720, 2800_000),
    "480": (854, 480, 1400_000),
}


# -------- utils --------
def which_ffmpeg(bin_path: str | None) -> str:
    if bin_path:
        return bin_path
    return "ffmpeg.exe" if os.name == "nt" else "ffmpeg"


def which_ffprobe(ffmpeg_bin: str | None) -> str:
    if not ffmpeg_bin:
        return "ffprobe.exe" if os.name == "nt" else "ffprobe"
    p = Path(ffmpeg_bin)
    cand = p.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    return (
        str(cand)
        if cand.exists()
        else ("ffprobe.exe" if os.name == "nt" else "ffprobe")
    )


def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def run(cmd: list[str], cwd: Path | None = None):
    print(" ".join(cmd), flush=True)
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {proc.returncode}")


def ms(ts: float) -> int:
    return int(round(ts * 1000))


def make_poster(ffmpeg: str, inp: Path, outdir: Path) -> str | None:
    try:
        dst = outdir / "poster.jpg"
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-y",
            "-ss",
            "3",
            "-i",
            str(inp),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(dst),
        ]
        run(cmd)
        return str(dst.relative_to(outdir))
    except Exception:
        return None


def write_json(path: Path, data: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def probe_src_wh(ffprobe: str, input_path: Path) -> tuple[int, int]:
    try:
        out = subprocess.check_output(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0:s=x",
                str(input_path),
            ],
            text=True,
        ).strip()
        w, h = out.split("x")
        return int(w), int(h)
    except Exception:
        return (1920, 1080)


def probe_dar(ffprobe: str, input_path: Path) -> str:
    """
    Kembalikan 'num:den' untuk Display Aspect Ratio sumber.
    Prefer display_aspect_ratio; fallback dari width,height dan SAR.
    """
    try:
        out = subprocess.check_output(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,sample_aspect_ratio,display_aspect_ratio",
                "-of",
                "json",
                str(input_path),
            ],
            text=True,
        )
        info = json.loads(out)["streams"][0]
        dar = (info.get("display_aspect_ratio") or "").strip()
        if dar and dar not in ("0:1", "N/A", "unknown"):
            return dar
        w = int(info.get("width") or 1920)
        h = int(info.get("height") or 1080)
        sar = (info.get("sample_aspect_ratio") or "1:1").strip()
        try:
            sn, sd = sar.split(":")
            sn, sd = int(sn), int(sd)
            if sn <= 0 or sd <= 0:
                sn, sd = 1, 1
        except Exception:
            sn, sd = 1, 1
        frac = Fraction(w, h) * Fraction(sn, sd)
        frac = frac.limit_denominator(1000)
        return f"{frac.numerator}:{frac.denominator}"
    except Exception:
        return "16:9"


def even(x: int) -> int:
    return x if x % 2 == 0 else x - 1


# -------- targets & filters --------
def build_targets(renditions: list[str], mode: str, src_w: int, src_h: int):
    """
    mode = 'source' -> pertahankan AR sumber: width = round_even(height * src_w/src_h)
           'fixed'  -> pakai RES_MAP mentah (16:9)
    Return: [(w,h,br,name)] besar->kecil
    """
    t = []
    if mode == "source":
        ar = (src_w / src_h) if src_h else (16 / 9)
        for r in renditions:
            if r not in RES_MAP:
                continue
            _W, _H, br = RES_MAP[r]
            H = _H
            W = even(int(round(H * ar)))
            t.append((W, H, br, f"{H}p"))
    else:
        for r in renditions:
            if r in RES_MAP:
                w, h, br = RES_MAP[r]
                t.append((w, h, br, f"{h}p"))
    t.sort(key=lambda x: x[1], reverse=True)
    return t


def build_filter_complex_for_targets(targets):
    """
    Skala langsung ke (w,h) yang sudah dihitung. Tidak crop/pad.
    """
    n = len(targets)
    split = f"[0:v]split={n}" + "".join(f"[v{i}]" for i in range(n))
    chains, out_labels = [], []
    for i, (w, h, _br, _name) in enumerate(targets):
        out_lab = f"v{i}o"
        chains.append(
            f"[v{i}]scale=w={w}:h={h}:flags=bicubic,format=yuv420p,setsar=1[{out_lab}]"
        )
        out_labels.append(out_lab)
    fc = split + ";" + ";".join(chains)
    return fc, out_labels


def add_stream_opts_one(args: list[str], vcodec: str, br: int):
    maxr = int(br * 1.07)
    buf = maxr * 2
    args += [
        "-c:v",
        vcodec,
        "-b:v",
        str(br),
        "-maxrate",
        str(maxr),
        "-bufsize",
        str(buf),
        "-g",
        "48",
        "-keyint_min",
        "48",
        "-sc_threshold",
        "0",
        "-preset",
        "medium",
    ]
    args += ["-c:a", "aac", "-b:a", "128k", "-ac", "2"]


# -------- HLS helpers --------
def write_hls_master(hls_dir: Path, base: str, targets):
    lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    for w, h, br, _n in targets:
        bw = int(br + 128_000)
        lines.append(f"#EXT-X-STREAM-INF:BANDWIDTH={bw},RESOLUTION={w}x{h}")
        lines.append(f"{h}/index.m3u8")
    (hls_dir / f"{base}.m3u8").write_text("\n".join(lines) + "\n", encoding="utf-8")


# -------- main --------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--codec", choices=["h264", "hevc"], default="h264")
    ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--renditions", default="1080,720,480")
    ap.add_argument("--no-hls", dest="hls", action="store_false")
    ap.add_argument("--no-dash", dest="dash", action="store_false")
    ap.add_argument("--encrypt", dest="encrypt_hls", action="store_true")
    ap.add_argument("--no-encrypt", dest="encrypt_hls", action="store_false")
    ap.add_argument("--extract-subs", action="store_true")
    ap.add_argument("--srt")
    ap.add_argument("--ffmpeg-bin")
    ap.add_argument("--ar-mode", choices=["source", "fixed"], default="source")
    ap.set_defaults(hls=True, dash=True, encrypt_hls=False)
    args = ap.parse_args()

    inp = Path(args.input).resolve()
    if not inp.exists():
        print(f"ERROR: input not found: {inp}", file=sys.stderr)
        sys.exit(2)

    ffmpeg = which_ffmpeg(args.ffmpeg_bin)
    ffprobe = which_ffprobe(args.ffmpeg_bin)
    base = inp.stem
    out_root = Path(args.outdir).resolve()
    workdir = out_root / base
    ensure_dir(workdir)

    print(f"[encode] start -> {inp.name}", flush=True)
    print(
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | INFO | __main__:main - Using ffmpeg: {ffmpeg}",
        flush=True,
    )

    # poster
    poster_rel = make_poster(ffmpeg, inp, workdir)

    # subtitles
    tracks = []
    if args.srt:
        try:
            vtt = srt_to_vtt(ffmpeg, args.srt, str(workdir), basename=base)
            if vtt:
                tracks.append(
                    {"lang": "und", "label": "Subtitles", "src": Path(vtt).name}
                )
        except Exception as e:
            print(f"[subs] srt_to_vtt failed: {e}", flush=True)
    if args.extract_subs:
        try:
            lst = extract_embedded_subs_to_vtt(ffmpeg, str(inp), str(workdir), base)
            for it in lst:
                tracks.append(
                    {
                        "lang": it.get("lang", "und"),
                        "label": it.get("label", "Sub"),
                        "src": it.get("vtt"),
                    }
                )
        except Exception as e:
            print(f"[subs] extract_embedded failed: {e}", flush=True)

    # targets berdasar AR sumber (default)
    src_w, src_h = probe_src_wh(ffprobe, inp)
    dar_str = probe_dar(ffprobe, inp)
    renditions = [r.strip() for r in args.renditions.split(",") if r.strip()]
    targets = build_targets(renditions, mode=args.ar_mode, src_w=src_w, src_h=src_h)
    if not targets:
        print("ERROR: no valid renditions", file=sys.stderr)
        sys.exit(3)

    # codec
    if args.codec == "h264":
        vcodec = "h264_nvenc" if args.gpu else "libx264"
    else:
        vcodec = "hevc_nvenc" if args.gpu else "libx265"

    sources = {}
    duration_ms = 0

    # ================= HLS =================
    if args.hls:
        print("[hls] start", flush=True)
        hls_dir = workdir / "HLS"
        ensure_dir(hls_dir)
        for _w, h, _br, _n in targets:
            ensure_dir(hls_dir / f"{h}")

        fc, out_labels = build_filter_complex_for_targets(targets)
        cmd = [ffmpeg, "-hide_banner", "-y", "-i", str(inp), "-filter_complex", fc]

        key_path, key_iv = (None, None)
        if args.encrypt_hls:
            key_path = hls_dir / "enc.key"
            key_path.write_bytes(os.urandom(16))
            key_iv = os.urandom(16).hex()

        for i, lab in enumerate(out_labels):
            w, h, br, name = targets[i]
            cmd += ["-map", f"[{lab}]", "-map", "a:0?"]
            add_stream_opts_one(cmd, vcodec, br)
            cmd += ["-aspect", dar_str]
            cmd += [
                "-f",
                "hls",
                "-hls_time",
                "4",
                "-hls_playlist_type",
                "vod",
                "-hls_flags",
                "independent_segments",
            ]
            if args.encrypt_hls:
                ki = hls_dir / f"_key_{h}.txt"
                ki.write_text(
                    "../enc.key\n" + str(key_path) + "\n" + key_iv + "\n",
                    encoding="utf-8",
                )
                cmd += ["-hls_key_info_file", str(ki)]
            cmd += [
                "-hls_segment_filename",
                str(hls_dir / f"{h}" / "seg_%05d.ts"),
                str(hls_dir / f"{h}" / "index.m3u8"),
            ]

        run(cmd)
        write_hls_master(hls_dir, base, targets)
        sources["hls"] = f"HLS/{base}.m3u8"
        print("[hls] done", flush=True)

    # ================= DASH =================
    if args.dash:
        print("[dash] start", flush=True)
        dash_dir = workdir / "DASH"
        ensure_dir(dash_dir)

        # Pre-create folder numerik untuk semua rep (v,a,v,a,...)
        total_reps = len(targets) * 2
        for i in range(total_reps):
            ensure_dir(dash_dir / str(i))

        fc, out_labels = build_filter_complex_for_targets(targets)
        cmd = [ffmpeg, "-hide_banner", "-y", "-i", str(inp), "-filter_complex", fc]

        # Map & codec per-rep ke satu output DASH
        for i, lab in enumerate(out_labels):
            br = targets[i][2]
            cmd += ["-map", f"[{lab}]", "-map", "a:0?"]
            maxr = int(br * 1.07)
            buf = maxr * 2
            cmd += [
                f"-c:v:{i}",
                vcodec,
                f"-b:v:{i}",
                str(br),
                f"-maxrate:v:{i}",
                str(maxr),
                f"-bufsize:v:{i}",
                str(buf),
                f"-g:v:{i}",
                "48",
                "-keyint_min",
                "48",
                f"-sc_threshold:v:{i}",
                "0",
                f"-preset:v:{i}",
                "medium",
                f"-aspect:v:{i}",
                dar_str,
                f"-c:a:{i}",
                "aac",
                f"-b:a:{i}",
                "128k",
                f"-ac:a:{i}",
                "2",
            ]

        mpd_name = f"{base}.mpd"
        cmd += [
            "-f",
            "dash",
            "-use_timeline",
            "1",
            "-use_template",
            "1",
            "-seg_duration",
            "4",
            "-adaptation_sets",
            "id=0,streams=v id=1,streams=a",
            "-init_seg_name",
            "$RepresentationID$/init.m4s",
            "-media_seg_name",
            "$RepresentationID$/chunk_$Number%05d$.m4s",
            mpd_name,
        ]

        run(cmd, cwd=dash_dir)
        mpd_path = dash_dir / mpd_name
        try:
            tree = ET.parse(mpd_path)
            root = tree.getroot()
            ns = ""
            if root.tag.startswith("{"):
                ns = root.tag.split("}")[0].strip("{")

            def q(tag):
                return f"{{{ns}}}{tag}" if ns else tag

            video_as = None
            for a in root.findall(".//" + q("AdaptationSet")):
                ctype = (a.get("contentType") or "") + " " + (a.get("mimeType") or "")
                if "video" in ctype:
                    video_as = a
                    break
            if video_as is None:
                video_as = root.find(".//" + q("AdaptationSet"))

            mapping = []
            reps = (
                list(video_as.findall(q("Representation")))
                if video_as is not None
                else []
            )
            # Petakan urutan height target terbesar->kecil ke id numerik
            heights_sorted = [str(t[1]) for t in targets]
            for idx, rep in enumerate(reps):
                rid = rep.get("id") or str(idx * 2)
                h = rep.get("height") or (
                    heights_sorted[idx] if idx < len(heights_sorted) else ""
                )
                if rid and h:
                    mapping.append((rid, h))
                    rep.set("id", str(h))
                    w = next((t[0] for t in targets if str(t[1]) == str(h)), "")
                    if w:
                        rep.set("width", str(w))
                        rep.set("height", str(h))

            tree.write(mpd_path, encoding="utf-8", xml_declaration=True)
            for rid, h in mapping:
                src = dash_dir / rid
                dst = dash_dir / str(h)
                if src.exists() and src.is_dir():
                    if dst.exists():
                        for rootdir, _dirs, files in os.walk(src):
                            rel = Path(rootdir).relative_to(src)
                            (dst / rel).mkdir(parents=True, exist_ok=True)
                            for f in files:
                                os.replace(
                                    os.path.join(rootdir, f), str((dst / rel) / f)
                                )
                        shutil.rmtree(src, ignore_errors=True)
                    else:
                        os.replace(src, dst)
        except Exception as e:
            print(f"[dash] mpd rewrite warn: {e}", flush=True)

        sources["dash"] = f"DASH/{base}.mpd"
        print("[dash] done", flush=True)

    # durasi
    try:
        out = subprocess.check_output(
            [
                which_ffprobe(ffmpeg),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(inp),
            ],
            text=True,
        ).strip()
        duration_ms = ms(float(out))
    except Exception:
        duration_ms = 0

    # job.json
    jobj = {
        "input": str(inp),
        "outdir": str(out_root),
        "codec": args.codec,
        "gpu": bool(args.gpu),
        "renditions": [f"{t[1]}p" for t in targets],
        "hls": bool(args.hls),
        "dash": bool(args.dash),
        "encrypt_hls": bool(args.encrypt_hls),
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    write_json(workdir / "job.json", jobj)

    # player.json
    meta = {
        "title": base,
        "codec": args.codec,
        "duration_ms": duration_ms,
        "poster": poster_rel or "",
        "sources": sources,
        "tracks": {"subtitles": tracks} if tracks else {},
        "thumbnails": "thumbs.vtt" if (workdir / "thumbs.vtt").exists() else "",
        "renditions": [
            {"name": f"{t[1]}p", "w": t[0], "h": t[1], "br": t[2]} for t in targets
        ],
    }
    write_json(workdir / "player.json", meta)

    print(f"JOB_DONE base={base}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error : {e}", flush=True)
        sys.exit(1)
