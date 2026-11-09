# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ["ct_share/admin_main.py"],
    pathex=["."],
    binaries=[],
    datas=[("config.example.json", ".")],
    hiddenimports=[
        "ct_share",
        "ct_share.admin_main",
        "ct_share.admin_ui",
        "ct_share.common",
        "ct_share.install",
        "ct_share.paths",
        "ct_share.setup_dialog",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CTShareAdmin",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
