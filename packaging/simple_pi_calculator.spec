# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Simple PI Calculator (onedir, windowed, Windows).
#
# Build from the "packaging" directory so the relative paths below resolve
# (see packaging/build_windows.ps1 and .github/workflows/build-windows.yml):
#
#   cd packaging
#   pyinstaller --noconfirm --clean simple_pi_calculator.spec
#
# Produces dist/SimplePICalculator/SimplePICalculator.exe (onedir build).
#
# Per docs/DESIGN.md §7.1.

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# This file lives in <repo>/packaging/, the package lives in <repo>/src/simple_pi_calculator
PACKAGING_DIR = os.path.dirname(os.path.abspath(SPEC))
REPO_ROOT = os.path.dirname(PACKAGING_DIR)
SRC_DIR = os.path.join(REPO_ROOT, "src")
ENTRY_SCRIPT = os.path.join(SRC_DIR, "simple_pi_calculator", "__main__.py")
EXAMPLES_DIR = os.path.join(REPO_ROOT, "examples")
VERSION_FILE = os.path.join(PACKAGING_DIR, "version_info.txt")

# --- datas -------------------------------------------------------------
# help/** and resources/** ship inside the package; examples/** ships next
# to the package data at the top level of the onedir bundle.
datas = collect_data_files(
    "simple_pi_calculator",
    includes=["help/**/*", "resources/**/*"],
)
if os.path.isdir(EXAMPLES_DIR):
    datas.append((EXAMPLES_DIR, "examples"))

# --- hidden imports ------------------------------------------------------
# pyqtgraph imports Qt-binding-specific template modules dynamically
# (e.g. pyqtgraph.graphicsItems.ViewBox.axisCtrlTemplate_pyside6), so the
# whole package must be pulled in explicitly rather than relying on static
# import analysis.
hiddenimports = []
hiddenimports += collect_submodules("pyqtgraph")
hiddenimports += [
    "pyqtgraph.exporters",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtSvg",
    "PySide6.QtPrintSupport",  # used by pyqtgraph exporters
    "openpyxl.cell._writer",
]

# --- excludes --------------------------------------------------------
# Large Qt/Python modules the app never touches; trimming these keeps the
# onedir bundle smaller and the startup import scan faster. QtSvg (icon /
# help rendering) and the platform plugins are deliberately NOT excluded.
# Kept deliberately conservative: only modules the app cannot plausibly need
# (per docs/DESIGN.md - Qt bindings limited to PySide6, core must not import
# scipy, no other GUI toolkits). PySide6.QtSvg and the platform plugins are
# NOT excluded - QtSvg renders the app icon/help images and the platform
# plugin (qwindows.dll) is required for the app to start at all.
excludes = [
    "tkinter",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQml",
    "PySide6.QtQmlModels",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth",
    "PyQt5",
    "PyQt6",
    "matplotlib",
    "scipy",
    "IPython",
]

a = Analysis(
    [ENTRY_SCRIPT],
    pathex=[SRC_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SimplePICalculator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed exes trigger AV false positives; keep uncompressed.
    console=False,  # windowed app
    icon=os.path.join(SRC_DIR, "simple_pi_calculator", "resources", "app.ico"),
    version=VERSION_FILE if os.path.isfile(VERSION_FILE) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SimplePICalculator",
)
