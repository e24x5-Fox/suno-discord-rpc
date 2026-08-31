# -*- mode: python ; coding: utf-8 -*-
# Сборка headless-бэкенда для Electron-приложения desktop/.
#
# console=True — намеренно, хотя окна консоли пользователь не увидит: Electron
# запускает этот exe через spawn с windowsHide, и в консольной сборке PyInstaller
# отдаёт родителю настоящие stdout/stderr, которые уходят в лог интерфейса.
# В windowed-сборке (console=False) их бы не было, и любая ошибка бэкенда
# оставалась бы невидимой — ровно то, ради чего в интерфейсе есть вкладка «Логи».

HIDDEN_IMPORTS = [
    'websockets',
    'websockets.legacy',
    'websockets.legacy.server',
    'websockets.legacy.client',
    'websockets.asyncio',
    'websockets.asyncio.server',
    'websockets.asyncio.client',
    'pypresence',
    'pypresence.presence',
    'pypresence.baseclient',
    'pypresence.payloads',
    'pypresence.utils',
    'asyncio',
    'asyncio.base_events',
    'asyncio.events',
    'asyncio.futures',
    'asyncio.tasks',
    'asyncio.streams',
    'asyncio.subprocess',
    'asyncio.windows_events',
    'asyncio.windows_utils',
    'configparser',
    'socket',
    'threading',
    'json',
    'io',
    'struct',
    'aiohttp',
    'aiohttp.web',
    'multidict',
    'yarl',
    'aiosignal',
    'frozenlist',
    'suno_stats',
    'youtube_stats',
]

a = Analysis(
    ['suno_rpc.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Первый список — интерфейс, уехавший в Electron. Второй — пакеты, которые
    # бэкенд не импортирует вообще: PyInstaller тянул их транзитивно (через
    # setuptools/pkg_resources и numpy), раздувая exe с ~15 до 77 МБ. Это
    # особенно важно теперь: сверху в установщик ложится ещё ~150 МБ Electron.
    excludes=[
        'pystray', 'PIL', 'webview', 'clr_loader', 'pythonnet', 'tkinter',
        'numpy', 'matplotlib', 'scipy', 'pandas', 'IPython', 'pytest', '_pytest',
        'pydantic', 'werkzeug', 'jedi', 'prompt_toolkit', 'rich', 'markdown_it',
        'pygments', 'PyQt5', 'PySide2', 'setuptools', 'pkg_resources',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='suno-rpc-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='suno_rpc.ico',
)
