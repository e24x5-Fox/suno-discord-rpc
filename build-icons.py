#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build-icons.py — готовит наборы иконок приложения из исходных PNG.

    python build-icons.py

Исходники лежат в desktop/src/icons/source/ (по одному PNG на вариант), результат
кладётся в desktop/src/icons/variants/. Запускать после добавления или замены
исходной картинки; в репозитории хранятся и исходники, и результат, чтобы сборка
не требовала Pillow.

Для каждого варианта делается три вещи:
  <slug>.ico              — окно приложения и кнопка на панели задач (много размеров)
  <slug>-tray-<st>.ico    — трей: та же картинка плюс точка состояния
  <slug>-preview.png      — превью 96px для выбора варианта в настройках

Точка состояния нужна, потому что до появления пользовательских иконок трей сам
рисовался кодом и показывал, играет ли музыка и подключён ли Discord. На готовой
картинке глиф не нарисуешь, поэтому оба признака свёрнуты в цвет одной точки:
  green — Discord подключён, идёт воспроизведение
  amber — Discord подключён, пауза
  gray  — Discord не подключён
"""

import os
import sys

from PIL import Image, ImageDraw

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT, "desktop", "src", "icons", "source")
OUT_DIR = os.path.join(ROOT, "desktop", "src", "icons", "variants")

# Порядок здесь = порядок в настройках; первый вариант считается основным.
VARIANTS = [
    ("play",      "иконка рпц сервака .png",             "Плеер"),
    ("play-solid", "иконка рпц сервака 2.png",           "Плеер (сплошной угол)"),
    ("fox",       "иконка рпц сервака бойкиссер.png",     "Бойкиссер"),
    ("fox-solid", "иконка рпц сервака бойкиссер 2.png",   "Бойкиссер (сплошной угол)"),
]

WINDOW_SIZES = [16, 24, 32, 48, 64, 128, 256]
TRAY_SIZES = [16, 20, 24, 32]
PREVIEW_SIZE = 96

DOT_COLORS = {
    "green": (34, 197, 94, 255),
    "amber": (234, 179, 8, 255),
    "gray":  (110, 110, 122, 255),
}


def with_status_dot(img: Image.Image, color) -> Image.Image:
    """Дорисовывает в правый нижний угол точку состояния с тёмной обводкой.

    Обводка обязательна: без неё зелёная точка теряется на светлой части
    рисунка (у «бойкиссера» там белая шерсть), и состояние не читается.
    """
    im = img.copy().convert("RGBA")
    w, h = im.size
    d = ImageDraw.Draw(im)
    r = int(w * 0.30)                 # диаметр точки — 30% ширины иконки
    pad = int(w * 0.04)
    x1, y1 = w - pad - r, h - pad - r
    x2, y2 = w - pad, h - pad
    ring = max(2, int(w * 0.035))
    d.ellipse([x1 - ring, y1 - ring, x2 + ring, y2 + ring], fill=(18, 18, 22, 235))
    d.ellipse([x1, y1, x2, y2], fill=color)
    return im


def save_ico(img: Image.Image, path: str, sizes):
    # Каждый размер масштабируем отдельно с LANCZOS: если отдать Pillow один
    # большой кадр и список размеров, мелкие 16–24px получаются заметно грязнее.
    frames = [img.resize((s, s), Image.LANCZOS) for s in sizes]
    frames[-1].save(path, format="ICO",
                    sizes=[(s, s) for s in sizes],
                    append_images=frames[:-1])


def main():
    if not os.path.isdir(SRC_DIR):
        print(f"[ОШИБКА] нет папки с исходниками: {SRC_DIR}")
        return 1
    os.makedirs(OUT_DIR, exist_ok=True)

    made = 0
    for slug, filename, label in VARIANTS:
        src = os.path.join(SRC_DIR, filename)
        if not os.path.isfile(src):
            print(f"[ПРОПУСК] нет файла {filename}")
            continue

        img = Image.open(src).convert("RGBA")
        save_ico(img, os.path.join(OUT_DIR, f"{slug}.ico"), WINDOW_SIZES)
        for state, color in DOT_COLORS.items():
            save_ico(with_status_dot(img, color),
                     os.path.join(OUT_DIR, f"{slug}-tray-{state}.ico"), TRAY_SIZES)
        img.resize((PREVIEW_SIZE, PREVIEW_SIZE), Image.LANCZOS).save(
            os.path.join(OUT_DIR, f"{slug}-preview.png"))

        print(f"  {slug:11} «{label}»  ← {filename}")
        made += 1

    if not made:
        print("[ОШИБКА] не собрано ни одного варианта")
        return 1

    # app.ico — то, что electron-builder зашивает в .exe и установщик. Меняется
    # только пересборкой приложения: иконку самого exe в рантайме не подменить,
    # поэтому здесь всегда основной (первый) вариант.
    main_slug = VARIANTS[0][0]
    with open(os.path.join(OUT_DIR, f"{main_slug}.ico"), "rb") as fr, \
         open(os.path.join(ROOT, "desktop", "src", "icons", "app.ico"), "wb") as fw:
        fw.write(fr.read())
    print(f"\napp.ico обновлён из варианта «{main_slug}» (иконка .exe и установщика)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
