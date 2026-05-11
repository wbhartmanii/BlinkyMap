# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for BlinkyMap.
# Build with:  pyinstaller BlinkyMap.spec
#
# The output is a self-contained folder dist/BlinkyMap/.
# Zip that folder and ship it — users unzip and double-click BlinkyMap (or BlinkyMap.exe).

from PyInstaller.utils.hooks import collect_all, collect_submodules
import sys

# ── collect all data / binaries / hidden-imports for heavy packages ───────────

vispy_d, vispy_b, vispy_h = collect_all("vispy")
cv2_d,   cv2_b,   cv2_h   = collect_all("cv2")
mpl_d,   mpl_b,   mpl_h   = collect_all("matplotlib")
pil_d,   pil_b,   pil_h   = collect_all("PIL")

all_datas    = vispy_d    + cv2_d    + mpl_d    + pil_d
all_binaries = vispy_b    + cv2_b    + mpl_b    + pil_b
all_hidden   = vispy_h    + cv2_h    + mpl_h    + pil_h + [
    # tkinter (stdlib — must be explicit on some platforms)
    "tkinter", "tkinter.ttk", "tkinter.scrolledtext", "_tkinter",
    # matplotlib 3D
    "mpl_toolkits", "mpl_toolkits.mplot3d",
    # requests extras
    "requests", "urllib3", "charset_normalizer", "certifi", "idna",
    # numpy internals
    "numpy.core._multiarray_umath",
    # blinkymap itself
    "blinkymap", "blinkymap.app", "blinkymap.controller",
    "blinkymap.capture", "blinkymap.triangulate",
    "blinkymap.export", "blinkymap.viewer3d",
]

# ── analysis ──────────────────────────────────────────────────────────────────

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=all_binaries,
    datas=all_datas,
    hiddenimports=all_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # keep size down — none of these are needed
        "IPython", "jupyter", "notebook", "scipy", "pandas",
        "PyQt5", "PyQt6", "PySide2", "PySide6",
        "wx", "gi",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BlinkyMap",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # No console window on Windows (set to True if you need to debug crashes)
    console=False,
    # icon="assets/icon.ico",   # uncomment once you have an icon file
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="BlinkyMap",
)

# macOS .app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="BlinkyMap.app",
        # icon="assets/icon.icns",
        bundle_identifier="com.blinkymap.app",
        info_plist={
            "CFBundleShortVersionString": "0.1.0",
            "NSCameraUsageDescription":
                "BlinkyMap uses your camera to detect lit pixels on the tree.",
            "NSHighResolutionCapable": True,
        },
    )
