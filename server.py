from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn, os, argparse


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        return resp


app = FastAPI()

BASE_DIR = Path(__file__).parent
ASSETS_DIR = BASE_DIR / "assets"

# assets statis (ikon, css/js lokal, dll)
if ASSETS_DIR.exists():
    app.mount(
        "/static", StaticFiles(directory=str(ASSETS_DIR), html=False), name="static"
    )


@app.get("/favicon.ico")
def favicon():
    ico = ASSETS_DIR / "app.ico"
    if ico.exists():
        return FileResponse(str(ico), media_type="image/x-icon")
    return Response(status_code=404)


# Web Preview (Plyr.io)
HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Preview</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/plyr@3.7.8/dist/plyr.css"/>
<link rel="icon" href="/static/app.ico?v=1" type="image/x-icon">
<link rel="shortcut icon" href="/static/app.ico?v=1" type="image/x-icon">
<style>
  :root { color-scheme: dark light; }
  body{background:#0b0b0b;color:#eee;font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;margin:0}
  .wrap{max-width:1200px;margin:24px auto;padding:0 16px}
  .head{font-weight:700;font-size:26px;margin:8px 0 16px}
  .src{margin-top:10px;color:#aeb3c2;font-size:13px}
  .err{background:#2a0f12;color:#ffb4b4;padding:10px 12px;border-radius:8px;display:none;margin:10px 0}
  video{width:100%;max-height:72vh;background:#000;border-radius:12px}
</style>
</head>
<body>
<div class="wrap">
  <div class="head">Preview</div>
  <div id="err" class="err"></div>
  <video id="v" playsinline></video>
  <div class="src">Source: <span id="src"></span></div>
</div>

<script src="/static/plyr-preview-thumbnails.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dashjs@4.7.1/dist/dash.all.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/hls.js@1.5.13/dist/hls.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/plyr@3.7.8/dist/plyr.min.js"></script>

<script>
(function () {
  const qs = new URLSearchParams(location.search);
  const hlsSrc  = qs.get("hls");
  const dashSrc = qs.get("dash");
  const vttSrc  = qs.get("vtt");
  const thumbs  = qs.get("thumbs");

  const video = document.querySelector("video");

  // util: build label dari daftar level/bitrate
  const uniq = (arr) => Array.from(new Set(arr));
  function setupPlyr(options) {
    const player = new Plyr(video, options);
    // muat tracks jika ada
    if (vttSrc) {
      const track = document.createElement("track");
      track.kind = "subtitles"; track.srclang = "id"; track.label = "Subtitles"; track.src = vttSrc;
      video.appendChild(track);
    }
    return player;
  }

  // HLS path (sudah OK di tempatmu)
  async function bootHls() {
    const hls = new Hls({ enableWorker: true });
    hls.loadSource(hlsSrc);
    hls.attachMedia(video);

    let qualities = [];
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      qualities = uniq(hls.levels.map(l => l.height)).sort((a,b)=>b-a);
      const plyr = setupPlyr({
        captions: { active: false, update: true },
        autoplay: false,
        controls: ['play','progress','current-time','mute','volume','settings','pip','airplay','fullscreen'],
        quality: {
          default: qualities[0],
          options: qualities,
          forced: true,
          onChange: q => {
            // set level berdasarkan height
            const idx = hls.levels.findIndex(l => l.height === q);
            if (idx >= 0) hls.currentLevel = idx;
          }
        }
      });
      // sinkronkan perubahan dari ABR
      hls.on(Hls.Events.LEVEL_SWITCHED, (_, data) => {
        const lvl = hls.levels[data.level];
        if (lvl && lvl.height) plyr.quality = lvl.height;
      });
    });
  }

  // DASH path (ini yang kamu butuh perbaikan)
  async function bootDash() {
    const dash = dashjs.MediaPlayer().create();
    dash.initialize(video, dashSrc, true);
    // matikan auto ABR jika user pilih manual
    dash.updateSettings({ streaming: { abr: { autoSwitchBitrate: { video: true }}}});

    dash.on(dashjs.MediaPlayer.events.STREAM_INITIALIZED, () => {
      // ambil daftar bitrate/representations
      const infos = dash.getBitrateInfoListFor("video") || [];
      // kadang height bisa undefined → fallback label pakai bitrate kbps
      const heights = uniq(infos.map(i => i.height).filter(Boolean)).sort((a,b)=>b-a);
      const options = heights.length ? heights : infos.map(i => Math.round(i.bitrate/1000)).sort((a,b)=>b-a);

      const plyr = setupPlyr({
        captions: { active: false, update: true },
        autoplay: false,
        controls: ['play','progress','current-time','mute','volume','settings','pip','airplay','fullscreen'],
        quality: {
          default: options[0],
          options: options,
          forced: true,
          onChange: (q) => {
            // jika pakai height, map ke index kualitas
            const idx = infos.findIndex(i => (i.height ? i.height === q : Math.round(i.bitrate/1000) === q));
            if (idx >= 0) {
              dash.updateSettings({ streaming: { abr: { autoSwitchBitrate: { video: false }}}});
              dash.setQualityFor("video", idx, true); // switch segera
            }
          }
        }
      });

      // sinkronkan kalau ABR aktif
      dash.on(dashjs.MediaPlayer.events.QUALITY_CHANGE_RENDERED, (e) => {
        const info = dash.getBitrateInfoListFor("video")[e.newQuality];
        const h = info && info.height ? info.height : Math.round(info.bitrate/1000);
        plyr.quality = h;
      });
    });
  }

  // Thumbnails: gunakan plugin hanya jika tersedia lokal
  // (CDN-mu error MIME text/plain, jadi jangan paksa.)
  if (thumbs && window.Plyr && window.Plyr.plugins && window.Plyr.plugins.previewThumbnails) {
    try {
      window.Plyr.defaults.previewThumbnails = { enabled: true, src: thumbs };
    } catch (e) { /* diam */ }
  }

  if (hlsSrc) bootHls();
  else if (dashSrc) bootDash();
})();
</script>
</body>
</html>
"""


@app.get("/player", response_class=HTMLResponse)
def player():
    return HTML


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="Root folder output encode")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    os.makedirs(args.root, exist_ok=True)
    app.mount("/out", NoCacheStaticFiles(directory=args.root), name="out")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
