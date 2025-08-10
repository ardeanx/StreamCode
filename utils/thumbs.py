import os, subprocess


def poster(ffmpeg: str, inp: str, out_dir: str):
    out = os.path.join(out_dir, "poster.jpg")
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-ss",
        "00:00:03",
        "-i",
        inp,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        out,
    ]
    subprocess.run(cmd, check=True)
    return out


def preview_clip(ffmpeg: str, inp: str, out_dir: str, seconds: int = 6):
    out = os.path.join(out_dir, "preview.mp4")
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-ss",
        "00:00:05",
        "-t",
        str(seconds),
        "-i",
        inp,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-an",
        out,
    ]
    subprocess.run(cmd, check=True)
    return out


def thumbnails_vtt(ffmpeg: str, inp: str, out_dir: str, every_sec: int = 10):
    img_dir = os.path.join(out_dir, "thumbs")
    os.makedirs(img_dir, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-i",
        inp,
        "-vf",
        f"fps=1/{every_sec},scale=320:-1",
        "-q:v",
        "5",
        os.path.join(img_dir, "thumb_%05d.jpg"),
    ]
    subprocess.run(cmd, check=True)
    images = sorted([f for f in os.listdir(img_dir) if f.endswith(".jpg")])
    vtt = os.path.join(out_dir, "thumbs.vtt")
    t = 0

    def fmt(s):
        m = s // 60
        s = s % 60
        return f"00:{m:02d}:{s:02d}.000"

    with open(vtt, "w", newline="\n") as f:
        f.write("WEBVTT\n\n")
        for img in images:
            start = fmt(t)
            end = fmt(t + every_sec)
            f.write(f"{start} --> {end}\n")
            f.write(f"thumbs/{img}\n\n")
            t += every_sec
    return vtt
