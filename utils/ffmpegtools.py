import os, secrets, subprocess, pathlib, shutil
from typing import List, Dict, Optional


def ensure_dir(p: str):
    pathlib.Path(p).mkdir(parents=True, exist_ok=True)


def make_hls_key(out_dir: str):
    ensure_dir(out_dir)
    key_path = os.path.join(out_dir, "key.key")
    with open(key_path, "wb") as f:
        f.write(secrets.token_bytes(16))
    keyinfo_path = os.path.join(out_dir, "key.keyinfo")
    uri = "key.key"
    iv_hex = secrets.token_hex(16)
    with open(keyinfo_path, "w", newline="\n") as f:
        f.write(uri + "\n")
        f.write(key_path + "\n")
        f.write(iv_hex + "\n")
    return keyinfo_path


def build_scale_filter(w: int, h: int):
    return f"scale=w={w}:h={h}:force_original_aspect_ratio=decrease"


def _resolve_ffprobe(ffmpeg_path: str) -> Optional[str]:
    d = os.path.dirname(ffmpeg_path)
    cands = []
    if d:
        cands.append(os.path.join(d, "ffprobe.exe"))
    cands.append("ffprobe")
    for c in cands:
        if os.path.isfile(c):
            return c
        w = shutil.which(c)
        if w:
            return w
    return None


def probe_duration_ms(ffmpeg: str, inp: str) -> Optional[int]:
    ffprobe = _resolve_ffprobe(ffmpeg)
    if not ffprobe:
        return None
    try:
        # returns seconds as float
        out = subprocess.check_output(
            (
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=nw=1:nk=1",
                    "-i",
                    inp,
                ]
                if ffprobe.endswith(".exe")
                else [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=nw=1:nk=1",
                    inp,
                ]
            ),
            universal_newlines=True,
        ).strip()
        sec = float(out)
        return int(sec * 1000)
    except Exception:
        return None


def run(cmd: List[str], progress: Optional[dict] = None):
    """
    Jalankan FFmpeg dan cetak stdout ter-stream.
    Jika progress diberikan: {total_ms:int|None, base:str, label:str}
    maka saat menerima baris 'out_time_ms=' akan cetak:
      PROGRESS base=<base> rend=<label> pct=<xx.xx>
    """
    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1,
    )
    total_ms = progress.get("total_ms") if progress else None
    base = progress.get("base") if progress else None
    label = progress.get("label") if progress else None

    for raw in p.stdout:
        line = raw.strip()
        if progress and line.startswith("out_time_ms="):
            try:
                out_ms = int(line.split("=", 1)[1])
                if total_ms and total_ms > 0:
                    pct = max(0.0, min(100.0, (out_ms / total_ms) * 100.0))
                else:
                    pct = 0.0
                print(f"PROGRESS base={base} rend={label} pct={pct:.2f}", flush=True)
            except Exception:
                pass
        else:
            print(line, flush=True)
    p.wait()
    if p.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {p.returncode}")


def hls_per_rendition(
    ffmpeg: str,
    inp: str,
    outdir: str,
    vcodec: str,
    acodec: str,
    ladder: Dict,
    gop: int,
    fps: int,
    ar: int,
    ab_k: int,
    channels: int,
    keyinfo: str | None,
    total_ms: Optional[int],
    base: str,
):
    rname = ladder["name"]
    w, h = ladder["width"], ladder["height"]
    rdir = os.path.join(outdir, rname)
    ensure_dir(rdir)
    args = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-i",
        inp,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-c:v",
        vcodec,
        "-b:v",
        f'{ladder["bitrate_k"]}k',
        "-maxrate",
        f'{ladder["maxrate_k"]}k',
        "-bufsize",
        f'{ladder["bufsize_k"]}k',
        "-r",
        str(fps),
        "-g",
        str(gop * fps),
        "-keyint_min",
        str(gop * fps),
        "-sc_threshold",
        "0",
        "-vf",
        build_scale_filter(w, h),
        "-c:a",
        acodec,
        "-b:a",
        f"{ab_k}k",
        "-ac",
        str(channels),
        "-ar",
        str(ar),
        "-hls_time",
        str(gop * 2),
        "-hls_playlist_type",
        "vod",
        "-hls_flags",
        "independent_segments",
        "-hls_segment_filename",
        os.path.join(rdir, "seg_%05d.ts"),
        "-progress",
        "pipe:1",
        "-nostats",
        os.path.join(rdir, "index.m3u8"),
    ]
    if keyinfo:
        args.insert(-2, "-hls_key_info_file")
        args.insert(-2, keyinfo)
    run(args, progress={"total_ms": total_ms, "base": base, "label": rname})
    return rname


def write_hls_master(
    outdir: str, master_name: str, ladders: List[Dict], audio_kbps: int, codec_id: str
):
    vtag = "avc1.640029" if codec_id == "h264" else "hvc1"
    atag = "mp4a.40.2"
    master_path = os.path.join(outdir, master_name)
    lines = ["#EXTM3U"]
    for L in ladders:
        bw = (L["maxrate_k"] + audio_kbps) * 1000
        res = f'{L["width"]}x{L["height"]}'
        lines += [
            f'#EXT-X-STREAM-INF:BANDWIDTH={bw},RESOLUTION={res},CODECS="{vtag},{atag}"',
            f'{L["name"]}/index.m3u8',
        ]
    with open(master_path, "w", newline="\n") as f:
        f.write("\n".join(lines))
    return master_path


def dash_multi(
    ffmpeg: str,
    inp: str,
    outdir: str,
    mpd_name: str,
    ladders: List[Dict],
    vcodec: str,
    acodec: str,
    gop: int,
    fps: int,
    ar: int,
    ab_k: int,
    channels: int,
    total_ms: Optional[int],
    base: str,
):
    ensure_dir(outdir)
    n = len(ladders)
    split = f"[0:v]split={n}" + "".join([f"[v{i}]" for i in range(n)]) + ";"
    scales = []
    for i, L in enumerate(ladders):
        scales.append(
            f"[v{i}]scale=w={L['width']}:h={L['height']}:force_original_aspect_ratio=decrease[v{i}o]"
        )
    fc = split + "".join(scales)
    args = [ffmpeg, "-y", "-hide_banner", "-i", inp, "-filter_complex", fc]
    for i, L in enumerate(ladders):
        args += ["-map", f"[v{i}o]"]
        args += [
            "-c:v:" + str(i),
            vcodec,
            "-b:v:" + str(i),
            f"{L['bitrate_k']}k",
            "-maxrate:v:" + str(i),
            f"{L['maxrate_k']}k",
            "-bufsize:v:" + str(i),
            f"{L['bufsize_k']}k",
            "-r",
            str(fps),
            "-g",
            str(gop * fps),
            "-keyint_min",
            str(gop * fps),
            "-sc_threshold",
            "0",
        ]
    args += [
        "-map",
        "0:a:0",
        "-c:a",
        acodec,
        "-b:a",
        f"{ab_k}k",
        "-ac",
        str(channels),
        "-ar",
        str(ar),
    ]
    args += [
        "-use_timeline",
        "1",
        "-use_template",
        "1",
        "-seg_duration",
        str(gop * 2),
        "-adaptation_sets",
        "id=0,streams=v id=1,streams=a",
        "-progress",
        "pipe:1",
        "-nostats",
        "-f",
        "dash",
        os.path.join(outdir, mpd_name),
    ]
    # label "DASH" untuk progress agregat
    run(args, progress={"total_ms": total_ms, "base": base, "label": "dash"})
    return os.path.join(outdir, mpd_name)
