# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files
from tools.package_policy import filter_webengine_locales

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets', 'assets'),   # 帶入 CSS 檔案
        ('ICON/icon.ico', 'ICON'),
    ] + collect_data_files('pptx') + collect_data_files('docx'),
    hiddenimports=[
        'app.render_service',
        'app.render_worker',
        'pygments.lexers._mapping',
        'pygments.formatters.html',
        'mdit_py_plugins.tasklists',
        'mdit_py_plugins.front_matter',
        'mdit_py_plugins.footnote',
        'mdit_py_plugins.deflist',
        'mdit_py_plugins.dollarmath',
        'linkify_it',
        'uc_micro',
        'pymupdf',
        'pptx',
        'docx',
        'lxml.etree',
        'lxml._elementpath',
        'PySide6.QtPdf',
        'PySide6.QtPdfWidgets',
        'PySide6.QtWebChannel',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtSvg',
        'shiboken6',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# The desktop UI is Chinese; retain both Chinese locales plus Chromium's
# English fallback. Never filter resource packs outside qtwebengine_locales.
a.datas = filter_webengine_locales(a.datas)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MarkdownViewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 不顯示黑色命令列視窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='ICON/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MarkdownViewer',
)
