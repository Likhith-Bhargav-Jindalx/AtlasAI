# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['desktop/app.py'],
    pathex=['engine/src'],
    binaries=[],
    datas=[('frontend/dist', 'frontend_dist'), ('desktop/production.env', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='atlas-desktop',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='atlas-desktop',
)
app = BUNDLE(
    coll,
    name='atlas-desktop.app',
    icon=None,
    bundle_identifier=None,
)
