# gui.py
import sys, os, subprocess, threading, queue, socket, ctypes, html, json, sqlite3, time
from pathlib import Path
from statistics import mean
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QFileDialog,
    QCheckBox,
    QLabel,
    QComboBox,
    QLineEdit,
    QTextEdit,
    QMessageBox,
    QProgressBar,
    QSystemTrayIcon,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QTabWidget,
    QToolButton,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor, QIcon

# Optional: Windows native toast
try:
    from winotify import Notification, audio
except Exception:
    Notification = None

CREATE_NO_WINDOW = 0x08000000
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0


def popen_hidden(args, cwd=None):
    si = None
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= STARTF_USESHOWWINDOW
        si.wShowWindow = SW_HIDE
    return subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=cwd,
        startupinfo=si,
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


APP_NAME = "StreamCode"

# =====(cx_Freeze Config) =====
FROZEN = bool(getattr(sys, "frozen", False))
EXE_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).parent.resolve()
ASSETS_DIR = EXE_DIR / "assets"
PLAYER_HTML = EXE_DIR / "player.html"
APPDATA_DIR = Path(os.environ.get("LOCALAPPDATA", EXE_DIR)) / "StreamCode"
LOG_DIR = APPDATA_DIR / "logs"
TMP_DIR = APPDATA_DIR / "tmp"
HISTORY_DB = APPDATA_DIR / "history.db"
for d in (APPDATA_DIR, LOG_DIR, TMP_DIR):
    d.mkdir(parents=True, exist_ok=True)


def is_windows() -> bool:
    return os.name == "nt"


def notify_native(title: str, message: str) -> bool:
    if is_windows() and Notification:
        try:
            n = Notification(
                app_id=APP_NAME, title=title, msg=message, duration="short"
            )
            n.set_audio(audio.Default, loop=False)
            n.show()
            return True
        except Exception:
            return False
    return False


def app_icon() -> QIcon:
    return QIcon(str(ASSETS_DIR / "app.ico"))


def _child_cmd(script: str) -> list[str]:
    """
    Saat dibundle (sys.frozen): panggil exe. Kalau tidak ada, fallback ke python + script.py.
    """
    if FROZEN:
        mapping = {"encode.py": "encoder.exe", "server.py": "server.exe"}
        exe = mapping.get(script)
        if exe:
            exe_path = EXE_DIR / exe
            if exe_path.exists():
                return [str(exe_path)]
    # dev mode / fallback
    return [sys.executable, str((Path(__file__).parent / script).resolve())]


def sizeof_fmt(num: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num < 1024.0:
            return f"{num:3.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


HELP_HTML = """
<h2 style="margin:6px 0">Panduan Singkat</h2>
<ol>
  <li><b>Pilih Output</b> folder untuk hasil encode.</li>
  <li>Tambahkan <b>video</b> (MP4/MKV/MOV/M4V). Opsional: pilih SRT.</li>
  <li>Pilih <b>Codec</b>, <b>Resolusi</b>, output <b>HLS/DASH</b>, <b>AES-128</b> bila perlu. 
      Centang <b>Ekstrak subtitle embed</b> untuk mengambil subtitle teks bawaan file.</li>
  <li>Klik <b>Mulai Proses</b>. Progress realtime muncul per-video.</li>
  <li>Setelah selesai, klik <b>Preview HLS/DASH</b>. Player mendukung quality selector, subtitle, dan thumbnail.</li>
  <li>Tab <b>Watcher</b> memantau folder input dan otomatis memproses file baru.</li>
  <li>Tab <b>Riwayat</b> menyimpan job yang selesai. Double-click untuk <b>Preview</b> atau klik <b>Buka Folder</b>.</li>
</ol>
<p style="color:#9aa3b2">
Catatan: HLS HEVC tidak didukung di Chrome; player otomatis fallback ke DASH. Untuk HLS lintas-browser gunakan H.264.
</p>
"""


class ProcessReader(threading.Thread):
    def __init__(
        self,
        base: str,
        popen: subprocess.Popen,
        out_queue: "queue.Queue[tuple[str,str]]",
    ):
        super().__init__(daemon=True)
        self.base = base
        self.p = popen
        self.q = out_queue

    def run(self):
        for line in iter(self.p.stdout.readline, ""):
            if not line:
                break
            self.q.put((self.base, line.rstrip()))
        try:
            self.p.stdout.close()
        except Exception:
            pass


class WatcherThread(threading.Thread):
    VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".m4v"}

    def __init__(
        self,
        gui_ref,
        in_dir: str,
        opts: dict,
        interval: float = 2.0,
        stable_hits: int = 3,
    ):
        super().__init__(daemon=True)
        self.gui = gui_ref
        self.in_dir = in_dir
        self.opts = opts
        self.interval = interval
        self.stable_hits = stable_hits
        self._stop = threading.Event()
        self._seen = {}
        self._processed = set()

    def stop(self):
        self._stop.set()

    def run(self):
        self.gui._log_line("INFO", f"[watcher] start → {self.in_dir}")
        while not self._stop.is_set():
            try:
                if not os.path.isdir(self.in_dir):
                    time.sleep(self.interval)
                    continue
                for name in os.listdir(self.in_dir):
                    path = os.path.join(self.in_dir, name)
                    ext = os.path.splitext(name)[1].lower()
                    if ext not in self.VIDEO_EXTS:
                        continue
                    if path in self._processed:
                        continue
                    base = os.path.splitext(name)[0]
                    out_dir = os.path.join(self.opts["out_root"], base)
                    if os.path.isdir(out_dir):
                        self._processed.add(path)
                        continue
                    try:
                        sz = os.path.getsize(path)
                    except Exception:
                        continue
                    rec = self._seen.get(path, {"size": -1, "hits": 0})
                    if sz == rec["size"]:
                        rec["hits"] += 1
                    else:
                        rec = {"size": sz, "hits": 1}
                    self._seen[path] = rec
                    if rec["hits"] >= self.stable_hits:
                        self._processed.add(path)
                        self.gui._log_line("INFO", f"[watcher] enqueue → {name}")
                        self.gui._start_job_for_path(
                            path,
                            codec=self.opts["codec"],
                            gpu=self.opts["gpu"],
                            renditions=self.opts["renditions"],
                            do_hls=self.opts["hls"],
                            do_dash=self.opts["dash"],
                            encrypt=self.opts["encrypt"],
                            extract_subs=self.opts["extract_subs"],
                            srt_path=None,
                        )
                time.sleep(self.interval)
            except Exception as e:
                self.gui._log_line("ERROR", f"[watcher] {e}")
                time.sleep(self.interval)
        self.gui._log_line("INFO", "[watcher] stopped")


class App(QWidget):
    COL_VIDEO, COL_STATUS, COL_PROGRESS, COL_OUTPUTS, COL_CODEC = range(5)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1260, 780)
        self.theme_dark = True

        if is_windows():
            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "Ardean.StreamCode.ABREncoder"
                )
            except Exception:
                pass

        # server preview
        self.server_proc: subprocess.Popen | None = None
        self.server_root_current: str | None = None
        self.app_port = self._find_free_port(8787)

        # runtime state
        self.log_queue: "queue.Queue[tuple[str,str]]" = queue.Queue()
        self.jobs = {}  # base -> {...}
        self.queue_paths = {}  # base -> input path
        self.row_of_base: dict[str, int] = {}

        # history db (user-writable)
        self.db_path = str(HISTORY_DB)
        self._init_db()

        # watcher
        self.watcher_thread: WatcherThread | None = None

        # Tray
        self.tray = QSystemTrayIcon(self)
        icon = app_icon()
        self.tray.setIcon(icon)
        self.setWindowIcon(icon)

        # Tabs
        tabs = QTabWidget()
        tabs.setTabPosition(QTabWidget.North)
        root_layout = QVBoxLayout(self)
        root_layout.addWidget(tabs)

        # Encoder tab
        self.encPage = QWidget()
        tabs.addTab(self.encPage, "Encoder")
        self._build_encoder_tab()

        # Watcher tab
        self.watchPage = QWidget()
        tabs.addTab(self.watchPage, "Watcher")
        self._build_watcher_tab()

        # History tab
        self.histPage = QWidget()
        tabs.addTab(self.histPage, "Riwayat")
        self._build_history_tab()

        # Help tab
        self.helpPage = QWidget()
        tabs.addTab(self.helpPage, "Panduan")
        self._build_help_tab()

        # Theme
        self.apply_theme()
        self.setAcceptDrops(True)

        # timers
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._pump_logs)
        self.timer.start()
        self.stat_timer = QTimer(self)
        self.stat_timer.setInterval(600)
        self.stat_timer.timeout.connect(self._refresh_stats)
        self.stat_timer.start()

        # start server on current output folder
        self.start_server(self.edOut.text().strip())

    # ===== Build Tabs =====
    def _build_encoder_tab(self):
        v = QVBoxLayout(self.encPage)

        # Toolbar
        r1 = QHBoxLayout()
        self.edOut = QLineEdit(
            os.environ.get("ENCODER_OUT_DIR")
            or str(Path.home() / "Documents" / "StreamCode")
        )
        self.btnOut = QPushButton("Pilih Output…")
        self.btnOut.clicked.connect(self.pick_outdir)
        self.btnTheme = QPushButton("Tema Gelap/Terang")
        self.btnTheme.clicked.connect(self.toggle_theme)
        self.btnClearDone = QPushButton("Bersihkan Selesai")
        self.btnClearDone.clicked.connect(self.clear_finished)
        self.btnClearLog = QPushButton("Bersihkan Log")
        self.btnClearLog.clicked.connect(self.clear_log)
        self.lblStats = QLabel("Queued: 0 | Running: 0 | Done: 0 | Failed: 0")
        r1.addWidget(QLabel("Output"))
        r1.addWidget(self.edOut, 2)
        r1.addWidget(self.btnOut)
        r1.addStretch(1)
        r1.addWidget(self.btnTheme)
        r1.addWidget(self.btnClearDone)
        r1.addWidget(self.btnClearLog)
        r1.addWidget(self.lblStats)
        v.addLayout(r1)

        # Controls line
        r2 = QHBoxLayout()
        self.cbCodec = QComboBox()
        self.cbCodec.addItems(["h264", "hevc"])
        self.cbGPU = QCheckBox("GPU Accel (NVENC)")
        self.edSrt = QLineEdit()
        self.edSrt.setPlaceholderText("Path SRT (opsional)")
        self.btnSrt = QPushButton("…")
        self.btnSrt.clicked.connect(self.pick_srt)

        resBox = QHBoxLayout()
        self.cb2160 = QCheckBox("2160")
        self.cb1440 = QCheckBox("1440")
        self.cb1080 = QCheckBox("1080")
        self.cb720 = QCheckBox("720")
        self.cb480 = QCheckBox("480")
        for cb in [self.cb2160, self.cb1440, self.cb1080, self.cb720, self.cb480]:
            cb.setChecked(True)
            resBox.addWidget(cb)

        outBox = QHBoxLayout()
        self.cbHLS = QCheckBox("HLS")
        self.cbHLS.setChecked(True)
        self.cbDASH = QCheckBox("DASH")
        self.cbDASH.setChecked(True)
        self.cbEncrypt = QCheckBox("Enkripsi AES-128 (HLS)")
        self.cbExtractSubs = QCheckBox("Ekstrak subtitle embed")
        for w in [self.cbHLS, self.cbDASH, self.cbEncrypt, self.cbExtractSubs]:
            outBox.addWidget(w)

        r2.addWidget(QLabel("Codec"))
        r2.addWidget(self.cbCodec)
        r2.addWidget(self.cbGPU)
        r2.addWidget(self.edSrt, 2)
        r2.addWidget(self.btnSrt)
        r2.addWidget(QLabel("Resolusi"))
        r2.addLayout(resBox)
        r2.addStretch(1)
        r2.addLayout(outBox)
        v.addLayout(r2)

        # Table + actions
        r3 = QHBoxLayout()
        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(
            ["Video", "Status", "Progress", "Outputs", "Codec"]
        )
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.setStyleSheet(
            "QTableWidget { font-size: 13px; } QHeaderView::section { padding: 8px; font-weight: 600; }"
        )
        self.tbl.setColumnWidth(self.COL_VIDEO, 340)
        self.tbl.setColumnWidth(self.COL_STATUS, 120)
        self.tbl.setColumnWidth(self.COL_PROGRESS, 110)
        self.tbl.setColumnWidth(self.COL_OUTPUTS, 160)
        self.tbl.setColumnWidth(self.COL_CODEC, 90)
        self.tbl.itemSelectionChanged.connect(self._on_selection_changed)
        r3.addWidget(self.tbl, 3)

        rr = QVBoxLayout()
        self.btnPick = QPushButton("Tambah Video…")
        self.btnPick.clicked.connect(self.pick_video)
        self.btnStart = QPushButton("Mulai Proses")
        self.btnStart.clicked.connect(self.start_selected)
        self.btnCancel = QPushButton("Batalkan")
        self.btnCancel.clicked.connect(self.cancel_selected)
        self.btnPreviewH = QPushButton("Preview HLS")
        self.btnPreviewH.clicked.connect(lambda: self.preview_mode("hls"))
        self.btnPreviewD = QPushButton("Preview DASH")
        self.btnPreviewD.clicked.connect(lambda: self.preview_mode("dash"))
        for b in [
            self.btnPick,
            self.btnStart,
            self.btnCancel,
            self.btnPreviewH,
            self.btnPreviewD,
        ]:
            rr.addWidget(b)
        rr.addStretch()
        r3.addLayout(rr, 1)
        v.addLayout(r3)

        # Progress + Log
        self.prog = QProgressBar()
        self.prog.setValue(0)
        v.addWidget(self.prog)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet(
            "QTextEdit{font-family: Consolas, 'Courier New', monospace; font-size: 12px}"
        )
        v.addWidget(self.log, 2)

    def _build_watcher_tab(self):
        v = QVBoxLayout(self.watchPage)

        r1 = QHBoxLayout()
        self.edWatchIn = QLineEdit(str(Path.home() / "Videos"))
        self.btnWatchIn = QPushButton("Pilih Folder…")
        self.btnWatchIn.clicked.connect(self.pick_watch_in)
        self.btnWatchStart = QPushButton("Start Watcher")
        self.btnWatchStop = QPushButton("Stop")
        self.btnWatchStart.clicked.connect(self.start_watcher)
        self.btnWatchStop.clicked.connect(self.stop_watcher)
        self.lblWatchState = QLabel("Status: idle")
        r1.addWidget(QLabel("Folder Input"))
        r1.addWidget(self.edWatchIn, 2)
        r1.addWidget(self.btnWatchIn)
        r1.addStretch(1)
        r1.addWidget(self.btnWatchStart)
        r1.addWidget(self.btnWatchStop)
        r1.addWidget(self.lblWatchState)
        v.addLayout(r1)

        r2 = QHBoxLayout()
        self.wCodec = QComboBox()
        self.wCodec.addItems(["h264", "hevc"])
        self.wGPU = QCheckBox("GPU Accel (NVENC)")
        rlad = QHBoxLayout()
        self.w2160 = QCheckBox("2160")
        self.w1440 = QCheckBox("1440")
        self.w1080 = QCheckBox("1080")
        self.w720 = QCheckBox("720")
        self.w480 = QCheckBox("480")
        for cb in [self.w2160, self.w1440, self.w1080, self.w720, self.w480]:
            cb.setChecked(True)
            rlad.addWidget(cb)
        rout = QHBoxLayout()
        self.wHLS = QCheckBox("HLS")
        self.wHLS.setChecked(True)
        self.wDASH = QCheckBox("DASH")
        self.wDASH.setChecked(True)
        self.wEnc = QCheckBox("Enkripsi AES-128 (HLS)")
        self.wSubs = QCheckBox("Ekstrak subtitle embed")
        self.wSubs.setChecked(True)
        for w in [self.wHLS, self.wDASH, self.wEnc, self.wSubs]:
            rout.addWidget(w)
        r2.addWidget(QLabel("Codec"))
        r2.addWidget(self.wCodec)
        r2.addWidget(self.wGPU)
        r2.addWidget(QLabel("Resolusi"))
        r2.addLayout(rlad)
        r2.addStretch(1)
        r2.addLayout(rout)
        v.addLayout(r2)

        tip = QLabel("Output memakai folder pada tab Encoder:  " + self.edOut.text())
        tip.setStyleSheet("color:#9aa3b2")
        v.addWidget(tip)
        v.addStretch(1)

    def _build_history_tab(self):
        v = QVBoxLayout(self.histPage)

        top = QHBoxLayout()
        self.edSearch = QLineEdit()
        self.edSearch.setPlaceholderText("Cari (judul/codec/renditions/root)…")
        self.btnRefreshHist = QPushButton("Refresh")
        self.btnRefreshHist.clicked.connect(self.load_history)
        self.btnClearMissing = QPushButton("Bersihkan Missing")
        self.btnClearMissing.clicked.connect(self.clear_missing_history)
        self.btnDeleteSel = QPushButton("Hapus Terpilih")
        self.btnDeleteSel.clicked.connect(self.delete_selected_history)
        top.addWidget(self.edSearch, 3)
        top.addWidget(self.btnRefreshHist)
        top.addWidget(self.btnClearMissing)
        top.addWidget(self.btnDeleteSel)
        v.addLayout(top)

        self.tblHist = QTableWidget(0, 9)
        self.tblHist.setHorizontalHeaderLabels(
            [
                "Waktu",
                "Video",
                "Codec",
                "Outputs",
                "Durasi",
                "Ukuran",
                "Root",
                "HLS/DASH",
                "ID",
            ]
        )
        self.tblHist.verticalHeader().setVisible(False)
        self.tblHist.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tblHist.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tblHist.setAlternatingRowColors(True)
        self.tblHist.setStyleSheet(
            "QTableWidget { font-size: 13px; } QHeaderView::section { padding: 8px; font-weight: 600; }"
        )
        self.tblHist.setColumnWidth(0, 150)
        self.tblHist.setColumnWidth(1, 250)
        self.tblHist.setColumnWidth(2, 80)
        self.tblHist.setColumnWidth(3, 110)
        self.tblHist.setColumnWidth(4, 80)
        self.tblHist.setColumnWidth(5, 100)
        self.tblHist.setColumnWidth(6, 260)
        self.tblHist.setColumnWidth(7, 120)
        self.tblHist.setColumnHidden(8, True)
        self.tblHist.doubleClicked.connect(self._history_preview_auto)
        self.tblHist.itemSelectionChanged.connect(self._history_update_preview_menu)
        v.addWidget(self.tblHist, 1)

        bot = QHBoxLayout()
        self.btnHistPreview = QToolButton()
        self.btnHistPreview.setText("Preview")
        self.btnHistPreview.setPopupMode(QToolButton.MenuButtonPopup)
        m = QMenu(self.btnHistPreview)
        self.actHistHLS = m.addAction("HLS")
        self.actHistDASH = m.addAction("DASH")
        self.btnHistPreview.setMenu(m)
        self.btnHistPreview.clicked.connect(self._history_preview_auto)
        self.actHistHLS.triggered.connect(lambda: self._history_preview_mode("hls"))
        self.actHistDASH.triggered.connect(lambda: self._history_preview_mode("dash"))
        self.btnHistOpen = QPushButton("Buka Folder")
        self.btnHistOpen.clicked.connect(self._history_open_selected)
        bot.addStretch(1)
        bot.addWidget(self.btnHistPreview)
        bot.addWidget(self.btnHistOpen)
        v.addLayout(bot)

        self.load_history()
        QTimer.singleShot(0, self._history_update_preview_menu)

    def _build_help_tab(self):
        v = QVBoxLayout(self.helpPage)
        self.helpView = QTextEdit()
        self.helpView.setReadOnly(True)
        self.helpView.setHtml(HELP_HTML)
        v.addWidget(self.helpView)

    # ===== Proc IO =====
    def _on_proc_out(self, proc, base: str):
        data = proc.readAllStandardOutput().data().decode("utf-8", "replace")
        for line in data.splitlines():
            self._log_line("INFO", line)

    # ===== Theming =====
    def apply_theme(self):
        if self.theme_dark:
            self.setStyleSheet(
                """
            QWidget{background:#0e0f13;color:#e6e6e6;font:12px 'Segoe UI'}
            QPushButton{background:#1b1d24;border:1px solid #2a2d36;padding:10px 12px;border-radius:8px}
            QPushButton:hover{background:#232633}
            QLineEdit{background:#14161b;border:1px solid #2a2d36;padding:8px;border-radius:6px}
            QTableWidget{background:#121319;border:1px solid #2a2d36;gridline-color:#2a2d36}
            QTextEdit{background:#0b0c10;border:1px solid #2a2d36}
            QProgressBar{border:1px solid #2a2d36;border-radius:6px;text-align:center;background:#14161b}
            QProgressBar::chunk{background:#4c8bf5}
            """
            )
        else:
            self.setStyleSheet(
                """
            QWidget{background:#f8f9fb;color:#111;font:12px 'Segoe UI'}
            QPushButton{background:#fff;border:1px solid #ccd2e0;padding:10px 12px;border-radius:8px}
            QPushButton:hover{background:#f0f2f7}
            QLineEdit{background:#fff;border:1px solid #ccd2e0;padding:8px;border-radius:6px}
            QTableWidget{background:#fff;border:1px solid #ccd2e0;gridline-color:#ccd2e0}
            QTextEdit{background:#fff;border:1px solid #ccd2e0}
            QProgressBar{border:1px solid #ccd2e0;border-radius:6px;text-align:center;background:#fff}
            QProgressBar::chunk{background:#4c8bf5}
            """
            )

    def toggle_theme(self):
        self.theme_dark = not self.theme_dark
        self.apply_theme()

    # ===== Drag & Drop =====
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if os.path.isfile(p) and os.path.splitext(p)[1].lower() in [
                ".mp4",
                ".mkv",
                ".mov",
                ".m4v",
            ]:
                self._add_to_queue(p)

    # ===== Pickers =====
    def pick_outdir(self):
        d = QFileDialog.getExistingDirectory(self, "Pilih Output")
        if not d:
            return
        self.edOut.setText(d)
        self.start_server(d)

    def pick_srt(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Pilih SRT", "", "Subtitle Files (*.srt)"
        )
        if p:
            self.edSrt.setText(p)

    def pick_watch_in(self):
        d = QFileDialog.getExistingDirectory(self, "Pilih Folder Input")
        if d:
            self.edWatchIn.setText(d)

    def pick_video(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Pilih Video", "", "Video Files (*.mp4 *.mkv *.mov *.m4v)"
        )
        if not paths:
            return
        for p in paths:
            self._add_to_queue(p)

    # ===== Queue/Table ops =====
    def _ensure_row(self, base: str) -> int:
        if base in self.row_of_base:
            return self.row_of_base[base]
        row = self.tbl.rowCount()
        self.tbl.insertRow(row)
        self.tbl.setRowHeight(row, 30)
        self.tbl.setItem(row, self.COL_VIDEO, QTableWidgetItem(base))
        self.tbl.setItem(row, self.COL_STATUS, QTableWidgetItem("queued"))
        self.tbl.setItem(row, self.COL_PROGRESS, QTableWidgetItem("0%"))
        self.tbl.setItem(row, self.COL_OUTPUTS, QTableWidgetItem("-"))
        self.tbl.setItem(
            row, self.COL_CODEC, QTableWidgetItem(self.cbCodec.currentText())
        )
        self.row_of_base[base] = row
        return row

    def _current_base(self) -> str | None:
        row = self.tbl.currentRow()
        if row < 0:
            return None
        item = self.tbl.item(row, self.COL_VIDEO)
        return item.text() if item else None

    def _set_row(self, base: str, col: int, text: str):
        row = self._ensure_row(base)
        self.tbl.setItem(row, col, QTableWidgetItem(text))

    def _set_row_progress(self, base: str, pct: int):
        self._set_row(base, self.COL_PROGRESS, f"{pct}%")

    def _update_item_status(self, base: str, status: str):
        self._set_row(base, self.COL_STATUS, status)

    def _add_to_queue(self, path: str):
        base = os.path.splitext(os.path.basename(path))[0]
        self._ensure_row(base)
        self.queue_paths[base] = path
        self._log_line("INFO", f"[queue] {base}")

    # ===== Start/Cancel =====
    def start_selected(self):
        base = self._current_base()
        if not base:
            QMessageBox.information(self, "Info", "Pilih satu baris video pada tabel.")
            return
        path = self.queue_paths.get(base)
        if not path:
            QMessageBox.warning(
                self, "Warning", "Path input tidak ditemukan untuk item ini."
            )
            return

        rend = [
            name
            for name, cb in [
                ("2160", self.cb2160),
                ("1440", self.cb1440),
                ("1080", self.cb1080),
                ("720", self.cb720),
                ("480", self.cb480),
            ]
            if cb.isChecked()
        ]
        if not rend:
            QMessageBox.warning(self, "Warning", "Pilih minimal satu resolusi.")
            return

        self._start_job_for_path(
            path,
            codec=self.cbCodec.currentText(),
            gpu=self.cbGPU.isChecked(),
            renditions=rend,
            do_hls=self.cbHLS.isChecked(),
            do_dash=self.cbDASH.isChecked(),
            encrypt=self.cbEncrypt.isChecked(),
            extract_subs=self.cbExtractSubs.isChecked(),
            srt_path=(self.edSrt.text().strip() or None),
        )

    def _start_job_for_path(
        self,
        path: str,
        *,
        codec: str,
        gpu: bool,
        renditions: list[str],
        do_hls: bool,
        do_dash: bool,
        encrypt: bool,
        extract_subs: bool,
        srt_path: str | None,
    ):
        base = os.path.splitext(os.path.basename(path))[0]
        if base in self.jobs and self.jobs[base].get("status") == "running":
            self._log_line("WARN", f"[encode] sudah berjalan → {base}")
            return

        outdir = self.edOut.text().strip()
        if not os.path.isdir(outdir):
            try:
                os.makedirs(outdir, exist_ok=True)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Gagal membuat output dir:\n{e}")
                return

        if not (do_hls or do_dash):
            QMessageBox.warning(
                self, "Warning", "Pilih minimal satu output (HLS/DASH)."
            )
            return

        args = _child_cmd("encode.py") + [
            "--input",
            path,
            "--outdir",
            outdir,
            "--codec",
            codec,
            "--renditions",
            ",".join(renditions),
        ]
        if gpu:
            args.append("--gpu")
        if not do_hls:
            args.append("--no-hls")
        if not do_dash:
            args.append("--no-dash")
        if encrypt:
            args.append("--encrypt")
        else:
            args.append("--no-encrypt")
        if extract_subs:
            args.append("--extract-subs")
        if srt_path:
            args += ["--srt", srt_path]

        try:
            p = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self.jobs[base] = {
                "proc": p,
                "rend": {},
                "status": "running",
                "hls": do_hls,
                "dash": do_dash,
                "outdir": outdir,
            }
            ProcessReader(base, p, self.log_queue).start()
            self._ensure_row(base)
            self._log_line("INFO", f"[encode] start → {base}")
            self._update_item_status(base, "running")
            self._set_row(
                base,
                self.COL_OUTPUTS,
                ("HLS" if do_hls else "")
                + ("+" if do_hls and do_dash else "")
                + ("DASH" if do_dash else ""),
            )
            self._set_row(base, self.COL_CODEC, codec)
            self._set_row_progress(base, 0)
            self.prog.setValue(0)
            self._set_controls_enabled(False)
            self._update_preview_buttons()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Gagal menjalankan encode:\n{e}")

    def cancel_selected(self):
        base = self._current_base()
        if not base:
            return
        job = self.jobs.get(base)
        if not job:
            return
        p = job.get("proc")
        if p and p.poll() is None:
            try:
                p.terminate()
                self._log_line("WARN", f"[encode] cancel → {base}")
                job["status"] = "failed"
                self._update_item_status(base, "failed")
                self._set_controls_enabled(not self._any_running())
                self._update_preview_buttons()
            except Exception:
                try:
                    p.kill()
                    job["status"] = "failed"
                    self._update_item_status(base, "failed")
                except Exception:
                    pass

    # ===== Watcher control =====
    def start_watcher(self):
        if self.watcher_thread and self.watcher_thread.is_alive():
            QMessageBox.information(self, "Info", "Watcher sudah berjalan.")
            return
        in_dir = self.edWatchIn.text().strip()
        if not os.path.isdir(in_dir):
            QMessageBox.warning(self, "Warning", "Folder input tidak valid.")
            return
        out_root = self.edOut.text().strip()
        if not os.path.isdir(out_root):
            QMessageBox.warning(
                self, "Warning", "Folder output (tab Encoder) tidak valid."
            )
            return

        rend = [
            name
            for name, cb in [
                ("2160", self.w2160),
                ("1440", self.w1440),
                ("1080", self.w1080),
                ("720", self.w720),
                ("480", self.w480),
            ]
            if cb.isChecked()
        ]
        if not rend:
            QMessageBox.warning(
                self, "Warning", "Watcher: pilih minimal satu resolusi."
            )
            return

        opts = {
            "out_root": out_root,
            "codec": self.wCodec.currentText(),
            "gpu": self.wGPU.isChecked(),
            "renditions": rend,
            "hls": self.wHLS.isChecked(),
            "dash": self.wDASH.isChecked(),
            "encrypt": self.wEnc.isChecked(),
            "extract_subs": self.wSubs.isChecked(),
        }
        self.watcher_thread = WatcherThread(
            self, in_dir, opts, interval=2.0, stable_hits=3
        )
        self.watcher_thread.start()
        self.lblWatchState.setText("Status: watching")
        self._log_line(
            "INFO",
            f"[watcher] options → codec={opts['codec']} gpu={opts['gpu']} r={','.join(rend)} out={out_root}",
        )

    def stop_watcher(self):
        if self.watcher_thread and self.watcher_thread.is_alive():
            self.watcher_thread.stop()
            self.watcher_thread.join(timeout=2.0)
        self.watcher_thread = None
        self.lblWatchState.setText("Status: idle")

    # ===== Server preview =====
    def _find_free_port(self, prefer: int = 8787):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", prefer))
                return prefer
            except OSError:
                s.bind(("127.0.0.1", 0))
                return s.getsockname()[1]

    def start_server(self, root_dir: str):
        root_dir = os.path.abspath(root_dir)
        if (
            self.server_root_current == root_dir
            and self.server_proc
            and self.server_proc.poll() is None
        ):
            return
        self.stop_server()
        try:
            os.makedirs(root_dir, exist_ok=True)
            self.server_root_current = root_dir
            args = _child_cmd("server.py") + [
                "--root",
                root_dir,
                "--port",
                str(self.app_port),
            ]
            self.server_proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            ProcessReader("_server", self.server_proc, self.log_queue).start()
            self._log_line(
                "INFO", f"[server] http://127.0.0.1:{self.app_port} → {root_dir}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Gagal start server preview:\n{e}")

    def stop_server(self):
        if self.server_proc and self.server_proc.poll() is None:
            try:
                self.server_proc.terminate()
                self.server_proc.wait(timeout=2)
            except Exception:
                try:
                    self.server_proc.kill()
                except Exception:
                    pass
        self.server_proc = None

    # ===== Preview (Encoder tab) =====
    def preview_mode(self, mode: str):
        base = self._current_base()
        if not base:
            QMessageBox.information(self, "Info", "Pilih satu baris video pada tabel.")
            return
        job = self.jobs.get(base)
        if not job or job.get("status") != "done":
            QMessageBox.information(
                self, "Info", "Job belum selesai atau data tidak tersedia."
            )
            return

        outdir = job.get("outdir") or self.edOut.text().strip()
        self.start_server(outdir)

        meta_path = os.path.join(outdir, base, "player.json")
        hls_rel = dash_rel = vtt_rel = thumbs_rel = None
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            srcs = meta.get("sources", {})
            hls_rel = srcs.get("hls")
            dash_rel = srcs.get("dash")
            tracks = (meta.get("tracks") or {}).get("subtitles") or []
            if tracks:
                vtt_rel = str(tracks[0].get("src", "")) or None
            thumbs_rel = meta.get("thumbnails") or None
        except Exception:
            if os.path.isfile(os.path.join(outdir, base, f"HLS/{base}.m3u8")):
                hls_rel = f"HLS/{base}.m3u8"
            if os.path.isfile(os.path.join(outdir, base, f"DASH/{base}.mpd")):
                dash_rel = f"DASH/{base}.mpd"
            if os.path.isfile(os.path.join(outdir, base, f"{base}.vtt")):
                vtt_rel = f"{base}.vtt"
            if os.path.isfile(os.path.join(outdir, base, "thumbs.vtt")):
                thumbs_rel = "thumbs.vtt"

        params = []
        if mode == "hls":
            if not hls_rel:
                QMessageBox.information(self, "Info", "Sumber HLS tidak ditemukan.")
                return
            params.append(f"hls=/out/{base}/{hls_rel}")
        elif mode == "dash":
            if not dash_rel:
                QMessageBox.information(self, "Info", "Sumber DASH tidak ditemukan.")
                return
            params.append(f"dash=/out/{base}/{dash_rel}")
        else:
            QMessageBox.warning(self, "Warning", f"Mode preview tidak dikenal: {mode}")
            return
        if vtt_rel:
            params.append(f"vtt=/out/{base}/{vtt_rel}")
        if thumbs_rel:
            params.append(f"thumbs=/out/{base}/{thumbs_rel}")

        url = f"http://127.0.0.1:{self.app_port}/player?" + "&".join(params)
        try:
            os.startfile(url) if is_windows() else subprocess.Popen(["xdg-open", url])
            self._log_line("INFO", f"[preview {mode}] {url}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Gagal membuka browser:\n{e}")

    # ===== Logs + Progress + Stats =====
    def _format_log_html(self, level: str, text: str) -> str:
        ts = datetime.now().strftime("%H:%M:%S")
        safe = html.escape(text)
        color = {
            "INFO": "#cfd3dc",
            "WARN": "#ffcc66",
            "ERROR": "#ff6b6b",
            "DONE": "#8fd18f",
        }.get(level, "#cfd3dc")
        return f'<span style="color:#8088a2">{ts}</span> <span style="color:{color}">{safe}</span>'

    def _log_line(self, level: str, text: str):
        html_line = self._format_log_html(level, text) + "<br/>"
        c = self.log.textCursor()
        c.movePosition(QTextCursor.End)
        self.log.setTextCursor(c)
        c.insertHtml(html_line)
        self.log.setTextCursor(c)
        self.log.ensureCursorVisible()

    def _pump_logs(self):
        try:
            while True:
                base, line = self.log_queue.get_nowait()
                if line.startswith("PROGRESS "):
                    try:
                        parts = dict(
                            kv.split("=", 1) for kv in line.split()[1:] if "=" in kv
                        )
                        b = parts.get("base", base)
                        r = parts.get("rend")
                        pct = float(parts.get("pct", "0"))
                        job = self.jobs.get(b)
                        if job is not None and r:
                            job["rend"][r] = pct
                    except Exception:
                        pass
                else:
                    if "JOB_DONE base=" in line:
                        b = line.split("JOB_DONE base=", 1)[1].strip()
                        self._mark_done(b)
                        continue
                    if line.startswith("out_time_ms=") or line.startswith("progress="):
                        continue
                    L = "INFO"
                    lower = line.lower()
                    if "error" in lower or "failed" in lower or "traceback" in lower:
                        L = "ERROR"
                    elif line.startswith("[encode] cancel"):
                        L = "WARN"
                    self._log_line(L, line)
        except queue.Empty:
            pass

        base = self._current_base()
        if base:
            job = self.jobs.get(base)
            if job and job["rend"]:
                val = int(mean(job["rend"].values()))
                self.prog.setValue(val)
                self._set_row_progress(base, val)
            elif job and job.get("status") == "done":
                self.prog.setValue(100)
                self._set_row_progress(base, 100)

    def _refresh_stats(self):
        q = len([b for b in self.queue_paths.keys() if b not in self.jobs])
        running = len([j for j in self.jobs.values() if j.get("status") == "running"])
        done = len([j for j in self.jobs.values() if j.get("status") == "done"])
        failed = len([j for j in self.jobs.values() if j.get("status") == "failed"])
        self.lblStats.setText(
            f"Queued: {q} | Running: {running} | Done: {done} | Failed: {failed}"
        )

    def _mark_done(self, base: str):
        job = self.jobs.get(base)
        if job:
            job["status"] = "done"
            self._update_item_status(base, "done")
            self._set_row_progress(base, 100)
            self._notify(f"Selesai: {base}")
            self._set_controls_enabled(not self._any_running())
            self._update_preview_buttons()
            try:
                self._history_record_job(
                    base, job.get("outdir") or self.edOut.text().strip()
                )
                self.load_history()
            except Exception as e:
                self._log_line("WARN", f"[history] gagal tulis: {e}")

    def _on_selection_changed(self):
        self._update_preview_buttons()
        base = self._current_base()
        allow_cancel = bool(base and self.jobs.get(base, {}).get("status") == "running")
        self.btnCancel.setEnabled(allow_cancel)

    def _update_preview_buttons(self):
        base = self._current_base()
        if not base:
            self.btnPreviewH.setEnabled(False)
            self.btnPreviewD.setEnabled(False)
            return
        job = self.jobs.get(base)
        if not job:
            self.btnPreviewH.setEnabled(False)
            self.btnPreviewD.setEnabled(False)
            return
        if job.get("status") == "running":
            self.btnPreviewH.setEnabled(False)
            self.btnPreviewD.setEnabled(False)
            return
        self.btnPreviewH.setEnabled(bool(job.get("hls")))
        self.btnPreviewD.setEnabled(bool(job.get("dash")))

    def _any_running(self) -> bool:
        return any(j.get("status") == "running" for j in self.jobs.values())

    def _set_controls_enabled(self, enabled: bool):
        for w in [
            self.btnPick,
            self.btnStart,
            self.btnOut,
            self.cbCodec,
            self.cbGPU,
            self.cbHLS,
            self.cbDASH,
            self.cbEncrypt,
            self.cbExtractSubs,
            self.edSrt,
            self.btnSrt,
            self.cb2160,
            self.cb1440,
            self.cb1080,
            self.cb720,
            self.cb480,
        ]:
            w.setEnabled(enabled)

    def _notify(self, msg: str):
        if notify_native(APP_NAME, msg):
            return
        try:
            if self.tray.isSystemTrayAvailable():
                self.tray.showMessage(APP_NAME, msg, QSystemTrayIcon.Information, 3000)
            else:
                QMessageBox.information(self, "Info", msg)
        except Exception:
            pass

    def clear_finished(self):
        remove_bases = [
            b
            for b, j in list(self.jobs.items())
            if j.get("status") in ("done", "failed")
        ]
        for b in remove_bases:
            row = self.row_of_base.get(b)
            if row is not None:
                self.tbl.removeRow(row)
                self.row_of_base.pop(b, None)
                self.row_of_base = {}
                for i in range(self.tbl.rowCount()):
                    base_i = self.tbl.item(i, self.COL_VIDEO).text()
                    self.row_of_base[base_i] = i
            self.jobs.pop(b, None)
            self.queue_paths.pop(b, None)
        self._log_line("INFO", f"[cleanup] removed {len(remove_bases)} finished job(s)")
        self.prog.setValue(0)
        self._update_preview_buttons()

    def clear_log(self):
        self.log.clear()

    # ===== History: DB =====
    def _init_db(self):
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(
                """
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                base TEXT NOT NULL,
                output_dir TEXT NOT NULL,
                codec TEXT,
                hls INTEGER,
                dash INTEGER,
                encrypt INTEGER,
                renditions TEXT,
                duration_ms INTEGER,
                finished_at TEXT,
                poster TEXT,
                hls_path TEXT,
                dash_path TEXT,
                vtt TEXT,
                thumbs TEXT,
                size_bytes INTEGER
            );"""
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_hist_base ON history(base);")
        finally:
            con.commit()
            con.close()

    def _folder_size(self, path: str) -> int:
        total = 0
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except Exception:
                    pass
        return total

    def _history_record_job(self, base: str, out_root: str):
        job_dir = os.path.join(out_root, base)
        player = os.path.join(job_dir, "player.json")
        jobj = os.path.join(job_dir, "job.json")
        if not os.path.isfile(player):
            raise FileNotFoundError("player.json tidak ditemukan untuk job ini.")
        with open(player, "r", encoding="utf-8") as f:
            meta = json.load(f)
        hsrc = (meta.get("sources") or {}).get("hls")
        dsrc = (meta.get("sources") or {}).get("dash")
        tracks = (meta.get("tracks") or {}).get("subtitles") or []
        vtt = tracks[0]["src"] if tracks else None
        thumbs = meta.get("thumbnails")
        poster = meta.get("poster")
        duration = int(meta.get("duration_ms") or 0)
        codec = str(meta.get("codec") or "")
        do_hls = 1
        do_dash = 1
        encrypt = 0
        rlist = []
        try:
            with open(jobj, "r", encoding="utf-8") as f:
                j = json.load(f)
            do_hls = 1 if j.get("hls") else 0
            do_dash = 1 if j.get("dash") else 0
            encrypt = 1 if j.get("encrypt_hls") else 0
            rlist = j.get("renditions") or []
        except Exception:
            pass

        sizeb = self._folder_size(job_dir)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(
                """
            INSERT INTO history (base, output_dir, codec, hls, dash, encrypt, renditions, duration_ms, finished_at, poster, hls_path, dash_path, vtt, thumbs, size_bytes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    base,
                    out_root,
                    codec,
                    do_hls,
                    do_dash,
                    encrypt,
                    ",".join(rlist),
                    duration,
                    ts,
                    poster or "",
                    hsrc or "",
                    dsrc or "",
                    vtt or "",
                    thumbs or "",
                    sizeb,
                ),
            )
        finally:
            con.commit()
            con.close()

    def load_history(self):
        q = (self.edSearch.text() or "").strip()
        con = sqlite3.connect(self.db_path)
        try:
            cur = con.cursor()
            if q:
                like = f"%{q}%"
                cur.execute(
                    """
                SELECT id, finished_at, base, codec, hls, dash, encrypt, renditions, duration_ms, size_bytes, output_dir, hls_path, dash_path
                FROM history
                WHERE base LIKE ? OR codec LIKE ? OR renditions LIKE ? OR output_dir LIKE ?
                ORDER BY id DESC
                """,
                    (like, like, like, like),
                )
            else:
                cur.execute(
                    """
                SELECT id, finished_at, base, codec, hls, dash, encrypt, renditions, duration_ms, size_bytes, output_dir, hls_path, dash_path
                FROM history ORDER BY id DESC"""
                )
            rows = cur.fetchall()
        finally:
            con.close()

        self.tblHist.setRowCount(0)
        for row in rows:
            (
                id_,
                ts,
                base,
                codec,
                hls,
                dash,
                enc,
                rend,
                dur,
                sizeb,
                root,
                hls_p,
                dash_p,
            ) = row
            r = self.tblHist.rowCount()
            self.tblHist.insertRow(r)
            self.tblHist.setRowHeight(r, 28)
            self.tblHist.setItem(r, 0, QTableWidgetItem(ts or ""))
            self.tblHist.setItem(r, 1, QTableWidgetItem(base))
            self.tblHist.setItem(r, 2, QTableWidgetItem(codec or ""))
            outstr = (
                ("HLS" if hls else "")
                + ("+" if hls and dash else "")
                + ("DASH" if dash else "")
            )
            if enc:
                outstr += " (enc)"
            self.tblHist.setItem(r, 3, QTableWidgetItem(outstr or "-"))
            self.tblHist.setItem(r, 4, QTableWidgetItem(f"{int((dur or 0)/1000)} s"))
            self.tblHist.setItem(r, 5, QTableWidgetItem(sizeof_fmt(sizeb or 0)))
            self.tblHist.setItem(r, 6, QTableWidgetItem(root))
            self.tblHist.setItem(
                r,
                7,
                QTableWidgetItem(
                    ("H" if hls and hls_p else "")
                    + (" " if hls and dash else "")
                    + ("D" if dash and dash_p else "")
                ),
            )
            iditem = QTableWidgetItem(str(id_))
            iditem.setData(Qt.UserRole, id_)
            self.tblHist.setItem(r, 8, iditem)

    def _selected_history_id(self) -> int | None:
        r = self.tblHist.currentRow()
        if r < 0:
            return None
        item = self.tblHist.item(r, 8)
        try:
            return int(item.data(Qt.UserRole))
        except Exception:
            return None

    def _hist_gather_sources(
        self, rec: dict
    ) -> tuple[str | None, str | None, str | None, str | None]:
        base = rec["base"]
        out_root = rec["output_dir"]
        player = os.path.join(out_root, base, "player.json")
        hls_rel = dash_rel = vtt_rel = thumbs_rel = None
        try:
            with open(player, "r", encoding="utf-8") as f:
                meta = json.load(f)
            srcs = meta.get("sources", {})
            hls_rel = srcs.get("hls")
            dash_rel = srcs.get("dash")
            tracks = (meta.get("tracks") or {}).get("subtitles") or []
            if tracks:
                vtt_rel = str(tracks[0].get("src", "")) or None
            thumbs_rel = meta.get("thumbnails")
        except Exception:
            if rec.get("hls"):
                p = os.path.join(out_root, base, "HLS", f"{base}.m3u8")
                if os.path.isfile(p):
                    hls_rel = f"HLS/{base}.m3u8"
            if rec.get("dash"):
                p = os.path.join(out_root, base, "DASH", f"{base}.mpd")
                if os.path.isfile(p):
                    dash_rel = f"DASH/{base}.mpd"
            if os.path.isfile(os.path.join(out_root, base, f"{base}.vtt")):
                vtt_rel = f"{base}.vtt"
            if os.path.isfile(os.path.join(out_root, base, "thumbs.vtt")):
                thumbs_rel = "thumbs.vtt"
        return hls_rel, dash_rel, vtt_rel, thumbs_rel

    def _history_update_preview_menu(self):
        hid = self._selected_history_id()
        ok_hls = ok_dash = False
        if hid is not None:
            rec = self._get_history_record(hid)
            if rec:
                hls_rel, dash_rel, _, _ = self._hist_gather_sources(rec)
                ok_hls = bool(hls_rel)
                ok_dash = bool(dash_rel)
                self.start_server(rec["output_dir"])
        self.actHistHLS.setEnabled(ok_hls)
        self.actHistDASH.setEnabled(ok_dash)
        self.btnHistPreview.setEnabled(ok_hls or ok_dash)

    def _history_preview_mode(self, mode: str):
        hid = self._selected_history_id()
        if hid is None:
            QMessageBox.information(self, "Info", "Pilih satu item riwayat.")
            return
        rec = self._get_history_record(hid)
        if not rec:
            return
        base = rec["base"]
        out_root = rec["output_dir"]
        self.start_server(out_root)
        hls_rel, dash_rel, vtt_rel, thumbs_rel = self._hist_gather_sources(rec)

        params = []
        if mode == "hls":
            if not hls_rel:
                QMessageBox.information(self, "Info", "Sumber HLS tidak ditemukan.")
                return
            params.append(f"hls=/out/{base}/{hls_rel}")
        elif mode == "dash":
            if not dash_rel:
                QMessageBox.information(self, "Info", "Sumber DASH tidak ditemukan.")
                return
            params.append(f"dash=/out/{base}/{dash_rel}")
        else:
            QMessageBox.warning(self, "Warning", f"Mode preview tidak dikenal: {mode}")
            return
        if vtt_rel:
            params.append(f"vtt=/out/{base}/{vtt_rel}")
        if thumbs_rel:
            params.append(f"thumbs=/out/{base}/{thumbs_rel}")
        url = f"http://127.0.0.1:{self.app_port}/player?" + "&".join(params)
        try:
            os.startfile(url) if is_windows() else subprocess.Popen(["xdg-open", url])
            self._log_line("INFO", f"[history preview {mode}] {url}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Gagal membuka browser:\n{e}")

    def _history_preview_auto(self):
        if self.actHistHLS.isEnabled():
            return
        self._history_preview_mode("hls")

        if self.actHistDASH.isEnabled():
            return self._history_preview_mode("dash")
        QMessageBox.information(
            self, "Info", "Output HLS/DASH tidak ditemukan untuk item ini."
        )

    def _history_open_selected(self):
        hid = self._selected_history_id()
        if hid is None:
            return
        rec = self._get_history_record(hid)
        if not rec:
            return
        path = os.path.join(rec["output_dir"], rec["base"])
        try:
            os.startfile(path) if is_windows() else subprocess.Popen(["xdg-open", path])
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Gagal buka folder:\n{e}")

    def _get_history_record(self, hid: int) -> dict | None:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            cur = con.cursor()
            cur.execute("SELECT * FROM history WHERE id=?", (hid,))
            r = cur.fetchone()
            return dict(r) if r else None
        finally:
            con.close()

    def clear_missing_history(self):
        con = sqlite3.connect(self.db_path)
        try:
            cur = con.cursor()
            cur.execute("SELECT id, output_dir, base FROM history")
            rows = cur.fetchall()
            removed = 0
            for hid, root, base in rows:
                if not os.path.isdir(os.path.join(root, base)):
                    cur.execute("DELETE FROM history WHERE id=?", (hid,))
                    removed += 1
        finally:
            con.commit()
            con.close()
        self.load_history()
        self._log_line("INFO", f"[history] removed {removed} missing record(s)")

    def delete_selected_history(self):
        hid = self._selected_history_id()
        if hid is None:
            return
        con = sqlite3.connect(self.db_path)
        try:
            con.execute("DELETE FROM history WHERE id=?", (hid,))
        finally:
            con.commit()
            con.close()
        self.load_history()

    # ===== Cleanup =====
    def closeEvent(self, ev):
        for base, job in self.jobs.items():
            p = job.get("proc")
            if p and p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
        self.stop_server()
        self.stop_watcher()
        super().closeEvent(ev)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setWindowIcon(app_icon())
    w = App()
    w.show()
    sys.exit(app.exec())
