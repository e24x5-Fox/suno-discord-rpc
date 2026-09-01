#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import-icons.py — вытаскивает персонажей прямо из файла Krita.

    python import-icons.py                # собрать всё
    python import-icons.py --list         # показать дерево слоёв
    python import-icons.py --file X.kra   # другой файл

Krita для этого запускать не нужно: .kra — это zip, внутри maindoc.xml с деревом
слоёв и по файлу на слой в тайловом формате Krita (LZF). Скрипт распаковывает
тайлы сам и складывает слои так же, как это делает Krita, — все слои в проекте
иконок идут в режиме «обычный», поэтому хватает обычного альфа-композита плюс
непрозрачность слоя и маски прозрачности групп.

Результат — по одному PNG 512×512 на каждого персонажа в каждом варианте
шаблона, в desktop/src/icons/source/. Оттуда их забирает build-icons.py и делает
.ico для окна и трея. Шаблоны и маски обрезки заодно выкладываются отдельными
файлами в desktop/src/icons/templates/ — они нужны редактору иконок внутри
приложения.

Сопоставление «слой в Krita → имя файла» держится в CHARACTERS ниже. Если в
проекте появился новый персонаж, скрипт скажет, что не знает такого слоя, и
покажет его имя — добавь строчку в таблицу.
"""

import argparse
import os
import sys
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image, ImageChops

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_KRA = os.path.join(ROOT, "икона", "исход", "италон.kra")
SRC_DIR = os.path.join(ROOT, "desktop", "src", "icons", "source")
TPL_DIR = os.path.join(ROOT, "desktop", "src", "icons", "templates")
OUT_SIZE = 512

NS = "{http://www.calligra.org/DTD/krita}"

# ── Что в проекте считается чем ────────────────────────────────────────────
#
# Имена берутся из панели слоёв Krita — посмотреть их можно через --list.
# Слой ищется сначала по имени, потом по внутреннему имени файла (layer42 и
# т.п.): имя можно переименовать, а имя файла меняется, если слой удалить и
# нарисовать заново, так что ни то ни другое не вечно.

# Группа, внутри которой лежат группы персонажей (по одной на персонажа).
CHARACTERS_GROUP = "персонажи"

# Слои плашек и масок обрезки.
SQUARE_PLATE = "шаблон стандартного квадрата без угла или с углом"
ROUND_PLATE = "шаблон круглого игонка"
CORNER_PATCH = "Обычный слой 13"      # закрывает вырезанный угол
SQUARE_MASK = "маска для квадратного шаблона"
ROUND_MASK = "маска для круглого шаблона"

# Варианты шаблона: суффикс имени файла → какие слои включить или выключить.
# Слои шаблона общие для всех персонажей, поэтому вариант — это просто другой
# набор видимых слоёв, а не отдельная сборка.
#
# masks — маски обрезки на группе персонажей. Их может быть несколько: Krita
# перемножает их одну за другой, и здесь так же. Круглому варианту нужны обе.
# Квадратная задаёт границу, в которой вообще рисовался персонаж: у двадцати
# образов из двадцати одного слой обводки выходит за плашку — там остались
# куски рамки от квадратного шаблона. Под квадратной маской они всегда были
# срезаны, а круглая верх не режет вовсе (уши должны торчать), и эти остатки
# вылезали в углах иконки светлым уголком рамки.
TEMPLATES = [
    {"suffix": "", "name": "квадрат с уголком", "masks": [SQUARE_MASK], "layers": {
        SQUARE_PLATE: True, ROUND_PLATE: False, CORNER_PATCH: False}},
    {"suffix": "-solid", "name": "квадрат", "masks": [SQUARE_MASK], "layers": {
        SQUARE_PLATE: True, ROUND_PLATE: False, CORNER_PATCH: True}},
    {"suffix": "-round", "name": "круг", "masks": [SQUARE_MASK, ROUND_MASK], "layers": {
        SQUARE_PLATE: False, ROUND_PLATE: True, CORNER_PATCH: False}},
]

# Группа со свечением. Она общая для всех плашек и лежит между ними и
# персонажами, а её собственная маска прозрачности в проекте нарисована под
# квадрат. У круглой плашки свечение из-за этого вылезало за кольцо светлой
# полосой и вдобавок читалось как удвоенное — снаружи круга ничто не гасило его
# края. Поэтому свечение всегда обрезается по форме самой плашки: у квадратных
# вариантов это ничего не меняет (форма и так совпадает), у круглого чинит и то
# и другое. Персонажа обрезка не касается — ему за края выходить положено.
GLOW_GROUP = "градиент"

# Группы, которые не участвуют в сборке (черновики, старые версии). Лежат среди
# групп персонажей, но персонажами не являются.
HIDE_ALWAYS = ["Группа 14"]

# Слой с картинкой персонажа внутри его группы → слаг и подпись в настройках.
# Слаг обязан быть латиницей: из него собирается публичная ссылка на картинку
# для Discord (см. комментарий в build-icons.py).
CHARACTERS = {
    "бойкиссер 837465.png":                ("stand",    "Во весь рост"),
    "интересное радастное чувство....png": ("glee",     "Интересное радостное чувство"),
    "милое удивление.png":                 ("cute-wow", "Милое удивление"),
    "не понял.png":                        ("huh",      "Не понял"),
    "обида.png":                           ("sulk",     "Обида"),
    "огорчение.png":                       ("sad",      "Огорчение"),
    "ожидание 938475.png":                 ("sprawl",   "Ожидание (развалился)"),
    "ожидание.png":                        ("wait",     "Ожидание"),
    "просто толстая кость.png":            ("chonk",    "Просто толстая кость"),
    "смущение.png":                        ("shy",      "Смущение"),
    "стыдно.png":                          ("shame",    "Стыдно"),
    "счастье 93485.png":                   ("starfish", "Счастье (звёздочкой)"),
    "счастье.png":                         ("joy",      "Счастье"),
    "удивление.png":                       ("wow",      "Удивление"),
    "умиление 34095.png":                  ("aww",      "Умиление"),
    "умиление 834675.png":                 ("aww-2",    "Умиление 2"),
    "умиление 843756.png":                 ("aww-3",    "Умиление 3"),
    "умиление.png":                        ("aww-4",    "Умиление 4"),
    "умиротворение.png":                   ("calm",     "Умиротворение"),
    "усталость.png":                       ("tired",    "Усталость"),
    "боикиссер 23453425.png":              ("smirk",    "Прищур"),
}


# ── Чтение .kra ────────────────────────────────────────────────────────────

def lzf_decompress(src, expected):
    """Распаковка liblzf — этим Krita жмёт каждый тайл слоя."""
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        ctrl = src[i]
        i += 1
        if ctrl < 32:                      # подряд идущие несжатые байты
            out += src[i:i + ctrl + 1]
            i += ctrl + 1
        else:                              # ссылка назад по уже распакованному
            length = ctrl >> 5
            if length == 7:
                length += src[i]
                i += 1
            ref = len(out) - ((ctrl & 0x1f) << 8) - src[i] - 1
            i += 1
            for _ in range(length + 2):    # куски перекрываются, поэтому побайтно
                out.append(out[ref])
                ref += 1
    if len(out) != expected:
        raise ValueError(f"тайл распаковался в {len(out)} байт вместо {expected}")
    return bytes(out)


class Kra:
    def __init__(self, path):
        self.zip = zipfile.ZipFile(path)
        root = ET.fromstring(self.zip.read("maindoc.xml"))
        self.image = root.find(NS + "IMAGE")
        self.width = int(self.image.get("width"))
        self.height = int(self.image.get("height"))
        self.nodes = list(self.image.find(NS + "layers"))
        self._cache = {}

    def _entry(self, name):
        # Имена внутри архива начинаются с названия документа, а оно бывает
        # кириллическим — zipfile тогда декодирует их из cp437, и точное имя не
        # предскажешь. Поэтому ищем по хвосту.
        suffix = "/layers/" + name
        return next((n for n in self.zip.namelist() if n.endswith(suffix)), None)

    def pixels(self, name):
        """Пиксели одного слоя или маски, разложенные на холст целиком."""
        if name in self._cache:
            return self._cache[name]
        entry = self._entry(name)
        if entry is None:
            return None
        data = self.zip.read(entry)
        default_entry = self._entry(name + ".defaultpixel")
        default = self.zip.read(default_entry) if default_entry else None

        pos, hdr = 0, {}
        for _ in range(5):                 # VERSION/TILEWIDTH/TILEHEIGHT/PIXELSIZE/DATA
            eol = data.index(b"\n", pos)
            key, _, value = data[pos:eol].decode("ascii").partition(" ")
            hdr[key] = value
            pos = eol + 1
        tw, th = int(hdr["TILEWIDTH"]), int(hdr["TILEHEIGHT"])
        ps, count = int(hdr["PIXELSIZE"]), int(hdr["DATA"])
        tile_bytes = tw * th * ps

        # У маски один канал, у обычного слоя четыре.
        if ps == 4:
            canvas = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        else:
            canvas = Image.new("L", (self.width, self.height), default[0] if default else 0)

        for _ in range(count):
            eol = data.index(b"\n", pos)
            x, y, _compression, size = data[pos:eol].decode("ascii").split(",")
            pos = eol + 1
            blob = data[pos:pos + int(size)]
            pos += int(size)
            # Первый байт — флаг сжатия, дальше сами данные.
            raw = lzf_decompress(blob[1:], tile_bytes) if blob[0] else blob[1:1 + tile_bytes]
            if ps == 4:
                # Внутри тайла каналы лежат не вперемешку, а подряд, и порядок у
                # Krita свой: B, G, R, A.
                b, g, r, a = (raw[c * tw * th:(c + 1) * tw * th] for c in range(4))
                px = bytearray(tw * th * 4)
                px[0::4], px[1::4], px[2::4], px[3::4] = r, g, b, a
                canvas.alpha_composite(Image.frombytes("RGBA", (tw, th), bytes(px)),
                                       (int(x), int(y)))
            else:
                canvas.paste(Image.frombytes("L", (tw, th), raw), (int(x), int(y)))
        self._cache[name] = canvas
        return canvas

    def render(self, nodes, visibility):
        """Складывает слои снизу вверх. visibility: имя или файл слоя → видимость."""
        out = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        kids = [n for n in nodes if n.tag.replace(NS, "") in ("layer", "mask")]
        masks = [n for n in kids if n.get("nodetype", "").endswith("mask")]
        layers = [n for n in kids if not n.get("nodetype", "").endswith("mask")]

        for node in reversed(layers):
            visible = visibility.get(node.get("name"),
                                     visibility.get(node.get("filename"),
                                                    node.get("visible") == "1"))
            if not visible:
                continue
            if node.get("nodetype") == "grouplayer":
                sub = node.find(NS + "layers")
                img = self.render(list(sub), visibility) if sub is not None else None
            else:
                img = self.pixels(node.get("filename"))
            if img is None:
                continue
            opacity = int(node.get("opacity", 255))
            if opacity < 255:
                img = img.copy()
                img.putalpha(img.getchannel("A").point(lambda v: v * opacity // 255))
            out.alpha_composite(img)

        # Маска прозрачности группы просто умножает альфу всего, что под ней.
        # Её тоже можно включать и выключать через visibility: у круглого и
        # квадратного шаблонов маски разные, а лежат обе на одной группе.
        for mask in masks:
            if mask.get("nodetype") != "transparencymask":
                continue
            visible = visibility.get(mask.get("name"),
                                     visibility.get(mask.get("filename"),
                                                    mask.get("visible") == "1"))
            if not visible:
                continue
            alpha = self.pixels(mask.get("filename") + ".pixelselection")
            if alpha is not None:
                out.putalpha(ImageChops.multiply(out.getchannel("A"), alpha))
        return out


def find_node(nodes, name):
    """Ищет узел по имени (или по внутреннему имени файла) на любой глубине."""
    for node in nodes:
        if node.tag.replace(NS, "") not in ("layer", "mask"):
            continue
        if node.get("name") == name or node.get("filename") == name:
            return node
        sub = node.find(NS + "layers")
        if sub is not None:
            found = find_node(list(sub), name)
            if found is not None:
                return found
    return None


def print_tree(nodes, depth=0):
    for node in nodes:
        if node.tag.replace(NS, "") not in ("layer", "mask"):
            continue
        flag = "  " if node.get("visible") == "1" else "· "
        print(f'{"  " * depth}{flag}{node.get("name")}   '
              f'[{node.get("nodetype")}, {node.get("filename")}]')
        sub = node.find(NS + "layers")
        if sub is not None:
            print_tree(list(sub), depth + 1)


# ── Сборка ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Импорт иконок из файла Krita")
    parser.add_argument("--file", default=DEFAULT_KRA, help="путь к .kra")
    parser.add_argument("--list", action="store_true", help="показать дерево слоёв и выйти")
    args = parser.parse_args()

    if not os.path.isfile(args.file):
        print(f"[ОШИБКА] нет файла: {args.file}")
        return 1
    kra = Kra(args.file)

    if args.list:
        print(f"{args.file}  —  {kra.width}×{kra.height}\n")
        print_tree(kra.nodes)
        return 0

    group = find_node(kra.nodes, CHARACTERS_GROUP)
    if group is None:
        print(f"[ОШИБКА] в файле нет группы «{CHARACTERS_GROUP}» — посмотри --list "
              "и поправь CHARACTERS_GROUP в начале скрипта")
        return 1
    characters = [n for n in group.find(NS + "layers")
                  if n.get("nodetype") == "grouplayer" and n.get("name") not in HIDE_ALWAYS]

    os.makedirs(SRC_DIR, exist_ok=True)
    os.makedirs(TPL_DIR, exist_ok=True)

    # Базовая видимость: черновые группы скрыты, все персонажи скрыты — дальше
    # включаем ровно одного. Маски обрезки тоже гасим все: нужную включит вариант.
    all_masks = [n.get("name") for n in group.find(NS + "layers")
                 if n.get("nodetype") == "transparencymask"]
    base = {name: False for name in HIDE_ALWAYS + all_masks}
    base.update({c.get("filename"): False for c in characters})

    unknown, made = [], 0
    for template in TEMPLATES:
        suffix, template_name = template["suffix"], template["name"]
        template_layers = {**template["layers"],
                           **{name: True for name in template["masks"]}}
        missing = [layer for layer in template_layers if find_node(kra.nodes, layer) is None]
        if missing:
            print(f"[ПРОПУСК] шаблон «{template_name}»: в файле нет слоёв {missing}")
            continue

        # Плашка без свечения — она же форма, по которой свечение обрезается.
        bare = kra.render(kra.nodes, {**base, **template_layers,
                                      CHARACTERS_GROUP: False, GLOW_GROUP: False})
        glow = kra.render(kra.nodes, {**{n.get("name"): False for n in kra.nodes},
                                      GLOW_GROUP: True})
        glow.putalpha(ImageChops.multiply(glow.getchannel("A"), bare.getchannel("A")))

        # Шаблон без персонажа — он же ассет для редактора иконок в приложении.
        plate = bare.copy()
        plate.alpha_composite(glow)
        plate.resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS).save(
            os.path.join(TPL_DIR, f"plate{suffix or '-corner'}.png"))
        # Маска для редактора — произведение всех масок варианта, ровно то, чем
        # обрезается персонаж. Кладём её как чёрную картинку, у которой маска
        # лежит в АЛЬФЕ, а не в яркости: редактор в приложении обрезает персонажа
        # через canvas-операцию destination-in, а она смотрит именно на альфу, и
        # обычная чёрно-белая маска для неё была бы сплошным прямоугольником.
        mask = None
        for name in template["masks"]:
            layer = kra.pixels(find_node(kra.nodes, name).get("filename") + ".pixelselection")
            mask = layer if mask is None else ImageChops.multiply(mask, layer)
        mask = mask.resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS)
        rgba = Image.new("RGBA", mask.size, (0, 0, 0, 0))
        rgba.putalpha(mask)
        rgba.save(os.path.join(TPL_DIR, f"mask{suffix or '-corner'}.png"))

        print(f"\nШаблон «{template_name}»:")
        for char in characters:
            inner = [n.get("name") for n in char.find(NS + "layers")
                     if n.get("name", "").lower().endswith((".png", ".webp", ".jpg"))]
            key = next((n for n in inner if n in CHARACTERS), None)
            if key is None:
                if suffix == "":
                    unknown.append((char.get("name"), inner))
                continue
            slug, label = CHARACTERS[key]

            # Порядок слоёв в проекте: плашка → свечение → персонаж. Собираем
            # тремя проходами, чтобы свечение можно было обрезать по плашке, не
            # трогая персонажа: ему как раз положено выходить за её края.
            figure = kra.render(kra.nodes, {**base, **template_layers,
                                            **{n.get("name"): False for n in kra.nodes},
                                            CHARACTERS_GROUP: True,
                                            char.get("filename"): True})
            img = plate.copy()
            img.alpha_composite(figure)
            img.resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS).save(
                os.path.join(SRC_DIR, f"{slug}{suffix}.png"))
            made += 1
            print(f"  {slug + suffix:22} «{label}»  ← {char.get('name')}")

    if unknown:
        print("\n[ВНИМАНИЕ] незнакомые персонажи — добавь их в CHARACTERS:")
        for group_name, inner in unknown:
            print(f"  группа «{group_name}», слои с картинкой: {inner}")

    print(f"\nГотово: {made} файлов в {SRC_DIR}")
    print("Дальше: python build-icons.py — он сделает .ico для окна и трея.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
