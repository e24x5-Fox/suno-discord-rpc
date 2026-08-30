#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build-extension.py — собирает расширение в ZIP для установки в браузер.

    python build-extension.py                  # youtube-extension
    python build-extension.py extension        # suno-расширение

Кладёт в dist/ два файла с одинаковым содержимым: .zip (Chrome, Edge, Яндекс,
Opera — и он же загружается на Chrome Web Store) и .xpi (Firefox: about:debugging
принимает и .zip, но .xpi открывается двойным кликом и не путается с архивами).

Пишем архив вручную через zipfile, а не Compress-Archive из PowerShell:
последний в некоторых версиях кладёт в записи обратные слэши, из-за чего
браузер не находит файлы внутри архива. Пути здесь всегда с прямыми слэшами,
время фиксировано — сборка одного и того же исходника даёт одинаковый файл.
"""

import json
import os
import sys
import zipfile

# Консоль Windows по умолчанию в cp1251, а печатать приходится и кириллицу, и
# название расширения со стрелкой «→» — без этого скрипт падал на выводе итога,
# уже собрав архив.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(ROOT, "dist")

# Мусор, который не должен попасть в архив: браузер ругается на посторонние
# файлы, а .pem — это приватный ключ подписи, ему в раздаваемом архиве не место.
SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
SKIP_EXT = {".pem", ".crx", ".zip", ".xpi", ".log", ".bak"}
SKIP_DIRS = {"node_modules", "__pycache__", ".git"}

# Дата внутри архива фиксирована, иначе одинаковый исходник давал бы разные
# файлы при каждой сборке (в ZIP пишется время модификации).
FIXED_DATE = (2026, 1, 1, 0, 0, 0)


def collect(src_dir):
    files = []
    for dirpath, dirnames, filenames in os.walk(src_dir):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            if name in SKIP_NAMES or os.path.splitext(name)[1].lower() in SKIP_EXT:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, src_dir).replace(os.sep, "/")
            files.append((full, rel))
    return sorted(files, key=lambda t: t[1])


def build(src_name):
    src_dir = os.path.join(ROOT, src_name)
    manifest_path = os.path.join(src_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        print(f"[ОШИБКА] не найден {manifest_path}")
        return 1

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    version = manifest.get("version", "0.0.0")

    files = collect(src_dir)
    if not any(rel == "manifest.json" for _, rel in files):
        print("[ОШИБКА] manifest.json не попал в архив")
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    base = f"{src_name}-{version}"
    zip_path = os.path.join(OUT_DIR, base + ".zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for full, rel in files:
            info = zipfile.ZipInfo(rel, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(full, "rb") as f:
                z.writestr(info, f.read())

    # .xpi — тот же ZIP под другим именем, отдельная сборка не нужна.
    xpi_path = os.path.join(OUT_DIR, base + ".xpi")
    with open(zip_path, "rb") as fr, open(xpi_path, "wb") as fw:
        fw.write(fr.read())

    size = os.path.getsize(zip_path)
    print(f"Собрано «{manifest.get('name', src_name)}» v{version}, файлов: {len(files)}")
    for _, rel in files:
        print("   ", rel)
    print(f"\n  dist/{base}.zip  ({size / 1024:.1f} КБ)  — Chrome / Edge / Яндекс / Opera")
    print(f"  dist/{base}.xpi  ({size / 1024:.1f} КБ)  — Firefox")
    return 0


if __name__ == "__main__":
    sys.exit(build(sys.argv[1] if len(sys.argv) > 1 else "youtube-extension"))
