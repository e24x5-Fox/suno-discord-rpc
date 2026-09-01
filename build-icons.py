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

import json
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
# Картинки для Discord: он тянет их по ссылке из репозитория, в установщике им
# делать нечего (package.json исключает эту папку так же, как source).
DISC_DIR = os.path.join(ROOT, "desktop", "src", "icons", "discord")

# Имя файла обязано совпадать со слагом: из слага собирается публичная ссылка
# на картинку для Discord (app_icon_url в suno_rpc.py), а Discord скачивает её
# сам. Кириллица в имени сюда не годится — ссылка тогда состоит из процентных
# escape-последовательностей и ломается при любом переносе файла.
#
# Порядок здесь = порядок плиток в настройках; первый образ считается основным.
BASES = [
    ("play",     "Плеер"),
    ("fox",      "Бойкиссер"),
    ("stand",    "Во весь рост"),
    ("glee",     "Интересное радостное чувство"),
    ("cute-wow", "Милое удивление"),
    ("huh",      "Не понял"),
    ("sulk",     "Обида"),
    ("sad",      "Огорчение"),
    ("sprawl",   "Ожидание (развалился)"),
    ("wait",     "Ожидание"),
    ("chonk",    "Просто толстая кость"),
    ("shy",      "Смущение"),
    ("shame",    "Стыдно"),
    ("starfish", "Счастье (звёздочкой)"),
    ("joy",      "Счастье"),
    ("wow",      "Удивление"),
    ("aww",      "Умиление"),
    ("aww-2",    "Умиление 2"),
    ("aww-3",    "Умиление 3"),
    ("aww-4",    "Умиление 4"),
    ("calm",     "Умиротворение"),
    ("tired",    "Усталость"),
    ("smirk",    "Прищур"),
]

# Формы плашки. Каждый образ существует во всех трёх, но собирается только та,
# для которой в source/ реально лежит картинка: у старых образов (например
# «Плеер») круглого варианта нет, и это не ошибка.
SHAPES = [
    ("",       "corner", "с уголком"),
    ("-solid", "square", "сплошной угол"),
    ("-round", "round",  "круг"),
]

# То, что собралось, записывается сюда — этот файл читает main.js, чтобы список
# иконок в настройках не приходилось держать вторым экземпляром вручную.
INDEX_FILE = os.path.join(ROOT, "desktop", "src", "icons", "variants.json")

# Кружок рядом с обложкой Discord обрезает картинку РОВНО по вписанной
# окружности, поэтому углы квадратной плашки срезаются первыми — вместе с тем
# самым уголком, ради которого у каждой иконки есть два варианта. В круг
# вписывается квадрат со стороной d/√2 ≈ 0.707d, так что иконку для кружка
# уменьшаем до 70% и центрируем на прозрачном холсте: тогда в круг попадает
# вся плашка целиком, с углом и обводкой.
CIRCLE_SCALE = 0.70
CIRCLE_SIZE = 512

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


def fit_for_circle(img: Image.Image, shape: str) -> Image.Image:
    """Уменьшает иконку так, чтобы круглая обрезка Discord её не срезала.

    Круглому варианту ужиматься не нужно: он сам вписан в окружность, и уменьши
    мы его — вокруг иконки в Discord осталось бы пустое кольцо.
    """
    im = img.convert("RGBA").resize((CIRCLE_SIZE, CIRCLE_SIZE), Image.LANCZOS)
    if shape == "round":
        return im
    side = int(CIRCLE_SIZE * CIRCLE_SCALE)
    inner = im.resize((side, side), Image.LANCZOS)
    out = Image.new("RGBA", (CIRCLE_SIZE, CIRCLE_SIZE), (0, 0, 0, 0))
    out.alpha_composite(inner, ((CIRCLE_SIZE - side) // 2,) * 2)
    return out


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
    os.makedirs(DISC_DIR, exist_ok=True)

    made, index = 0, []
    for base, label in BASES:
        for suffix, shape, shape_label in SHAPES:
            slug = base + suffix
            src = os.path.join(SRC_DIR, f"{slug}.png")
            if not os.path.isfile(src):
                continue

            img = Image.open(src).convert("RGBA")
            save_ico(img, os.path.join(OUT_DIR, f"{slug}.ico"), WINDOW_SIZES)
            for state, color in DOT_COLORS.items():
                save_ico(with_status_dot(img, color),
                         os.path.join(OUT_DIR, f"{slug}-tray-{state}.ico"), TRAY_SIZES)
            img.resize((PREVIEW_SIZE, PREVIEW_SIZE), Image.LANCZOS).save(
                os.path.join(OUT_DIR, f"{slug}-preview.png"))
            fit_for_circle(img, shape).save(os.path.join(DISC_DIR, f"{slug}-circle.png"))

            index.append({"id": slug, "base": base, "shape": shape,
                          "label": label, "shapeLabel": shape_label})
            print(f"  {slug:16} «{label}» — {shape_label}")
            made += 1

    if not made:
        print("[ОШИБКА] не собрано ни одного варианта")
        return 1

    # app.ico — то, что electron-builder зашивает в .exe и установщик. Меняется
    # только пересборкой приложения: иконку самого exe в рантайме не подменить,
    # поэтому здесь всегда основной (первый) вариант.
    main_slug = index[0]["id"]
    with open(os.path.join(OUT_DIR, f"{main_slug}.ico"), "rb") as fr, \
         open(os.path.join(ROOT, "desktop", "src", "icons", "app.ico"), "wb") as fw:
        fw.write(fr.read())
    print(f"\napp.ico обновлён из варианта «{main_slug}» (иконка .exe и установщика)")

    # Список для main.js: пусть приложение читает то, что реально собралось, а не
    # второй экземпляр таблицы, который забудут дополнить при добавлении иконки.
    with open(INDEX_FILE, "w", encoding="utf-8") as fw:
        json.dump(index, fw, ensure_ascii=False, indent=2)
    print(f"{os.path.basename(INDEX_FILE)}: {len(index)} вариантов для настроек")
    return 0


if __name__ == "__main__":
    sys.exit(main())
