# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files
from tools.package_policy import (
    agent_bundle_datas,
    agent_bundle_source,
    filter_webengine_locales,
    stdlib_imports,
)

block_cipher = None

EXCLUDES = ['tkinter', 'unittest']

# Optional bundled Agent Watchdog (agent_watchdog/aiwatch.py). It is shipped as
# data and run by this executable (--agent-watchdog), so PyInstaller cannot see
# what it imports: its standard-library imports become hidden imports. Without
# the directory both lists are empty and the build is unchanged.
_agent_source = agent_bundle_source(SPECPATH)
_agent_datas = agent_bundle_datas(_agent_source)
_agent_hiddenimports = stdlib_imports(_agent_source, excludes=EXCLUDES)

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets', 'assets'),   # 帶入 CSS 檔案
        ('ICON/icon.ico', 'ICON'),
    ] + collect_data_files('pptx') + collect_data_files('docx') + _agent_datas,
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
    ] + _agent_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
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
