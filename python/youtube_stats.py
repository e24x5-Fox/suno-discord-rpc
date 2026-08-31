# -*- coding: utf-8 -*-
"""
youtube_stats.py — статистика просмотров YouTube.

Хранилище отдельное от suno_stats (`youtube_stats.json`): видео и треки не
должны смешиваться в одном топе. А вот ОТРИСОВКА общая — отчёт собирается тем
же шаблоном и теми же функциями секций из suno_stats, только с другой шапкой,
и кладётся в файл-соседку `youtube_stats.html`. Пользователь открывает его в том
же окне статистики кнопкой в шапке (см. suno_stats.nav_button).

Формат записи полностью совпадает с suno_stats, иначе общие функции секций его
не отрисуют: title = название видео, artist = название канала, cover = превью,
url = ссылка на видео.
"""

import json
import os
import time
import datetime
import threading

import suno_stats

# Пути кладём рядом с суновскими, той же логикой «данные к данным, отчёт в temp».
STATS_FILE = os.path.join(os.path.dirname(suno_stats.STATS_FILE), "youtube_stats.json")
HTML_FILE  = suno_stats.YT_HTML_FILE

# Порог засчитывания просмотра. Ниже, чем у музыки (40%): ролики бывают
# длинными, и досматривать час лекции до 40%, чтобы просмотр засчитался,
# пришлось бы 24 минуты — а просмотр начался явно раньше.
VIEW_THRESHOLD  = 0.20
MIN_VIEW_SECONDS = 60   # либо просто 60 секунд подряд

_lock = threading.Lock()

# Свой трекер «что сейчас смотрим», независимый от такого же в suno_stats:
# музыка и видео могут идти одновременно, и общий трекер сбрасывал бы себя
# на каждом переключении между источниками.
_current_key      = None
_current_started  = 0.0
_view_counted     = False
_last_elapsed     = 0
_last_saved_elapsed = 0


# ══════════════════════════════════════════════════════════════════════════════
#  ХРАНИЛИЩЕ
# ══════════════════════════════════════════════════════════════════════════════

def _empty() -> dict:
    return {"version": 1, "months": {}, "generated_monthly": [], "generated_yearly": []}


def _load() -> dict:
    if not os.path.exists(STATS_FILE):
        return _empty()
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "months" not in data:
            return _empty()
        data.setdefault("generated_monthly", [])
        data.setdefault("generated_yearly", [])
        return data
    except Exception:
        return _empty()


def _save(data: dict):
    tmp = STATS_FILE + ".tmp"
    try:
        # Пишем через временный файл: обычная запись на месте оставляла бы
        # обрезанный JSON, если приложение закрыть ровно в этот момент, и вся
        # накопленная история просмотров терялась бы.
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATS_FILE)
    except Exception as e:
        print(f"⚠️  Не удалось сохранить статистику YouTube: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  УЧЁТ ПРОСМОТРОВ
# ══════════════════════════════════════════════════════════════════════════════

def on_video_update(title: str, channel: str, thumb_url: str, video_url: str,
                    elapsed: int, duration: int, is_paused: bool):
    """Вызывается на каждое обновление данных от youtube-расширения."""
    global _current_key, _current_started, _view_counted
    global _last_elapsed, _last_saved_elapsed

    title   = (title   or "Видео без названия").strip()
    channel = (channel or "YouTube").strip()
    video_url = (video_url or "").strip()

    # Ключ — ссылка на видео: у роликов разных каналов названия совпадают
    # сплошь и рядом, и по «title||channel» они бы слиплись в одну запись.
    key = video_url or f"{title}||{channel}"

    should_count  = False
    delta_seconds = 0

    with _lock:
        if key != _current_key:
            _current_key        = key
            _current_started    = time.time()
            _view_counted       = False
            _last_elapsed       = elapsed
            _last_saved_elapsed = elapsed
        else:
            # Любая перемотка назад сбрасывает опорную позицию. Проверка идёт
            # ДО условия «просмотр уже засчитан» намеренно: если засчитать ещё
            # не успели, а зритель отмотал ролик в начало, старая (большая)
            # опорная позиция осталась бы висеть, и всё время до неё повторно
            # не накапливалось бы — просмотр молча недосчитывался.
            if elapsed < _last_elapsed - 10:
                _last_saved_elapsed = elapsed
                if _view_counted:
                    # Пересмотр уже засчитанного ролика — считаем заново.
                    _view_counted    = False
                    _current_started = time.time()

            if not is_paused and elapsed > _last_saved_elapsed:
                delta_seconds = elapsed - _last_saved_elapsed
                # Перемотка вперёд не должна засчитываться как просмотренное
                # время: без этой отсечки один прыжок по таймлайну добавлял бы
                # в статистику десятки минут, которые никто не смотрел.
                if delta_seconds > 60:
                    delta_seconds = 0
                _last_saved_elapsed = elapsed

            _last_elapsed = elapsed

        if not _view_counted:
            real_duration = duration if duration > 0 else 9999
            if elapsed >= real_duration * VIEW_THRESHOLD or \
               (time.time() - _current_started) >= MIN_VIEW_SECONDS:
                _view_counted = True
                should_count  = True

    if delta_seconds <= 0 and not should_count:
        return

    today_str = datetime.date.today().isoformat()
    month_key = datetime.date.today().strftime("%Y-%m")
    data  = _load()
    month = data["months"].setdefault(month_key, {})
    entry = month.setdefault(key, {
        "title":                title,
        "artist":               channel,
        "cover":                thumb_url or "",
        "url":                  video_url or "",
        "count":                0,
        "last_seen":            "",
        "daily_seconds":        {},
        "avg_listen_seconds":   0,
        "total_listen_seconds": 0,
        "duration":             duration,
    })

    entry["title"]  = title
    entry["artist"] = channel
    if duration > 0:
        entry["duration"] = duration
    if thumb_url:
        entry["cover"] = thumb_url
    if video_url:
        entry["url"] = video_url

    if delta_seconds > 0:
        ds = entry.setdefault("daily_seconds", {})
        ds[today_str] = ds.get(today_str, 0) + delta_seconds
        entry["total_listen_seconds"] = entry.get("total_listen_seconds", 0) + delta_seconds
        entry["avg_listen_seconds"] = entry["total_listen_seconds"] / max(entry.get("count", 1), 1)

    if should_count:
        entry["count"] = entry.get("count", 0) + 1
        entry["last_seen"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        if entry["count"] > 0:
            entry["avg_listen_seconds"] = entry.get("total_listen_seconds", 0) / entry["count"]

    _save(data)
    if should_count:
        print(f"📊 YouTube: +1 просмотр «{title}» (всего: {entry['count']})")


def get_video_stats(video_url: str = "", title: str = "", channel: str = "") -> dict:
    """Сводка по одному видео за все месяцы — для третьей строки в Discord."""
    key = (video_url or "").strip() or f"{(title or '').strip()}||{(channel or '').strip()}"
    data = _load()
    count = 0
    secs  = 0
    for month in data["months"].values():
        e = month.get(key)
        if e:
            count += e.get("count", 0)
            secs  += e.get("total_listen_seconds", 0)
    return {"count": count, "total_listen_seconds": int(secs)}


# ══════════════════════════════════════════════════════════════════════════════
#  СВОДКИ ДЛЯ ИНТЕРФЕЙСА
# ══════════════════════════════════════════════════════════════════════════════

def current_month_preview() -> dict:
    return _load()["months"].get(datetime.date.today().strftime("%Y-%m"), {})


def _closed_periods():
    today  = datetime.date.today()
    months = sorted(_load()["months"].keys())
    closed_m, closed_y = [], []
    for mk in months:
        try:
            y, m = map(int, mk.split("-"))
            nxt = datetime.date(y + 1, 1, 1) if m == 12 else datetime.date(y, m + 1, 1)
            if today >= nxt:
                closed_m.append(mk)
        except Exception:
            pass
    for y in sorted(set(mk[:4] for mk in months)):
        try:
            if today >= datetime.date(int(y) + 1, 1, 1):
                closed_y.append(y)
        except Exception:
            pass
    return closed_m, closed_y


def has_monthly_stats() -> list:
    return _closed_periods()[0]


def has_yearly_stats() -> list:
    return _closed_periods()[1]


def summary() -> dict:
    """Кратко: сколько видео и сколько времени за текущий месяц."""
    month = current_month_preview()
    secs  = sum(v.get("total_listen_seconds", 0) for v in month.values())
    h, rem = divmod(int(secs), 3600)
    m, _s  = divmod(rem, 60)
    months, years = _closed_periods()
    return {
        "count":    len(month),
        "time_str": f"{h}ч {m}м" if h > 0 else f"{m}м",
        "months":   months,
        "years":    years,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  ОТЧЁТ
# ══════════════════════════════════════════════════════════════════════════════

# Замены текста: шаблон один на обе страницы, здесь он превращается в
# ютубовский. data-i18n снимаем там, где подставляем свой текст, — иначе
# переключатель языка вернул бы на место музыкальные формулировки.
_REBRAND = [
    ("<title>SUNO STATS</title>", "<title>YOUTUBE STATS</title>"),
    ("<h1>SUNO STATS</h1>", "<h1>YOUTUBE STATS</h1>"),
    ('<p class="hero-sub" data-i18n="hero_sub">Твоя музыкальная история</p>',
     '<p class="hero-sub">Твоя история просмотров</p>'),
    ('<div class="suno-orb orb-main" id="sunoMainOrb">SUNO</div>',
     '<div class="suno-orb orb-main" id="sunoMainOrb">YT</div>'),
    ('<div class="suno-orb orb-b">S</div>', '<div class="suno-orb orb-b">Y</div>'),
    ('<div class="suno-orb orb-c">S</div>', '<div class="suno-orb orb-c">T</div>'),
    ('<div class="suno-orb orb-d">S</div>', '<div class="suno-orb orb-d">Y</div>'),
    ('data-i18n="open_suno">↗ открыть на Suno', '>↗ открыть на YouTube'),
    # Логотип Suno на ютубовской странице неуместен. Элемент оставляем на месте
    # (на него ссылается скрипт анимации шапки), но подсовываем несуществующий
    # файл — сработает штатный onerror и спрячет картинку.
    ('<img src="2.png" alt="Suno" class="hero-logo-img"',
     '<img src="no-logo.png" alt="YouTube" class="hero-logo-img"'),
]

# Перекраска. Через отдельный блок стилей в конце <head>, а не заменой цветов
# по тексту: фирменный оранжевый захардкожен в шаблоне четыре десятка раз, в
# градиентах и тенях, и ловить каждое вхождение строкой — гарантия что-нибудь
# пропустить. Переопределение позже по документу выигрывает у исходных правил.
_STYLE_OVERRIDE = """
<style>
  /* Красная палитра YouTube поверх оранжевой суновской. */
  :root {
    --suno-orange: #ff0033;
    --suno-pink:   #ff5252;
    --suno-yellow: #ff8a80;
    --accent:      #ff0033;
    --gold:        #ff8a80;
  }
  #scroll-progress {
    background: linear-gradient(90deg, #ff8a80, #ff0033, #c4001d);
    box-shadow: 0 0 16px rgba(255,0,51,0.9), 0 0 40px rgba(255,82,82,0.4);
  }
  .suno-orb {
    background: radial-gradient(circle at 35% 35%, #ff5252, #ff0033 40%, #c4001d);
  }
  .hero h1 {
    background: linear-gradient(135deg,#ff8a80 0%,#ff0033 45%,#c4001d 100%);
    -webkit-background-clip:text; background-clip:text;
    filter: drop-shadow(0 0 50px rgba(255,0,51,0.35));
  }
  .rank-1 {
    background: linear-gradient(135deg,#ff5252,#ff0033);
    box-shadow: 0 0 16px rgba(255,0,51,0.5);
  }
  .bar-fill { background: linear-gradient(180deg,#ff5252,#ff0033); }
  .bar-wrap:hover .bar-fill { background: linear-gradient(180deg,#ff8a80,#c4001d); }
</style>
</head>"""


def generate_html():
    """Собирает страницу отчёта по YouTube тем же шаблоном, что и отчёт Suno."""
    data       = _load()
    today      = datetime.date.today()
    all_months = sorted(data["months"].keys())
    closed_months, closed_years = _closed_periods()

    cur_key  = today.strftime("%Y-%m")
    cur_data = data["months"].get(cur_key, {})

    # Мобильных данных у YouTube нет — передаём пустой словарь той же формы,
    # чтобы общие функции секций не пришлось трогать.
    empty_mobile = {"months": {}}

    html = suno_stats._HTML_TEMPLATE \
        .replace("{{MONTH_TABS}}",      suno_stats._build_month_tabs(closed_months, data, empty_mobile)) \
        .replace("{{YEAR_SECTIONS}}",   suno_stats._build_year_sections(closed_years, data, empty_mobile)) \
        .replace("{{CURRENT_SECTION}}", suno_stats._build_current_section(cur_key, cur_data, {})) \
        .replace("{{NAV}}",             suno_stats.nav_button("suno_stats.html", "Suno")) \
        .replace("{{GENERATED_AT}}",    datetime.datetime.now().strftime("%d.%m.%Y %H:%M"))

    for old, new in _REBRAND:
        html = html.replace(old, new)
    html = html.replace("</head>", _STYLE_OVERRIDE, 1)

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
