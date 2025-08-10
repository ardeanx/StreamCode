import os, sys
from pathlib import Path
from cx_Freeze import setup, Executable

APP_NAME = "StreamCode"
APP_VERSION = "1.0.0"
ICON_PATH = os.path.join("assets", "app.ico")

include_files = [
    ("assets", "assets"),
    ("utils", "utils"),
    ("player.html", "player.html"),
]

# Bundle ffmpeg
for tool in ("ffmpeg.exe", "ffprobe.exe"):
    p = Path(r"C:\ffmpeg") / tool
    if p.exists():
        include_files.append((str(p), tool))

# --- PySide6 plugin & DLL penting ---
qt_plugins_dir = None
opengl32sw = None
try:
    from PySide6 import QtCore, __file__ as pyside6_file

    qt_plugins_dir = QtCore.QLibraryInfo.path(QtCore.QLibraryInfo.PluginsPath)
    pyside_bin = Path(pyside6_file).parent
    opengl32sw = pyside_bin / "opengl32sw.dll"
except Exception:
    pass

if qt_plugins_dir and Path(qt_plugins_dir).exists():
    include_files.append((qt_plugins_dir, "PySide6/plugins"))

# Renderer software OpenGL
if opengl32sw and opengl32sw.exists():
    include_files.append((str(opengl32sw), "opengl32sw.dll"))

build_exe_options = {
    "packages": [
        "asyncio",
        "json",
        "sqlite3",
        "fastapi",
        "uvicorn",
        "h11",
        "PySide6",
        "shiboken6",
    ],
    "excludes": ["tkinter", "PyQt5", "pytest", "unittest"],
    "include_msvcr": True,
    "include_files": include_files,
    "optimize": 1,
    "zip_include_packages": ["encodings", "importlib", "collections"],
    "zip_exclude_packages": ["PySide6", "fastapi", "uvicorn", "h11"],
}

base_gui = "Win32GUI" if sys.platform == "win32" else None

executables = [
    Executable("main.py", base=base_gui, target_name="StreamCode.exe", icon=ICON_PATH),
    Executable("server.py", base=base_gui, target_name="server.exe", icon=ICON_PATH),
    Executable("encode.py", base=base_gui, target_name="encoder.exe", icon=ICON_PATH),
]

setup(
    name=APP_NAME,
    version=APP_VERSION,
    description="StreamCode ABR Encoder (HLS/DASH) with preview server",
    options={"build_exe": build_exe_options},
    executables=executables,
)
