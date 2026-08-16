# PyInstaller build. Run with: pyinstaller taplock.spec
#
# One directory rather than one file: a one-file build unpacks the whole of Qt
# into a temp folder on every launch, and this app is meant to start at login
# and sit in the tray. The zip contains a folder either way.

from PyInstaller.utils.hooks import collect_submodules  # noqa: F401  (kept for reference)

# Qt ships far more than this app touches. It only uses QtCore, QtGui,
# QtWidgets and QtNetwork; the rest is weight in the download.
EXCLUDED = [
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNfc",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    "PySide6.QtXml",
    "tkinter",
    "unittest",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    # icon.py resolves these with Path(__file__).parent, which lands inside the
    # bundle at runtime, so the folder name has to stay "assets".
    datas=[("assets", "assets")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDED,
    noarchive=False,
)

# `excludes` above only filters Python modules. PySide6's hook collects Qt's
# DLLs as binaries, so the ones for modules this app never imports survive it
# and have to be dropped by name. Everything here was confirmed absent from the
# import graph first -- there is no QML, no PDF, and Pillow only ever writes PNG.
UNUSED_BINARIES = (
    "Qt6Quick",
    "Qt6Qml",
    "PySide6\\qml",
    "Qt6Pdf",
    "_avif",
)
a.binaries = [b for b in a.binaries if not any(part in b[0] for part in UNUSED_BINARIES)]
a.datas = [d for d in a.datas if not any(part in d[0] for part in UNUSED_BINARIES)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TapLock",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # tray app: a console window would be wrong at login
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="TapLock",
)
