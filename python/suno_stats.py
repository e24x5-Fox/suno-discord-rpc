"""
suno_stats.py — Модуль статистики прослушивания для Suno RPC
Считает прослушивания по трекам, сохраняет месячные/годовые данные,
генерирует HTML-отчёт и сигнализирует о готовности статистики.
"""

import json
import os
import time
import datetime
import threading
import shutil

# ─── Пути к файлам ───────────────────────────────────────────────────────────
import sys as _sys
import tempfile as _tempfile

def _data_dir():
    """%APPDATA%\SunoRPC — для JSON и конфигов (persist между запусками)."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "SunoRPC")
    os.makedirs(path, exist_ok=True)
    return path

def _temp_dir():
    """Системная temp-папка — для HTML (пересоздаётся каждый раз)."""
    path = os.path.join(_tempfile.gettempdir(), "SunoRPC")
    os.makedirs(path, exist_ok=True)
    return path

# При обычном запуске (.py) — класть рядом со скриптом, как раньше
def _dev_dir():
    return os.path.dirname(os.path.abspath(__file__))

_IS_EXE = getattr(_sys, 'frozen', False)

_BASE_DIR          = _data_dir() if _IS_EXE else _dev_dir()
STATS_FILE         = os.path.join(_data_dir() if _IS_EXE else _dev_dir(), "suno_stats.json")         # ПК
STATS_MOBILE_FILE  = os.path.join(_data_dir() if _IS_EXE else _dev_dir(), "suno_stats_mobile.json")  # телефон
HTML_FILE          = os.path.join(_temp_dir() if _IS_EXE else _dev_dir(), "suno_stats.html")          # HTML-отчёт (temp)

# ─── Порог для засчёта "прослушивания" ───────────────────────────────────────
# Трек считается прослушанным если elapsed ≥ этого процента длительности
LISTEN_THRESHOLD = 0.40   # 40 % длины трека

# ─── Внутреннее состояние ─────────────────────────────────────────────────────
_lock              = threading.Lock()
_current_track_key = None   # ключ текущего трека (URL или "title||artist")
_current_started    = 0.0    # время (time.time()) когда трек начал играть
_listen_counted     = False  # уже засчитали это прослушивание?
_last_elapsed       = 0      # последний известный elapsed (для детекта replay)
_counted_at_elapsed = -1     # elapsed в момент засчёта (для повторного прослушивания)
_last_saved_elapsed = 0      # последний elapsed сохранённый в daily_seconds
_last_saved_time    = 0.0    # время последнего сохранения daily_seconds

# Коллбэк, который main-код может зарегистрировать:
# вызывается когда появляется новая готовая статистика
_on_stats_ready_cb = None


# ══════════════════════════════════════════════════════════════════════════════
#  ЗАГРУЗКА / СОХРАНЕНИЕ JSON
# ══════════════════════════════════════════════════════════════════════════════

def _empty_stats() -> dict:
    return {
        "version": 4,
        "months": {},   # "YYYY-MM": { "track_key": {...} }
        "generated_monthly": [],   # список "YYYY-MM" для которых уже сгенерирован отчёт
        "generated_yearly":  [],   # список "YYYY" для которых уже сгенерирован отчёт
    }


def _load() -> dict:
    if not os.path.exists(STATS_FILE):
        return _empty_stats()
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # миграция старой версии
        if data.get("version", 1) < 2:
            data["generated_monthly"] = []
            data["generated_yearly"]  = []
            data["version"] = 2
        if data.get("version", 2) < 3:
            # Добавляем поля daily_seconds и avg_listen_seconds в существующие треки
            for month_data in data.get("months", {}).values():
                for entry in month_data.values():
                    if "daily_seconds" not in entry:
                        entry["daily_seconds"] = {}
                    if "avg_listen_seconds" not in entry:
                        entry["avg_listen_seconds"] = 0
                    if "total_listen_seconds" not in entry:
                        entry["total_listen_seconds"] = 0
            data["version"] = 3

        # Миграция v3→v4: переключаем ключи с "title||artist" на URL трека,
        # если у записи есть поле url. Старые записи без URL остаются как есть.
        if data.get("version", 3) < 4:
            for month_data in data.get("months", {}).values():
                to_rename = {}
                for old_key, entry in list(month_data.items()):
                    url = entry.get("url", "").strip()
                    if url and old_key != url:
                        to_rename[old_key] = url
                for old_key, new_key in to_rename.items():
                    if new_key in month_data:
                        # Уже есть запись с этим URL — объединяем счётчики
                        existing = month_data[new_key]
                        old = month_data[old_key]
                        existing["count"] += old.get("count", 0)
                        existing["total_listen_seconds"] = (
                            existing.get("total_listen_seconds", 0) + old.get("total_listen_seconds", 0))
                        for day, secs in old.get("daily_seconds", {}).items():
                            ds = existing.setdefault("daily_seconds", {})
                            ds[day] = ds.get(day, 0) + secs
                        cnt = max(existing["count"], 1)
                        existing["avg_listen_seconds"] = existing["total_listen_seconds"] / cnt
                        del month_data[old_key]
                    else:
                        month_data[new_key] = month_data.pop(old_key)
            data["version"] = 4
        return data
    except Exception:
        return _empty_stats()


def _save(data: dict):
    tmp = STATS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATS_FILE)


def _load_mobile() -> dict:
    """Загружает мобильную статистику (отдельный файл)."""
    if not os.path.exists(STATS_MOBILE_FILE):
        return _empty_stats()
    try:
        with open(STATS_MOBILE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "months" not in data:
            return _empty_stats()
        return data
    except Exception:
        return _empty_stats()


def _save_mobile(data: dict):
    """Сохраняет мобильную статистику атомарно."""
    tmp = STATS_MOBILE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATS_MOBILE_FILE)


def record_mobile_play(title: str, artist: str, cover_url: str,
                       track_url: str, elapsed: int, duration: int,
                       daily_seconds: dict = None, count: int = 1):
    """
    Записывает прослушивание из мобильного расширения в отдельный файл.
    Использует max() вместо накопления чтобы повторные синхронизации
    не задваивали счётчики.
    """
    title     = (title     or "Неизвестный трек").strip()
    artist    = (artist    or "Suno AI").strip()
    track_url = (track_url or "").strip()
    key       = track_url if track_url else f"{title}||{artist}"

    month_key = datetime.date.today().strftime("%Y-%m")

    with _lock:
        data  = _load_mobile()
        month = data["months"].setdefault(month_key, {})
        entry = month.setdefault(key, {
            "title":                title,
            "artist":               artist,
            "cover":                cover_url or "",
            "url":                  track_url or "",
            "count":                0,
            "last_seen":            "",
            "daily_seconds":        {},
            "avg_listen_seconds":   0,
            "total_listen_seconds": 0,
            "duration":             duration,
        })

        if duration > 0:
            entry["duration"] = duration
        if cover_url:
            entry["cover"] = cover_url
        if track_url:
            entry["url"] = track_url

        # Используем max() — повторная синхронизация не задваивает счётчики
        entry["count"] = max(entry.get("count", 0), count)
        entry["last_seen"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        # daily_seconds: берём максимум по каждому дню
        if daily_seconds:
            ds = entry.setdefault("daily_seconds", {})
            for day, secs in daily_seconds.items():
                ds[day] = max(ds.get(day, 0), int(secs))
            entry["total_listen_seconds"] = sum(ds.values())

        cnt = max(entry["count"], 1)
        entry["avg_listen_seconds"] = entry.get("total_listen_seconds", 0) / cnt

        _save_mobile(data)
        print(f"📱 Мобильная статистика: «{title}» count={entry['count']}")


# ══════════════════════════════════════════════════════════════════════════════
#  УЧЁТ ПРОСЛУШИВАНИЙ
# ══════════════════════════════════════════════════════════════════════════════

def register_callback(cb):
    """Зарегистрировать функцию cb(), которая вызывается когда статистика готова."""
    global _on_stats_ready_cb
    _on_stats_ready_cb = cb


def on_track_update(title: str, artist: str, cover_url: str,
                    elapsed: int, duration: int, is_paused: bool,
                    track_url: str = ""):
    """
    Вызывать из handle_client каждый раз при получении данных трека.
    Засчитывает прослушивание и при необходимости генерирует отчёт.

    Поддерживает replay: если после засчёта elapsed сбросился к 0
    (или прыгнул назад значительно) — считаем новое прослушивание.
    """
    global _current_track_key, _current_started, _listen_counted
    global _last_elapsed, _counted_at_elapsed
    global _last_saved_elapsed, _last_saved_time

    title      = (title      or "Неизвестный трек").strip()
    artist     = (artist     or "Suno AI").strip()
    track_url  = (track_url  or "").strip()

    # NOTE: ключ — это URL трека (уникален для каждого трека на Suno).
    # Если URL недоступен — fallback на "title||artist", но тогда
    # одноимённые треки будут сливаться. URL приходит из расширения как trackUrl.
    key = track_url if track_url else f"{title}||{artist}"

    should_count = False
    # Дельта прослушанного времени для сохранения в daily_seconds
    delta_seconds = 0

    with _lock:
        # ── Смена трека ────────────────────────────────────────────────────
        if key != _current_track_key:
            _current_track_key  = key
            _current_started    = time.time()
            _listen_counted     = False
            _last_elapsed       = elapsed
            _counted_at_elapsed = -1
            _last_saved_elapsed = elapsed
            _last_saved_time    = time.time()

        else:
            # ── Детект replay: elapsed сбросился назад ─────────────────────
            if _listen_counted and elapsed < _last_elapsed - 10:
                _listen_counted     = False
                _current_started    = time.time()
                _counted_at_elapsed = -1
                _last_saved_elapsed = elapsed
                _last_saved_time    = time.time()

            # ── Считаем дельту прослушанного времени ───────────────────────
            # Только если трек не на паузе и elapsed движется вперёд
            if not is_paused and elapsed > _last_saved_elapsed:
                delta_seconds = elapsed - _last_saved_elapsed
                # Ограничиваем дельту чтобы не было гигантских скачков
                # (например, после переоткрытия браузера)
                if delta_seconds > 60:
                    delta_seconds = 0  # пропускаем аномальные скачки
                _last_saved_elapsed = elapsed
                _last_saved_time    = time.time()

            _last_elapsed = elapsed

        # ── Уже засчитали в этом «прогоне» — проверяем только дельту ─────
        if _listen_counted:
            pass  # дельту всё равно сохраняем ниже
        else:
            # ── Условие засчёта ────────────────────────────────────────────
            real_duration = duration if duration > 0 else 9999
            elapsed_ok    = elapsed >= real_duration * LISTEN_THRESHOLD
            time_ok       = (time.time() - _current_started) >= 30

            if elapsed_ok or time_ok:
                _listen_counted     = True
                _counted_at_elapsed = elapsed
                should_count        = True

    # Сохраняем дельту и/или засчёт (вне lock)
    if delta_seconds > 0 or should_count:
        today_str = datetime.date.today().isoformat()   # "YYYY-MM-DD"
        month_key = datetime.date.today().strftime("%Y-%m")
        data  = _load()
        month = data["months"].setdefault(month_key, {})
        entry = month.setdefault(key, {
            "title":                title,
            "artist":               artist,
            "cover":                cover_url or "",
            "url":                  track_url or "",
            "count":                0,
            "last_seen":            "",
            "daily_seconds":        {},   # "YYYY-MM-DD": секунды
            "avg_listen_seconds":   0,    # среднее время прослушивания за 1 сеанс
            "total_listen_seconds": 0,    # суммарное время прослушивания
            "duration":             duration,  # длительность трека
        })

        # Обновляем длительность трека (может уточняться)
        if duration > 0:
            entry["duration"] = duration

        # Сохраняем ссылки/обложку
        if cover_url:
            entry["cover"] = cover_url
        if track_url:
            entry["url"] = track_url

        # Добавляем дельту в daily_seconds
        if delta_seconds > 0:
            ds = entry.setdefault("daily_seconds", {})
            ds[today_str] = ds.get(today_str, 0) + delta_seconds
            entry["total_listen_seconds"] = entry.get("total_listen_seconds", 0) + delta_seconds
            # Пересчитываем среднее: total / count (count ≥ 1)
            cnt = max(entry.get("count", 1), 1)
            entry["avg_listen_seconds"] = entry["total_listen_seconds"] / cnt

        # Засчитываем прослушивание
        if should_count:
            entry["count"] = entry.get("count", 0) + 1
            entry["last_seen"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            # Пересчитываем среднее после обновления count
            cnt = entry["count"]
            if cnt > 0:
                entry["avg_listen_seconds"] = entry.get("total_listen_seconds", 0) / cnt

        _save(data)
        if should_count:
            print(f"📊 Статистика: +1 для «{title}» (всего: {entry['count']})")

    if should_count:
        _check_and_generate()


# ══════════════════════════════════════════════════════════════════════════════
#  ПРОВЕРКА ДЕДЛАЙНОВ
# ══════════════════════════════════════════════════════════════════════════════

def _check_and_generate():
    """Проверяет, завершился ли месяц или год, и генерирует отчёт."""
    today = datetime.date.today()
    data  = _load()

    # ── Поиск завершённых месяцев ─────────────────────────────────────────────
    newly_ready_months = []
    for month_key in list(data["months"].keys()):
        if month_key in data["generated_monthly"]:
            continue
        # Месяц завершён если сейчас уже другой месяц
        try:
            y, m = map(int, month_key.split("-"))
            month_end = datetime.date(y, m, 1)
            # первый день следующего месяца
            if m == 12:
                next_month = datetime.date(y + 1, 1, 1)
            else:
                next_month = datetime.date(y, m + 1, 1)
            if today >= next_month:
                newly_ready_months.append(month_key)
        except Exception:
            pass

    # ── Поиск завершённых годов ───────────────────────────────────────────────
    newly_ready_years = []
    all_years = set()
    for mk in data["months"].keys():
        try:
            all_years.add(mk.split("-")[0])
        except Exception:
            pass
    for year_str in all_years:
        if year_str in data["generated_yearly"]:
            continue
        try:
            y = int(year_str)
            if today >= datetime.date(y + 1, 1, 1):
                newly_ready_years.append(year_str)
        except Exception:
            pass

    if not newly_ready_months and not newly_ready_years:
        return

    # Генерируем HTML
    generate_html()

    # Помечаем как обработанные
    data = _load()
    for mk in newly_ready_months:
        if mk not in data["generated_monthly"]:
            data["generated_monthly"].append(mk)
    for yk in newly_ready_years:
        if yk not in data["generated_yearly"]:
            data["generated_yearly"].append(yk)
            # После годовой статистики — архивируем и чистим старый год
            _archive_year(data, yk)
    _save(data)

    # Уведомляем UI
    if _on_stats_ready_cb:
        try:
            _on_stats_ready_cb(
                monthly=newly_ready_months,
                yearly=newly_ready_years,
            )
        except Exception:
            pass


def _archive_year(data: dict, year_str: str):
    """Сохраняет архив года и удаляет его данные из основного файла."""
    archive_path = os.path.join(_data_dir() if _IS_EXE else _dev_dir(), f"suno_stats_{year_str}_archive.json")
    year_data = {k: v for k, v in data["months"].items() if k.startswith(year_str + "-")}
    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump({"year": year_str, "months": year_data}, f, ensure_ascii=False, indent=2)
    # Удаляем месяцы этого года из активных данных
    for mk in list(year_data.keys()):
        data["months"].pop(mk, None)
    # Также убираем из generated_monthly чтобы не копились
    data["generated_monthly"] = [m for m in data["generated_monthly"]
                                  if not m.startswith(year_str + "-")]


def get_track_stats(track_url: str = "", title: str = "", artist: str = "") -> dict:
    """
    Возвращает накопленную статистику по треку из всех месяцев.
    Ключ поиска: track_url если есть, иначе "title||artist".
    Возвращает: {"count": int, "total_listen_seconds": int}
    """
    key = track_url.strip() if track_url and track_url.strip() else f"{title}||{artist}"
    data = _load()
    total_count = 0
    total_secs  = 0
    for month_data in data.get("months", {}).values():
        entry = month_data.get(key)
        if entry:
            total_count += entry.get("count", 0)
            total_secs  += entry.get("total_listen_seconds", 0)
    return {"count": total_count, "total_listen_seconds": int(total_secs)}


# ══════════════════════════════════════════════════════════════════════════════
#  ПУБЛИЧНЫЕ ХЕЛПЕРЫ ДЛЯ UI
# ══════════════════════════════════════════════════════════════════════════════

def has_monthly_stats() -> list:
    """Возвращает список 'YYYY-MM' завершённых месяцев с данными."""
    data  = _load()
    today = datetime.date.today()
    result = []
    for mk in data["months"]:
        try:
            y, m = map(int, mk.split("-"))
            if m == 12:
                next_month = datetime.date(y + 1, 1, 1)
            else:
                next_month = datetime.date(y, m + 1, 1)
            if today >= next_month:
                result.append(mk)
        except Exception:
            pass
    return sorted(result)


def has_yearly_stats() -> list:
    """Возвращает список 'YYYY' завершённых лет с данными."""
    data   = _load()
    today  = datetime.date.today()
    years  = set()
    for mk in data["months"]:
        try:
            years.add(mk.split("-")[0])
        except Exception:
            pass
    result = []
    for y in years:
        try:
            if today >= datetime.date(int(y) + 1, 1, 1):
                result.append(y)
        except Exception:
            pass
    return sorted(result)


def current_month_preview() -> dict:
    """Возвращает данные текущего (незавершённого) месяца для предпросмотра."""
    data      = _load()
    month_key = datetime.date.today().strftime("%Y-%m")
    return data["months"].get(month_key, {})


# ══════════════════════════════════════════════════════════════════════════════
#  ГЕНЕРАЦИЯ HTML
# ══════════════════════════════════════════════════════════════════════════════

_MONTH_RU = {
    "01": "Январь", "02": "Февраль", "03": "Март",
    "04": "Апрель", "05": "Май",     "06": "Июнь",
    "07": "Июль",   "08": "Август",  "09": "Сентябрь",
    "10": "Октябрь","11": "Ноябрь",  "12": "Декабрь",
}


def _top_tracks(month_data: dict, n: int) -> list:
    tracks = list(month_data.values())
    tracks.sort(key=lambda t: t["count"], reverse=True)
    return tracks[:n]


def generate_html():
    """Генерирует полный HTML-отчёт из текущих данных stats.json + mobile."""
    data        = _load()
    mobile_data = _load_mobile()
    today       = datetime.date.today()
    all_months  = sorted(data["months"].keys())

    # Все месяцы из обоих источников
    all_months_set = set(all_months) | set(mobile_data["months"].keys())
    all_months     = sorted(all_months_set)

    # Завершённые месяцы (не текущий)
    closed_months = []
    for mk in all_months:
        try:
            y, m = map(int, mk.split("-"))
            if m == 12:
                nm = datetime.date(y + 1, 1, 1)
            else:
                nm = datetime.date(y, m + 1, 1)
            if today >= nm:
                closed_months.append(mk)
        except Exception:
            pass

    # Завершённые годы
    closed_years = []
    years = sorted(set(mk[:4] for mk in all_months))
    for y in years:
        try:
            if today >= datetime.date(int(y) + 1, 1, 1):
                closed_years.append(y)
        except Exception:
            pass

    # Текущий месяц
    cur_month_key      = today.strftime("%Y-%m")
    cur_month_data     = data["months"].get(cur_month_key, {})
    cur_month_mobile   = mobile_data["months"].get(cur_month_key, {})

    # ── Строим секции HTML ────────────────────────────────────────────────────
    month_tabs_html    = _build_month_tabs(closed_months, data, mobile_data)
    year_sections_html = _build_year_sections(closed_years, data, mobile_data)
    current_html       = _build_current_section(cur_month_key, cur_month_data, cur_month_mobile)

    html = _HTML_TEMPLATE.replace("{{MONTH_TABS}}", month_tabs_html) \
                         .replace("{{YEAR_SECTIONS}}", year_sections_html) \
                         .replace("{{CURRENT_SECTION}}", current_html) \
                         .replace("{{GENERATED_AT}}", datetime.datetime.now().strftime("%d.%m.%Y %H:%M"))

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)



def _fmt_duration(seconds: float) -> str:
    """Форматирует секунды в строку 'M:SS'."""
    s = int(seconds)
    if s <= 0:
        return "—"
    m, sec = divmod(s, 60)
    if m > 0:
        return f"{m}:{sec:02d}"
    return f"0:{sec:02d}"


def _fmt_total_time(seconds: float) -> str:
    """Возвращает span с data-seconds — JS отформатирует на нужном языке."""
    s = int(seconds)
    if s <= 0:
        return "—"
    h, rem = divmod(s, 3600)
    m = rem // 60
    fallback = f"{h} ч {m} мин" if h > 0 else f"{m} мин"
    return f'<span class="i18n-time" data-seconds="{s}">{fallback}</span>'


def _track_daily_chart(track: dict) -> str:
    """
    Строит SVG-график для трека:
    — столбики: секунды прослушивания по дням
    — пунктирная линия: средняя длительность прослушивания за 1 раз
    — пунктирная линия: полная длительность трека
    """
    daily: dict = track.get("daily_seconds") or {}
    if not daily:
        return ""

    # Сортируем по дате, берём последние 30 дней
    sorted_days = sorted(daily.items())[-30:]
    if not sorted_days:
        return ""

    avg_listen = track.get("avg_listen_seconds", 0)
    track_dur  = track.get("duration", 0)

    max_val = max(v for _, v in sorted_days) or 1
    # Масштаб: учитываем и avg_listen и track_dur для корректного отображения линий
    scale_max = max(max_val, avg_listen * 1.1, track_dur * 0.5 if track_dur else 0) or 1

    W, H = 280, 80
    BAR_W = max(4, int(W / (len(sorted_days) + 1)))
    GAP   = 2
    BOTTOM = 16  # высота зоны подписей дат

    bars_svg = ""
    for i, (day, secs) in enumerate(sorted_days):
        bar_h = int((secs / scale_max) * (H - BOTTOM - 4))
        bar_h = max(bar_h, 2)
        x = i * (BAR_W + GAP)
        y = H - BOTTOM - bar_h
        # подпись дня (только число) если помещается
        day_label = day.split("-")[2].lstrip("0") or "0"
        bars_svg += (
            f'<rect x="{x}" y="{y}" width="{BAR_W}" height="{bar_h}" '
            f'rx="2" fill="url(#barGrad)" opacity="0.85">'
            f'<title>{day}: {_fmt_duration(secs)}</title></rect>'
        )
        if BAR_W >= 8:
            bars_svg += (
                f'<text x="{x + BAR_W//2}" y="{H - 2}" '
                f'text-anchor="middle" font-size="7" fill="#52525b">{day_label}</text>'
            )

    # Линия среднего прослушивания
    avg_lines = ""
    if avg_listen > 0:
        y_avg = H - BOTTOM - int((avg_listen / scale_max) * (H - BOTTOM - 4))
        total_bar_w = len(sorted_days) * (BAR_W + GAP)
        avg_lines += (
            f'<line x1="0" y1="{y_avg}" x2="{total_bar_w}" y2="{y_avg}" '
            f'stroke="#a78bfa" stroke-width="1.5" stroke-dasharray="4,3" opacity="0.9"/>'
            f'<text x="{total_bar_w + 2}" y="{y_avg + 4}" '
            f'font-size="7" fill="#a78bfa">обычно {_fmt_duration(avg_listen)}</text>'
        )

    # Линия полной длительности трека
    dur_line = ""
    if track_dur > 0 and track_dur <= scale_max * 1.2:
        y_dur = H - BOTTOM - int((track_dur / scale_max) * (H - BOTTOM - 4))
        total_bar_w = len(sorted_days) * (BAR_W + GAP)
        dur_line = (
            f'<line x1="0" y1="{y_dur}" x2="{total_bar_w}" y2="{y_dur}" '
            f'stroke="#22c55e" stroke-width="1" stroke-dasharray="3,4" opacity="0.7"/>'
            f'<text x="{total_bar_w + 2}" y="{y_dur + 4}" '
            f'font-size="7" fill="#22c55e">{_fmt_duration(track_dur)}</text>'
        )

    chart_id = f"chart-{abs(hash(track.get('title','') + track.get('artist',''))) % 99999}"

    return f"""
    <div class="track-chart" id="{chart_id}">
      <svg viewBox="0 0 {W + 50} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:{H}px">
        <defs>
          <linearGradient id="barGrad-{chart_id}" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#c084fc"/>
            <stop offset="100%" stop-color="#7c3aed" stop-opacity="0.5"/>
          </linearGradient>
        </defs>
        {bars_svg.replace('fill="url(#barGrad)"', f'fill="url(#barGrad-{chart_id})"')}
        {avg_lines}
        {dur_line}
      </svg>
      <div class="chart-legend">
        <span class="legend-avg" data-i18n="avg_listen" data-val="{_fmt_duration(avg_listen)}">обычно слушаю: {_fmt_duration(avg_listen)}</span>
        {"<span class='legend-dur' data-i18n='track_length' data-val='" + _fmt_duration(track_dur) + "'>длина трека: " + _fmt_duration(track_dur) + "</span>" if track_dur > 0 else ""}
      </div>
    </div>"""


def _track_card(track: dict, rank: int, badge_color: str = "", mobile_track: dict = None) -> str:
    cover = track.get("cover") or ""
    url   = track.get("url")   or ""
    cover_html = (f'<img class="cover" src="{cover}" alt="обложка" onerror="this.style.display=\'none\'">'
                  if cover else '<div class="cover-placeholder">♪</div>')
    count = track["count"]
    plays = _pluralize(count, "раз", "раза", "раз")

    avg_sec   = track.get("avg_listen_seconds", 0)
    total_sec = track.get("total_listen_seconds", 0)
    avg_fmt   = _fmt_duration(avg_sec)
    total_fmt = _fmt_duration(total_sec)

    link_attr = f' href="{_esc(url)}" target="_blank" title="Open on Suno"' if url else ""
    tag_open  = f'<a class="track-card"{link_attr}>' if url else '<div class="track-card">'
    tag_close = '</a>' if url else '</div>'
    link_hint = '<span class="track-link-hint" data-i18n="open_suno">↗ открыть на Suno</span>' if url else ''

    rank_class = {1: "rank rank-1", 2: "rank rank-2", 3: "rank rank-3"}.get(rank, "rank rank-n")
    chart_html = _track_daily_chart(track)

    time_html = ""
    if avg_sec > 0 or total_sec > 0:
        avg_part   = f'<span data-i18n="avg_listen" data-val="{avg_fmt}">обычно слушаю {avg_fmt}</span>' if avg_sec > 0 else ""
        sep        = "&nbsp;&nbsp;·&nbsp;&nbsp;" if avg_sec > 0 and total_sec > 0 else ""
        total_part = f'<span data-i18n="total_listen_card" data-val="{total_fmt}">всего {total_fmt}</span>' if total_sec > 0 else ""
        time_html  = f"<div class='track-time-stats'>{avg_part}{sep}{total_part}</div>"

    plays_span = f'<span data-i18n="plays" data-count="{count}">▶ {count} раз</span>'

    # Мобильный счётчик
    mobile_html = ""
    if mobile_track and mobile_track.get("count", 0) > 0:
        mc = mobile_track["count"]
        mobile_html = f'<div class="mobile-plays"><span class="mobile-icon">📱</span> {mc} раз с телефона</div>'

    return f"""
    {tag_open}
      <div class="{rank_class}">#{rank}</div>
      <div class="card-top">
        {cover_html}
        <div class="track-info">
          <div class="track-title">{_esc(track['title'])}</div>
          <div class="track-artist">{_esc(track['artist'])}</div>
          <div class="track-count">{plays_span}</div>
          {mobile_html}
          {time_html}
          {link_hint}
        </div>
      </div>
      {chart_html}
    {tag_close}"""


def _build_month_tabs(months: list, data: dict, mobile_data: dict = None) -> str:
    if not months:
        return '<p class="empty-note" data-i18n="no_months">Пока нет завершённых месяцев — возвращайся позже! 🎵</p>'

    mobile_data = mobile_data or {}
    tabs_nav  = ""
    tabs_body = ""
    for i, mk in enumerate(reversed(months)):
        y, m = mk.split("-")
        label        = f"{_MONTH_RU.get(m, m)} {y}"
        active       = "active" if i == 0 else ""
        month_d      = data["months"].get(mk, {})
        month_m      = mobile_data.get("months", {}).get(mk, {})
        top3_pc      = _top_tracks(month_d, 3)
        top3_mobile  = _top_tracks(month_m, 3)
        total_pc     = sum(t["count"] for t in month_d.values())
        total_mobile = sum(t["count"] for t in month_m.values())
        total_time   = sum(t.get("total_listen_seconds", 0) for t in month_d.values())

        pc_cards  = "".join(_track_card(t, j+1) for j, t in enumerate(top3_pc))
        mob_cards = "".join(_track_card(t, j+1) for j, t in enumerate(top3_mobile))

        if not pc_cards:
            pc_cards = '<p class="empty-note" data-i18n="no_tracks_month">В этом месяце треков не было 🎶</p>'
        if not mob_cards:
            mob_cards = '<p class="empty-note">Пока пусто</p>'

        time_html = (f'<div class="month-summary"><span class="summary-label" data-i18n="time_in_music">⏱ Времени в музыке</span>'
                     f'<span class="summary-value">{_fmt_total_time(total_time)}</span></div>') if total_time > 0 else ""

        tabs_nav  += f'<button class="tab-btn {active}" onclick="switchTab(\'{mk}\')" data-month-label="{label}">{label}</button>\n'
        tabs_body += f"""
        <div class="tab-content {active}" id="tab-{mk}">
          {time_html}
          <div class="two-col-stats">
            <div class="col-pc">
              <div class="col-header"><span class="col-icon">🖥</span> Компьютер
                <span class="col-count">{total_pc}</span>
              </div>
              <div class="cards-col">{pc_cards}</div>
            </div>
            <div class="col-mobile">
              <div class="col-header"><span class="col-icon">📱</span> Телефон
                <span class="col-count">{total_mobile}</span>
              </div>
              <div class="cards-col">{mob_cards}</div>
            </div>
          </div>
          {_build_mini_chart(month_d, mk)}
        </div>"""

    return f'<div class="tabs-nav">{tabs_nav}</div><div class="tabs-body">{tabs_body}</div>'


def _build_year_sections(years: list, data: dict, mobile_data: dict = None) -> str:
    if not years:
        return '<p class="empty-note" data-i18n="no_years">Годовая статистика появится после 31 декабря ✨</p>'

    mobile_data = mobile_data or {}
    html = ""
    for yr in reversed(years):
        year_months        = {mk: data["months"][mk] for mk in data["months"] if mk.startswith(yr + "-")}
        year_months_mobile = {mk: mobile_data["months"][mk] for mk in mobile_data.get("months", {}) if mk.startswith(yr + "-")}

        combined: dict = {}
        combined_mobile: dict = {}

        for md in year_months.values():
            for key, tr in md.items():
                if key in combined:
                    combined[key]["count"] += tr["count"]
                    combined[key]["total_listen_seconds"] = (
                        combined[key].get("total_listen_seconds", 0) + tr.get("total_listen_seconds", 0))
                    for day, secs in tr.get("daily_seconds", {}).items():
                        ds = combined[key].setdefault("daily_seconds", {})
                        ds[day] = ds.get(day, 0) + secs
                    if tr.get("cover"):
                        combined[key]["cover"] = tr["cover"]
                else:
                    combined[key] = dict(tr)
                    combined[key]["daily_seconds"] = dict(tr.get("daily_seconds", {}))

        for md in year_months_mobile.values():
            for key, tr in md.items():
                if key in combined_mobile:
                    combined_mobile[key]["count"] += tr["count"]
                else:
                    combined_mobile[key] = dict(tr)

        for entry in combined.values():
            cnt = entry.get("count", 1)
            if cnt > 0:
                entry["avg_listen_seconds"] = entry.get("total_listen_seconds", 0) / cnt

        top10_pc     = _top_tracks(combined, 10)
        top10_mobile = _top_tracks(combined_mobile, 10)
        total        = sum(t["count"] for t in combined.values())
        total_mobile = sum(t["count"] for t in combined_mobile.values())
        total_time   = sum(t.get("total_listen_seconds", 0) for t in combined.values())

        pc_cards  = "".join(_track_card(t, j+1) for j, t in enumerate(top10_pc))
        mob_cards = "".join(_track_card(t, j+1) for j, t in enumerate(top10_mobile))
        if not pc_cards:
            pc_cards = '<p class="empty-note">Пока пусто</p>'
        if not mob_cards:
            mob_cards = '<p class="empty-note">Пока пусто</p>'

        chart_html = _build_year_chart(year_months, yr)
        time_html  = (f'<div class="month-summary"><span class="summary-label" data-i18n="time_in_music_year">⏱ Времени в музыке за год</span>'
                      f'<span class="summary-value">{_fmt_total_time(total_time)}</span></div>') if total_time > 0 else ""

        html += f"""
        <div class="year-section">
          <h2 class="year-title">🗓 {yr}</h2>
          {time_html}
          {chart_html}
          <div class="two-col-stats">
            <div class="col-pc">
              <div class="col-header"><span class="col-icon">🖥</span> Компьютер
                <span class="col-count">{total}</span>
              </div>
              <div class="cards-col">{pc_cards}</div>
            </div>
            <div class="col-mobile">
              <div class="col-header"><span class="col-icon">📱</span> Телефон
                <span class="col-count">{total_mobile}</span>
              </div>
              <div class="cards-col">{mob_cards}</div>
            </div>
          </div>
        </div>"""
    return html


def _build_current_section(mk: str, month_data: dict, mobile_data: dict = None) -> str:
    mobile_data = mobile_data or {}
    if not month_data and not mobile_data:
        return '<p class="empty-note" data-i18n="no_current">Начни слушать музыку — статистика появится здесь! 🎵</p>'
    y, m = mk.split("-")
    label        = f"{_MONTH_RU.get(m, m)} {y}"
    top3_pc      = _top_tracks(month_data,  3)
    top3_mobile  = _top_tracks(mobile_data, 3)
    total_pc     = sum(t["count"] for t in month_data.values())
    total_mobile = sum(t["count"] for t in mobile_data.values())
    total_time   = sum(t.get("total_listen_seconds", 0) for t in month_data.values())

    pc_cards = "".join(_track_card(t, j+1) for j, t in enumerate(top3_pc))
    mob_cards = "".join(_track_card(t, j+1) for j, t in enumerate(top3_mobile))

    time_html = (f'<div class="month-summary"><span class="summary-label" '
                 f'data-i18n="time_in_music">⏱ Времени в музыке</span>'
                 f'<span class="summary-value">{_fmt_total_time(total_time)}</span></div>') if total_time > 0 else ""

    return f"""
    <div class="current-month">
      <div class="current-header">
        <div class="pulse-dot"></div>
        <span data-i18n="now_playing" data-month="{label}" data-month-key="{mk}">{label} — идёт прямо сейчас</span>
      </div>
      {time_html}
      <div class="two-col-stats">
        <div class="col-pc">
          <div class="col-header"><span class="col-icon">🖥</span> Компьютер
            <span class="col-count">{total_pc}</span>
          </div>
          <div class="cards-col">
            {pc_cards if pc_cards else '<p class="empty-note" data-i18n="empty_now">Пока пусто</p>'}
          </div>
        </div>
        <div class="col-mobile">
          <div class="col-header"><span class="col-icon">📱</span> Телефон
            <span class="col-count">{total_mobile}</span>
          </div>
          <div class="cards-col">
            {mob_cards if mob_cards else '<p class="empty-note">Пока пусто</p>'}
          </div>
        </div>
      </div>
    </div>"""


def _build_mini_chart(month_data: dict, mk: str) -> str:
    """SVG-столбчатая диаграмма топ-8 треков месяца."""
    top8  = _top_tracks(month_data, 8)
    if not top8:
        return ""
    mx = top8[0]["count"] or 1
    bars = ""
    for i, t in enumerate(top8):
        pct   = t["count"] / mx * 100
        label = t["title"][:18] + ("…" if len(t["title"]) > 18 else "")
        bars  += f"""
          <div class="bar-wrap" title="{_esc(t['title'])} — {t['count']} раз">
            <div class="bar-fill" style="height:{pct:.0f}%"></div>
            <div class="bar-label">{_esc(label)}</div>
          </div>"""
    return f'<div class="bar-chart">{bars}</div>'


def _build_year_chart(year_months: dict, yr: str) -> str:
    """Линейный мини-чарт: прослушивания по месяцам."""
    month_counts = []
    for m in range(1, 13):
        mk  = f"{yr}-{m:02d}"
        cnt = sum(t["count"] for t in year_months.get(mk, {}).values())
        month_counts.append((_MONTH_RU[f"{m:02d}"][:3], cnt))

    mx = max(c for _, c in month_counts) or 1
    bars = ""
    for name, cnt in month_counts:
        pct  = cnt / mx * 100
        bars += f"""
          <div class="bar-wrap" title="{name}: {cnt} прослушиваний">
            <div class="bar-fill" style="height:{pct:.0f}%"></div>
            <div class="bar-label">{name}</div>
          </div>"""
    return f'<h3 class="section-subtitle" data-i18n="plays_by_month">📊 Прослушивания по месяцам</h3><div class="bar-chart year-chart">{bars}</div>'


# ─── Утилиты ──────────────────────────────────────────────────────────────────
def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _pluralize(n: int, form1: str, form2: str, form5: str) -> str:
    """Склонение по-русски: 1-раз, 2-раза, 5-раз."""
    n = abs(n) % 100
    if 11 <= n <= 19:
        return form5
    n = n % 10
    if n == 1:
        return form1
    if 2 <= n <= 4:
        return form2
    return form5


# ══════════════════════════════════════════════════════════════════════════════
#  HTML ШАБЛОН
# ══════════════════════════════════════════════════════════════════════════════

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SUNO STATS</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=Bebas+Neue&display=swap" rel="stylesheet">
<style>
  :root {
    --suno-orange: #ff6b00;
    --suno-pink:   #ff2d78;
    --suno-yellow: #ffb800;
    --bg:    #080810;
    --bg2:   #0d0d18;
    --bg3:   #161625;
    --bg4:   #1e1e30;
    --text:  #f0ede8;
    --text2: #9993a8;
    --text3: #4a4560;
    --accent:  #ff6b00;
    --accent2: #ff9b4e;
    --gold:    #ffb800;
    --green:   #00e87a;
    --purple:  #a78bfa;
    --glass:   rgba(255,255,255,0.03);
    --glass-border: rgba(255,255,255,0.06);
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Space Grotesk', sans-serif;
    overflow-x: hidden;
    min-height: 100vh;
  }
  /* ── Noise texture overlay ── */
  body::before {
    content: '';
    position: fixed; inset: 0; z-index: 0;
    pointer-events: none;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.04'/%3E%3C/svg%3E");
    opacity: 0.4;
  }
  .reveal {
    opacity: 0;
    transform: translateY(48px);
    transition: opacity 0.7s cubic-bezier(0.22,1,0.36,1), transform 0.7s cubic-bezier(0.22,1,0.36,1);
  }
  .reveal.visible { opacity: 1; transform: translateY(0); }
  .reveal-left {
    opacity: 0; transform: translateX(-60px);
    transition: opacity 0.7s cubic-bezier(0.22,1,0.36,1), transform 0.7s cubic-bezier(0.22,1,0.36,1);
  }
  .reveal-left.visible { opacity:1; transform:translateX(0); }
  .reveal-right {
    opacity: 0; transform: translateX(60px);
    transition: opacity 0.7s cubic-bezier(0.22,1,0.36,1), transform 0.7s cubic-bezier(0.22,1,0.36,1);
  }
  .reveal-right.visible { opacity:1; transform:translateX(0); }
  #scroll-progress {
    position: fixed; top: 0; left: 0; height: 2px; z-index: 999;
    background: linear-gradient(90deg, #ffb800, #ff6b00, #ff2d78);
    width: 0%; transition: width 0.1s linear;
    box-shadow: 0 0 16px rgba(255,107,0,0.9), 0 0 40px rgba(255,45,120,0.4);
  }
  #suno-canvas {
    position: fixed; top: 0; left: 0;
    width: 100%; height: 100%;
    pointer-events: none; z-index: 0;
  }
  .suno-fly-container {
    position: fixed; top: 0; left: 0;
    width: 100%; height: 100%;
    z-index: 100; pointer-events: none; overflow: hidden;
  }
  .suno-orb {
    position: absolute; border-radius: 50%;
    background: radial-gradient(circle at 35% 35%, #ffb800, #ff6b00 40%, #ff2d78);
    display: flex; align-items: center; justify-content: center;
    font-family: 'Bebas Neue', sans-serif;
    color: #fff; letter-spacing: 3px;
    box-shadow: 0 0 60px rgba(255,107,0,0.6), 0 0 120px rgba(255,45,120,0.3);
    will-change: transform, opacity;
  }
  .suno-orb.orb-main { width:160px;height:160px;font-size:44px; animation: fly-main 1.6s cubic-bezier(0.22,1,0.36,1) forwards; }
  .suno-orb.orb-b    { width:80px;height:80px;font-size:22px;opacity:0.7; animation: fly-b 1.8s cubic-bezier(0.22,1,0.36,1) 0.1s forwards; }
  .suno-orb.orb-c    { width:50px;height:50px;font-size:14px;opacity:0.5; animation: fly-c 1.5s cubic-bezier(0.22,1,0.36,1) 0.2s forwards; }
  .suno-orb.orb-d    { width:60px;height:60px;font-size:16px;opacity:0.6; animation: fly-d 2.0s cubic-bezier(0.22,1,0.36,1) 0.05s forwards; }
  @keyframes fly-main {
    0%   { left:-200px;top:-200px;transform:rotate(-30deg) scale(0.5);opacity:0; }
    30%  { opacity:1; }
    80%  { transform:rotate(5deg) scale(1.08); }
    100% { left:calc(50% - 80px);top:calc(50% - 80px);transform:rotate(0) scale(1);opacity:1; }
  }
  @keyframes fly-b {
    0%   { right:-120px;top:-120px;transform:rotate(40deg) scale(0.4);opacity:0; }
    30%  { opacity:0.7; }
    100% { right:calc(30% - 40px);top:calc(40% - 40px);transform:rotate(0) scale(1);opacity:0; }
  }
  @keyframes fly-c {
    0%   { right:-80px;bottom:-80px;transform:rotate(-20deg) scale(0.3);opacity:0; }
    30%  { opacity:0.5; }
    100% { right:calc(25% - 25px);bottom:calc(35% - 25px);transform:rotate(0) scale(1);opacity:0; }
  }
  @keyframes fly-d {
    0%   { left:-100px;bottom:-100px;transform:rotate(60deg) scale(0.4);opacity:0; }
    30%  { opacity:0.6; }
    100% { left:calc(28% - 30px);bottom:calc(32% - 30px);transform:rotate(0) scale(1);opacity:0; }
  }
  .suno-orb.orb-main.settled { animation: orb-settle 0.8s cubic-bezier(0.34,1.56,0.64,1) forwards; }
  @keyframes orb-settle {
    0%   { left:calc(50% - 80px);top:calc(50% - 80px);transform:scale(1); }
    50%  { transform:scale(1.15);box-shadow:0 0 120px rgba(255,107,0,0.9),0 0 200px rgba(255,45,120,0.5); }
    100% { left:calc(50% - 80px);top:calc(50% - 80px);transform:scale(1);opacity:0; }
  }
  /* ── Glow orbs ── */
  .glow-orb {
    position: fixed; border-radius: 50%; filter: blur(100px);
    opacity: 0.10; pointer-events: none; z-index: 0;
    animation: float-orb 10s ease-in-out infinite;
  }
  .glow-1 { width:600px;height:600px;background:var(--suno-orange);top:-180px;right:-180px;animation-delay:0s; }
  .glow-2 { width:500px;height:500px;background:var(--suno-pink);bottom:10%;left:-200px;animation-delay:-4s; }
  .glow-3 { width:350px;height:350px;background:var(--suno-yellow);top:50%;right:5%;opacity:0.06;animation-delay:-7s; }
  @keyframes float-orb {
    0%,100% { transform:translateY(0) scale(1); }
    50%      { transform:translateY(-40px) scale(1.08); }
  }

  /* ── Hero ── */
  .hero {
    position: relative; z-index: 1;
    min-height: 100vh;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    text-align: center; padding: 40px 20px;
    opacity: 0;
    animation: hero-reveal 1s ease 2.2s forwards;
  }
  @keyframes hero-reveal {
    from { opacity:0; transform:translateY(30px); }
    to   { opacity:1; transform:translateY(0); }
  }
  .hero-logo-img {
    width:130px; height:130px; border-radius:50%;
    margin-bottom:36px;
    box-shadow:0 0 0 1px rgba(255,107,0,0.2),
               0 0 60px rgba(255,107,0,0.5),
               0 0 120px rgba(255,45,120,0.2);
    animation: logo-pulse 3s ease-in-out infinite;
  }
  @keyframes logo-pulse {
    0%,100% { box-shadow:0 0 0 1px rgba(255,107,0,0.2),0 0 60px rgba(255,107,0,0.5),0 0 120px rgba(255,45,120,0.2); }
    50%      { box-shadow:0 0 0 1px rgba(255,107,0,0.4),0 0 90px rgba(255,107,0,0.8),0 0 180px rgba(255,45,120,0.4); }
  }
  .hero-img-wrap { margin-bottom: 28px; }
  .hero-text-group {
    display: flex; flex-direction: column;
    align-items: center; gap: 14px;
    will-change: transform;
  }
  .hero h1 {
    font-family: 'Bebas Neue', sans-serif;
    font-size: clamp(4.5rem,13vw,10rem);
    letter-spacing: 0.14em; line-height: 0.88;
    background: linear-gradient(135deg,#ffb800 0%,#ff6b00 45%,#ff2d78 100%);
    -webkit-background-clip:text; -webkit-text-fill-color:transparent;
    background-clip:text;
    filter: drop-shadow(0 0 50px rgba(255,107,0,0.35));
  }
  .hero-sub {
    font-size: 0.8rem; color: var(--text3);
    letter-spacing: 0.4em; text-transform: uppercase;
    font-weight: 500; margin-bottom: 8px;
    border: 1px solid var(--glass-border);
    padding: 6px 20px; border-radius: 999px;
    background: var(--glass);
    backdrop-filter: blur(10px);
  }
  .hero-generated {
    font-size: 0.72rem; color: var(--text3);
    letter-spacing: 0.1em;
    margin-top: 4px;
  }
  .hero-scroll {
    position:absolute; bottom:40px; left:50%; transform:translateX(-50%);
    display:flex; flex-direction:column; align-items:center; gap:8px;
    color:var(--text3); font-size:0.65rem; letter-spacing:0.25em; text-transform:uppercase;
    animation: scroll-bounce 2.5s ease-in-out infinite;
  }
  .scroll-line { width:1px;height:48px;background:linear-gradient(to bottom,var(--suno-orange),transparent); }
  @keyframes scroll-bounce {
    0%,100% { transform:translateX(-50%) translateY(0);opacity:0.5; }
    50%      { transform:translateX(-50%) translateY(10px);opacity:1; }
  }

  /* ── Layout ── */
  .container {
    position:relative; z-index:1;
    max-width:940px; margin:0 auto; padding:0 24px 100px;
    opacity:0; animation: hero-reveal 1s ease 2.8s forwards;
  }
  .section { margin-bottom:64px; }
  .section-title {
    font-family:'Bebas Neue',sans-serif;
    font-size:0.75rem; letter-spacing:0.35em;
    color:var(--text3); text-transform:uppercase;
    margin-bottom:28px;
    display:flex; align-items:center; gap:16px;
  }
  .section-title::after {
    content:''; flex:1; height:1px;
    background:linear-gradient(to right,var(--bg4),transparent);
  }
  .section-subtitle {
    font-size:0.75rem; color:var(--text3); text-transform:uppercase;
    letter-spacing:0.25em;
    margin:28px 0 16px;
    display:flex; align-items:center; gap:10px;
  }
  .section-subtitle::after {
    content:''; flex:1; height:1px;
    background:linear-gradient(to right,var(--bg4),transparent);
  }

  /* ── Tabs ── */
  .tabs-nav { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:24px; }
  .tab-btn {
    background: var(--glass);
    border: 1px solid var(--glass-border);
    color:var(--text3); padding:8px 20px; border-radius:999px;
    cursor:pointer; font-size:0.75rem; font-family:'Space Grotesk',sans-serif;
    letter-spacing:0.08em; font-weight:500;
    transition:all 0.25s; backdrop-filter:blur(8px);
  }
  .tab-btn:hover { border-color:rgba(255,107,0,0.3); color:var(--text2); }
  .tab-btn.active {
    background:linear-gradient(135deg,rgba(255,107,0,0.2),rgba(255,45,120,0.15));
    border-color:rgba(255,107,0,0.5); color:#fff; font-weight:600;
    box-shadow:0 0 24px rgba(255,107,0,0.25), inset 0 1px 0 rgba(255,255,255,0.05);
  }
  .tab-content { display:none; }
  .tab-content.active { display:block; }

  /* ── Cards ── */
  .cards-row  { display:flex; flex-wrap:wrap; gap:14px; }
  .cards-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(270px,1fr)); gap:14px; }
  .track-card {
    background: linear-gradient(145deg, var(--bg3), var(--bg2));
    border: 1px solid var(--glass-border);
    border-radius:20px; padding:20px;
    display:flex; flex-direction:column; gap:12px;
    flex:1 1 270px; max-width:440px;
    position:relative; overflow:hidden;
    transition:transform 0.3s cubic-bezier(0.22,1,0.36,1),
                border-color 0.3s, box-shadow 0.3s;
    cursor:default;
    backdrop-filter: blur(4px);
  }
  .track-card::before {
    content:''; position:absolute; inset:0; border-radius:20px;
    background: radial-gradient(ellipse at 20% 0%, rgba(255,107,0,0.07) 0%, transparent 55%);
    pointer-events:none;
  }
  .track-card::after {
    content:''; position:absolute;
    top:0; left:0; right:0; height:1px;
    background:linear-gradient(90deg,transparent,rgba(255,255,255,0.08),transparent);
    pointer-events:none;
  }
  .track-card:hover {
    transform:translateY(-6px) scale(1.01);
    border-color:rgba(255,107,0,0.25);
    box-shadow: 0 20px 60px rgba(0,0,0,0.4),
                0 0 0 1px rgba(255,107,0,0.1),
                0 0 40px rgba(255,107,0,0.08);
  }
  a.track-card { text-decoration:none; color:inherit; cursor:pointer; }
  a.track-card:hover .track-title { color:var(--accent2); }
  .track-link-hint {
    font-size:0.65rem; color:var(--text3); margin-top:2px;
    opacity:0; transition:opacity .25s; letter-spacing:0.05em;
  }
  a.track-card:hover .track-link-hint { opacity:1; }

  /* ── Rank badge ── */
  .rank {
    position:absolute; top:14px; right:14px;
    min-width:26px; height:26px; border-radius:999px;
    padding: 0 8px;
    display:flex; align-items:center; justify-content:center;
    font-size:0.65rem; font-weight:700; color:rgba(255,255,255,0.9);
    letter-spacing:0.05em;
  }
  .rank-1 { background:linear-gradient(135deg,#ffb800,#ff8c00); box-shadow:0 0 16px rgba(255,184,0,0.5); }
  .rank-2 { background:linear-gradient(135deg,#c0c0c0,#8a8a8a); box-shadow:0 0 12px rgba(192,192,192,0.3); }
  .rank-3 { background:linear-gradient(135deg,#cd7c2f,#a05a1e); box-shadow:0 0 12px rgba(205,124,47,0.3); }
  .rank-n { background: rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.08); color:var(--text3); }

  /* ── Card internals ── */
  .card-top { display:flex; align-items:center; gap:14px; }
  .cover {
    width:60px; height:60px; border-radius:12px;
    object-fit:cover; flex-shrink:0;
    box-shadow:0 6px 20px rgba(0,0,0,0.5);
    border:1px solid rgba(255,255,255,0.06);
  }
  .cover-placeholder {
    width:60px; height:60px; border-radius:12px;
    background:linear-gradient(135deg,var(--bg4),var(--bg3));
    border:1px solid var(--glass-border);
    display:flex; align-items:center; justify-content:center;
    font-size:1.5rem; flex-shrink:0;
  }
  .track-info { min-width:0; flex:1; }
  .track-title {
    font-size:0.88rem; font-weight:600;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
    color:var(--text); line-height:1.3;
    transition: color 0.2s;
  }
  .track-artist { font-size:0.72rem; color:var(--text3); margin-top:3px; font-weight:400; }
  .track-count {
    font-size:0.72rem; color:var(--accent2); margin-top:8px; font-weight:600;
    display:inline-flex; align-items:center; gap:4px;
    background:rgba(255,107,0,0.08); border:1px solid rgba(255,107,0,0.15);
    padding:2px 8px; border-radius:999px;
  }
  .track-time-stats {
    font-size:0.65rem; color:var(--text3); margin-top:4px;
    font-feature-settings:'tnum';
    letter-spacing:0.03em;
  }
  .mobile-plays {
    font-size:0.68rem; color:#38bdf8; margin-top:4px;
    display:flex; align-items:center; gap:4px; font-weight:500;
  }
  .mobile-icon { font-size:0.8rem; }
  .mobile-summary .summary-label { color:#38bdf8 !important; }

  /* ── Две колонки ПК / Телефон ── */
  .two-col-stats {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    margin-top: 16px;
  }
  @media (max-width: 700px) {
    .two-col-stats { grid-template-columns: 1fr; }
  }
  .col-pc, .col-mobile {
    background: var(--card-bg);
    border: 1px solid var(--glass-border);
    border-radius: 14px;
    padding: 14px;
  }
  .col-header {
    font-size: 0.8rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--text2);
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .col-mobile .col-header { color: #38bdf8; }
  .col-icon { font-size: 1rem; }
  .col-count {
    margin-left: auto;
    font-size: 1.1rem;
    font-weight: 800;
    color: var(--text1);
  }
  .cards-col { display: flex; flex-direction: column; gap: 8px; }
  .track-chart { border-top:1px solid var(--glass-border); padding-top:10px; }
  .chart-legend { display:flex; flex-wrap:wrap; gap:10px; margin-top:6px; font-size:0.63rem; }
  .legend-avg, .legend-dur { display:flex; align-items:center; gap:5px; }
  .legend-avg::before { content:''; display:inline-block; width:16px; height:2px; border-top:2px dashed #a78bfa; flex-shrink:0; }
  .legend-dur::before { content:''; display:inline-block; width:16px; height:2px; border-top:2px dashed #00e87a; flex-shrink:0; }
  .legend-avg { color:#a78bfa; }
  .legend-dur { color:#00e87a; }

  /* ── Summary pills ── */
  .month-summary {
    display:flex; align-items:center; justify-content:space-between;
    background: linear-gradient(135deg, var(--bg3), var(--bg2));
    border: 1px solid var(--glass-border);
    border-left: 2px solid var(--suno-orange);
    padding:16px 22px; border-radius:16px;
    margin-bottom:20px;
    position:relative; overflow:hidden;
  }
  .month-summary::before {
    content:''; position:absolute; inset:0;
    background: radial-gradient(ellipse at left, rgba(255,107,0,0.06), transparent 60%);
    pointer-events:none;
  }
  .summary-label { color:var(--text2); font-size:0.8rem; font-weight:500; }
  .summary-value {
    font-family:'Bebas Neue',sans-serif; font-size:2rem; letter-spacing:0.05em;
    background:linear-gradient(135deg,var(--suno-yellow),var(--suno-orange));
    -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text;
  }

  /* ── Current month ── */
  .current-month {
    background: linear-gradient(145deg, rgba(13,13,24,0.9), rgba(10,10,16,0.9));
    border:1px solid rgba(255,107,0,0.2);
    border-radius:24px; padding:32px;
    box-shadow:0 0 80px rgba(255,107,0,0.05) inset,
               0 32px 80px rgba(0,0,0,0.3);
    backdrop-filter: blur(8px);
    position:relative; overflow:hidden;
  }
  .current-month::before {
    content:''; position:absolute;
    top:0; left:0; right:0; height:1px;
    background:linear-gradient(90deg,transparent,rgba(255,107,0,0.3),transparent);
  }
  .current-header {
    color:var(--green); font-size:0.72rem;
    letter-spacing:0.25em; text-transform:uppercase; font-weight:600;
    margin-bottom:24px; display:flex; align-items:center; gap:10px;
  }
  .pulse-dot {
    width:7px; height:7px; background:var(--green);
    border-radius:50%; animation:pulse-anim 1.8s ease-in-out infinite;
    box-shadow:0 0 10px var(--green), 0 0 20px rgba(0,232,122,0.3);
  }
  @keyframes pulse-anim {
    0%,100% { opacity:1; transform:scale(1); }
    50%      { opacity:0.3; transform:scale(1.8); }
  }

  /* ── Year section ── */
  .year-section {
    background: linear-gradient(145deg, var(--bg3), var(--bg2));
    border:1px solid var(--glass-border);
    border-radius:24px; padding:36px; margin-bottom:24px;
    position:relative; overflow:hidden;
  }
  .year-section::before {
    content:''; position:absolute;
    top:0; left:0; right:0; height:1px;
    background:linear-gradient(90deg,transparent,rgba(255,184,0,0.2),transparent);
  }
  .year-title {
    font-family:'Bebas Neue',sans-serif; font-size:2rem; letter-spacing:0.15em;
    background:linear-gradient(135deg,var(--suno-yellow),var(--suno-orange));
    -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text;
    margin-bottom:24px;
  }

  /* ── Bar charts ── */
  .bar-chart {
    display:flex; align-items:flex-end; gap:5px;
    height:96px; padding:0 2px;
    border-top:1px solid var(--glass-border);
    padding-top:12px; margin-top:12px;
  }
  .year-chart { height:130px; }
  .bar-wrap {
    flex:1; display:flex; flex-direction:column;
    align-items:center; justify-content:flex-end;
    height:100%; gap:5px; cursor:default;
    position:relative;
  }
  .bar-fill {
    width:100%; min-height:3px;
    background:linear-gradient(180deg,#ffb800,#ff6b00);
    border-radius:4px 4px 0 0;
    transition:filter 0.2s, transform 0.2s;
    position:relative;
  }
  .bar-fill::after {
    content:''; position:absolute;
    top:0; left:0; right:0; height:2px;
    background:linear-gradient(90deg,transparent,rgba(255,255,255,0.25),transparent);
    border-radius:4px 4px 0 0;
  }
  .bar-wrap:hover .bar-fill {
    filter:brightness(1.2);
    background:linear-gradient(180deg,#ff9b4e,#ff2d78);
    transform: scaleX(0.92);
  }
  .bar-label {
    font-size:0.52rem; color:var(--text3); text-align:center;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap; width:100%;
    letter-spacing:0.05em;
  }

  /* ── Misc ── */
  .empty-note {
    color:var(--text3); font-size:0.82rem; text-align:center;
    padding:48px 24px;
    border:1px dashed var(--bg4); border-radius:20px;
    line-height:1.8; letter-spacing:0.02em;
  }
  .footer {
    text-align:center; color:var(--text3); font-size:0.7rem;
    margin-top:80px; padding-top:24px;
    border-top:1px solid var(--glass-border);
    letter-spacing:0.08em;
  }
  .footer a { color:var(--text3); text-decoration:none; }
  .footer a:hover { color:var(--accent2); }

  /* ── Language switcher ── */
  #lang-switcher {
    position: fixed; top: 18px; right: 20px; z-index: 990;
    display: flex; align-items: center; gap: 6px;
    background: rgba(13,13,24,0.85);
    border: 1px solid var(--glass-border);
    border-radius: 999px; padding: 5px 8px;
    backdrop-filter: blur(16px);
    box-shadow: 0 4px 24px rgba(0,0,0,0.4);
  }
  .lang-btn {
    background: none; border: none; cursor: pointer;
    color: var(--text3); font-size: 0.7rem; font-family: 'Space Grotesk', sans-serif;
    font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase;
    padding: 3px 8px; border-radius: 999px;
    transition: all 0.2s;
  }
  .lang-btn:hover { color: var(--text2); }
  .lang-btn.active {
    background: linear-gradient(135deg, rgba(255,107,0,0.25), rgba(255,45,120,0.15));
    color: #fff;
    border: 1px solid rgba(255,107,0,0.4);
  }
  .lang-sep { color: var(--text3); font-size: 0.6rem; opacity: 0.4; user-select:none; }

  /* ── HTML download button ── */
  #btn-save-html {
    position: fixed; top: 18px; left: 20px; z-index: 990;
    display: flex; align-items: center; gap: 7px;
    background: rgba(13,13,24,0.85);
    border: 1px solid rgba(255,107,0,0.35);
    border-radius: 999px; padding: 6px 14px;
    backdrop-filter: blur(16px);
    box-shadow: 0 4px 24px rgba(0,0,0,0.4);
    cursor: pointer;
    font-family: 'Space Grotesk', sans-serif;
    font-size: 0.7rem; font-weight: 600;
    letter-spacing: 0.05em; text-transform: uppercase;
    color: var(--text2);
    transition: all 0.2s;
    text-decoration: none;
  }
  #btn-save-html:hover {
    border-color: rgba(255,107,0,0.7);
    color: #fff;
    box-shadow: 0 4px 24px rgba(255,107,0,0.25);
  }
  #btn-save-html svg { flex-shrink:0; }
</style>
</head>
<body>

<div id="scroll-progress"></div>

<a id="btn-save-html" title="Сохранить HTML в загрузки">
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
    <polyline points="7 10 12 15 17 10"/>
    <line x1="12" y1="15" x2="12" y2="3"/>
  </svg>
  HTML
</a>

<div id="lang-switcher">
  <button class="lang-btn active" onclick="setLang('ru')">RU</button>
  <span class="lang-sep">|</span>
  <button class="lang-btn" onclick="setLang('en')">EN</button>
  <span class="lang-sep">|</span>
  <button class="lang-btn" onclick="setLang('uk')">UA</button>
  <span class="lang-sep">|</span>
  <button class="lang-btn" onclick="setLang('de')">DE</button>
  <span class="lang-sep">|</span>
  <button class="lang-btn" onclick="setLang('es')">ES</button>
  <span class="lang-sep">|</span>
  <button class="lang-btn" onclick="setLang('zh')">中文</button>
  <span class="lang-sep">|</span>
  <button class="lang-btn" onclick="setLang('ja')">JP</button>
</div>
<div class="glow-orb glow-1"></div>
<div class="glow-orb glow-2"></div>
<div class="glow-orb glow-3"></div>
<canvas id="suno-canvas"></canvas>

<div class="suno-fly-container" id="sunoFlyContainer">
  <div class="suno-orb orb-main" id="sunoMainOrb">SUNO</div>
  <div class="suno-orb orb-b">S</div>
  <div class="suno-orb orb-c">S</div>
  <div class="suno-orb orb-d">S</div>
</div>

<div class="hero">
  <div class="hero-img-wrap">
    <img src="2.png" alt="Suno" class="hero-logo-img"
         onerror="this.style.display='none'">
  </div>
  <div class="hero-text-group">
    <h1>SUNO STATS</h1>
    <p class="hero-sub" data-i18n="hero_sub">Твоя музыкальная история</p>
    <div class="hero-generated" data-i18n="updated_at" data-val="{{GENERATED_AT}}">Обновлено {{GENERATED_AT}}</div>
  </div>
  <div class="hero-scroll"><span>Scroll</span><div class="scroll-line"></div></div>
</div>

<div class="container">

  <div class="section reveal">
    <div class="section-title" data-i18n="sec_now">⚡&nbsp; Прямо сейчас</div>
    {{CURRENT_SECTION}}
  </div>

  <div class="section reveal">
    <div class="section-title" data-i18n="sec_months">📅&nbsp; По месяцам</div>
    {{MONTH_TABS}}
  </div>

  <div class="section reveal">
    <div class="section-title" data-i18n="sec_year">🏅&nbsp; Итоги года</div>
    {{YEAR_SECTIONS}}
  </div>

  <div class="footer" data-i18n="footer">
    Создано с ♥ Suno RPC &nbsp;·&nbsp; Данные хранятся локально
  </div>

</div>

<script>
/* ── LENIS SMOOTH SCROLL ── */
(function(){
  function Lenis(){
    this.scroll=window.scrollY||0;
    this.target=this.scroll;
    this.vel=0;
    this.ease=0.07;
    this.raf=null;
    this._onWheel=this._onWheel.bind(this);
    this._onKey=this._onKey.bind(this);
    this._onTouch=this._onTouch.bind(this);
    this._touchY=0;
    document.documentElement.style.overflow='hidden';
    document.body.style.overflow='hidden';
    var wrapper=document.createElement('div');
    wrapper.id='lenis-wrapper';
    wrapper.style.cssText='position:fixed;top:0;left:0;width:100%;height:100%;overflow-y:scroll;overflow-x:hidden;scrollbar-width:thin;scrollbar-color:rgba(255,107,0,0.4) transparent;';
    while(document.body.firstChild) wrapper.appendChild(document.body.firstChild);
    document.body.appendChild(wrapper);
    this.wrapper=wrapper;
    this.scroll=wrapper.scrollTop;
    this.target=wrapper.scrollTop;
    wrapper.addEventListener('wheel',this._onWheel,{passive:false});
    window.addEventListener('keydown',this._onKey);
    wrapper.addEventListener('touchstart',this._onTouch,{passive:true});
    wrapper.addEventListener('touchmove',this._onTouchMove.bind(this),{passive:false});
    this._tick=this._tick.bind(this);
    this.raf=requestAnimationFrame(this._tick);
  }
  Lenis.prototype._onWheel=function(e){
    e.preventDefault();
    var delta=e.deltaY;
    if(e.deltaMode===1) delta*=40;
    if(e.deltaMode===2) delta*=800;
    this.target+=delta;
    this._clamp();
  };
  Lenis.prototype._onKey=function(e){
    var map={ArrowDown:80,ArrowUp:-80,PageDown:window.innerHeight*.9,PageUp:-window.innerHeight*.9,Home:-999999,End:999999,Space:window.innerHeight*.9};
    if(e.shiftKey&&e.key==='Space') map['Space']=-window.innerHeight*.9;
    if(map[e.key]!==undefined){
      e.preventDefault();
      this.target+=map[e.key];
      this._clamp();
    }
  };
  Lenis.prototype._onTouch=function(e){ this._touchY=e.touches[0].clientY; };
  Lenis.prototype._onTouchMove=function(e){
    e.preventDefault();
    var dy=this._touchY-e.touches[0].clientY;
    this._touchY=e.touches[0].clientY;
    this.target+=dy*1.5;
    this._clamp();
  };
  Lenis.prototype._clamp=function(){
    var max=this.wrapper.scrollHeight-window.innerHeight;
    if(this.target<0) this.target=0;
    if(this.target>max) this.target=max;
  };
  Lenis.prototype._tick=function(){
    this.scroll+=(this.target-this.scroll)*this.ease;
    if(Math.abs(this.target-this.scroll)<0.5) this.scroll=this.target;
    this.wrapper.scrollTop=this.scroll;
    window.dispatchEvent(new CustomEvent('lenis-scroll',{detail:{scrollY:this.scroll}}));
    this.raf=requestAnimationFrame(this._tick);
  };
  window._lenis=new Lenis();
})();

(function(){
  /* ── OVERRIDE scrollY for effects ── */
  var getScrollY=function(){
    return window._lenis?window._lenis.scroll:(window.scrollY||0);
  };

  /* ── PARTICLES ── */
  var c=document.getElementById('suno-canvas'),ctx=c.getContext('2d'),W,H,ps=[];
  function resize(){W=c.width=window.innerWidth;H=c.height=window.innerHeight;}
  resize(); window.addEventListener('resize',resize);
  function P(init){
    this.x=Math.random()*W; this.y=init?Math.random()*H:H+10;
    this.vx=(Math.random()-.5)*.3; this.vy=-(Math.random()*.4+.1);
    this.life=1; this.decay=Math.random()*.003+.001;
    this.r=Math.random()*1.5+.5;
    this.hue=[28,340,45][Math.floor(Math.random()*3)];
  }
  P.prototype.reset=function(){
    this.x=Math.random()*W; this.y=H+10;
    this.vx=(Math.random()-.5)*.3; this.vy=-(Math.random()*.4+.1);
    this.life=1; this.decay=Math.random()*.003+.001;
    this.r=Math.random()*1.5+.5;
    this.hue=[28,340,45][Math.floor(Math.random()*3)];
  };
  for(var i=0;i<120;i++) ps.push(new P(true));
  function frame(){
    ctx.clearRect(0,0,W,H);
    for(var i=0;i<ps.length;i++){
      var p=ps[i];
      p.x+=p.vx; p.y+=p.vy; p.life-=p.decay;
      if(p.life<=0||p.y<-10) p.reset();
      ctx.save(); ctx.globalAlpha=p.life*.6;
      ctx.fillStyle='hsl('+p.hue+',100%,65%)';
      ctx.beginPath(); ctx.arc(p.x,p.y,p.r,0,Math.PI*2); ctx.fill();
      ctx.restore();
    }
    requestAnimationFrame(frame);
  }
  frame();

  /* ── ORB ENTRY ── */
  var mo=document.getElementById('sunoMainOrb');
  var fc=document.getElementById('sunoFlyContainer');
  if(mo) setTimeout(function(){mo.classList.add('settled');},1600);
  if(fc) {
    setTimeout(function(){ fc.style.display='none'; fc.style.visibility='hidden'; },2400);
    // Fallback: скрыть через 4 секунды если что-то пошло не так
    setTimeout(function(){ fc.style.display='none'; fc.style.visibility='hidden'; fc.style.pointerEvents='none'; },4000);
  }

  /* ── SCROLL PROGRESS BAR ── */
  var prog=document.getElementById('scroll-progress');
  function updateProgress(){
    var st=getScrollY();
    var wrapper=window._lenis?window._lenis.wrapper:document.documentElement;
    var dh=(wrapper.scrollHeight||document.documentElement.scrollHeight)-window.innerHeight;
    var pct=dh>0?Math.min(100,st/dh*100):0;
    prog.style.width=pct+'%';
  }

  /* ── REVEAL ON SCROLL ── */
  var reveals=document.querySelectorAll('.reveal,.reveal-left,.reveal-right');
  function checkReveals(){
    var wh=window.innerHeight;
    for(var i=0;i<reveals.length;i++){
      var el=reveals[i];
      var top=el.getBoundingClientRect().top;
      if(top<wh*0.88) el.classList.add('visible');
    }
  }

  /* ── PARALLAX HERO TITLE ── */
  var heroTextGroup=document.querySelector('.hero-text-group');
  var heroImg=document.querySelector('.hero-logo-img');
  function doParallax(){
    var sy=getScrollY();
    if(heroTextGroup) heroTextGroup.style.transform='translateY('+sy*0.18+'px)';
    if(heroImg)       heroImg.style.transform='translateY('+sy*0.08+'px) scale('+(1-sy*0.0002)+')';
  }

  /* ── GLOW ORBS PARALLAX ── */
  var glows=document.querySelectorAll('.glow-orb');
  function doGlowParallax(){
    var sy=getScrollY();
    if(glows[0]) glows[0].style.transform='translateY('+(sy*0.12)+'px) scale(1)';
    if(glows[1]) glows[1].style.transform='translateY('+(sy*-0.08)+'px) scale(1)';
    if(glows[2]) glows[2].style.transform='translateY('+(sy*0.06)+'px) scale(1)';
  }

  /* ── SCROLL BURST PARTICLES ── */
  var lastScrollY=0, scrollVel=0, burstTimer=null;
  function scrollBurst(vel){
    var count=Math.min(12,Math.floor(Math.abs(vel)/6)+3);
    for(var i=0;i<count;i++){
      var p=new P(false);
      p.x=Math.random()*W;
      p.y=window.scrollY%H+(Math.random()-0.5)*200;
      p.vy=-(Math.random()*.8+.3)*(vel>0?1:-1);
      p.vx=(Math.random()-.5)*.8;
      p.r=Math.random()*2.5+1;
      p.decay=Math.random()*.008+.004;
      ps.push(p);
      if(ps.length>200) ps.shift();
    }
  }

  /* ── SECTION GLOW ON ENTER ── */
  var sections=document.querySelectorAll('.section');
  var sectionColors=['rgba(255,107,0,0.08)','rgba(255,45,120,0.07)','rgba(255,184,0,0.06)'];
  function checkSectionGlow(){
    var wh=window.innerHeight;
    for(var i=0;i<sections.length;i++){
      var r=sections[i].getBoundingClientRect();
      var inView=r.top<wh*0.6&&r.bottom>wh*0.3;
      sections[i].style.background=inView?sectionColors[i%sectionColors.length]:'transparent';
      sections[i].style.borderRadius=inView?'20px':'0';
      sections[i].style.padding=inView?'24px':'0';
      sections[i].style.transition='background 0.6s ease, padding 0.4s ease, border-radius 0.4s ease';
    }
  }

  /* ── BAR CHART GROW ON SCROLL ── */
  var barsAnimated=false;
  function animateBars(){
    if(barsAnimated) return;
    var fills=document.querySelectorAll('.bar-fill');
    var anyVisible=false;
    for(var i=0;i<fills.length;i++){
      var r=fills[i].getBoundingClientRect();
      if(r.top<window.innerHeight){anyVisible=true;break;}
    }
    if(!anyVisible) return;
    barsAnimated=true;
    fills.forEach(function(el,idx){
      var h=el.style.height;
      el.style.height='0%';
      el.style.transition='none';
      setTimeout(function(){
        el.style.transition='height 0.8s cubic-bezier(0.22,1,0.36,1)';
        el.style.height=h;
      },50+idx*40);
    });
  }

  /* ── TRACK CARDS STAGGER ── */
  var cardsStaggered=false;
  function staggerCards(){
    if(cardsStaggered) return;
    var cards=document.querySelectorAll('.track-card');
    var anyV=false;
    for(var i=0;i<cards.length;i++){
      if(cards[i].getBoundingClientRect().top<window.innerHeight){anyV=true;break;}
    }
    if(!anyV) return;
    cardsStaggered=true;
    cards.forEach(function(el,i){
      el.style.opacity='0';
      el.style.transform='translateY(30px) scale(0.96)';
      el.style.transition='none';
      setTimeout(function(){
        el.style.transition='opacity 0.5s ease, transform 0.5s cubic-bezier(0.22,1,0.36,1)';
        el.style.opacity='1';
        el.style.transform='translateY(0) scale(1)';
      },80+i*60);
    });
  }

  /* ── MAIN SCROLL HANDLER ── */
  var ticking=false;
  function onScroll(){
    var sy=getScrollY();
    scrollVel=sy-lastScrollY; lastScrollY=sy;
    if(Math.abs(scrollVel)>4) scrollBurst(scrollVel);
    if(!ticking){
      ticking=true;
      requestAnimationFrame(function(){
        updateProgress();
        doParallax();
        doGlowParallax();
        checkReveals();
        checkSectionGlow();
        animateBars();
        staggerCards();
        ticking=false;
      });
    }
  }
  window.addEventListener('lenis-scroll', onScroll, {passive:true});

  /* init */
  setTimeout(function(){
    updateProgress();
    checkReveals();
    staggerCards();
    animateBars();
  },2600);

})();

function switchTab(id) {
  document.querySelectorAll('.tab-content').forEach(function(el){el.classList.remove('active');});
  document.querySelectorAll('.tab-btn').forEach(function(el){el.classList.remove('active');});
  var content = document.getElementById('tab-' + id);
  if (content) content.classList.add('active');
  document.querySelectorAll('.tab-btn').forEach(function(btn){
    if (btn.getAttribute('onclick') === "switchTab('" + id + "')") {
      btn.classList.add('active');
    }
  });
}

/* ══════════════════════════════════════════
   i18n ENGINE
══════════════════════════════════════════ */
var I18N = {
  ru: {
    hero_sub:        'Твоя музыкальная история',
    updated_at:      'Обновлено {val}',
    sec_now:         '⚡\u00a0 Прямо сейчас',
    sec_months:      '📅\u00a0 По месяцам',
    sec_year:        '🏅\u00a0 Итоги года',
    footer:          'Создано с ♥ Suno RPC\u00a0·\u00a0 Данные хранятся локально',
    no_months:       'Пока нет завершённых месяцев — возвращайся позже! 🎵',
    no_tracks_month: 'В этом месяце треков не было 🎶',
    no_years:        'Годовая статистика появится после 31 декабря ✨',
    no_current:      'Начни слушать музыку — статистика появится здесь! 🎵',
    empty_now:       'Пока пусто',
    total_plays:     '🎧 Всего прослушиваний',
    time_in_music:   '⏱ Времени в музыке',
    time_in_music_year: '⏱ Времени в музыке за год',
    total_year:      '🎧 Всего за год',
    already_listened:'🎧 Уже прослушано',
    top3_month:      '🏆 Топ-3 трека месяца',
    top10_year:      '🏆 Топ-10 треков года',
    leaders_now:     '🌟 Лидеры прямо сейчас',
    plays_by_month:  '📊 Прослушивания по месяцам',
    now_playing:     '{month} — идёт прямо сейчас',
    plays:           '▶ {count} раз',
    avg_listen:      'обычно слушаю: {val}',
    track_length:    'длина трека: {val}',
    avg_listen_card: 'обычно слушаю {val}',
    total_listen_card:'всего {val}',
    open_suno:       '↗ открыть на Suno',
    time_h:          'ч',
    time_min:        'мин',
    months: ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'],
  },
  en: {
    hero_sub:        'Your music history in numbers',
    updated_at:      'Updated {val}',
    sec_now:         '⚡\u00a0 Right now',
    sec_months:      '📅\u00a0 By month',
    sec_year:        '🏅\u00a0 Year in review',
    footer:          'Made with ♥ Suno RPC\u00a0·\u00a0 Data stored locally',
    no_months:       'No completed months yet — come back later! 🎵',
    no_tracks_month: 'No tracks this month 🎶',
    no_years:        'Yearly stats will appear after December 31 ✨',
    no_current:      'Start listening — your stats will appear here! 🎵',
    empty_now:       'Nothing yet',
    total_plays:     '🎧 Total plays',
    time_in_music:   '⏱ Time in music',
    time_in_music_year: '⏱ Time in music this year',
    total_year:      '🎧 Total this year',
    already_listened:'🎧 Already played',
    top3_month:      '🏆 Top 3 tracks of the month',
    top10_year:      '🏆 Top 10 tracks of the year',
    leaders_now:     '🌟 Leaders right now',
    plays_by_month:  '📊 Plays by month',
    now_playing:     '{month} — in progress',
    plays:           '▶ {count} plays',
    avg_listen:      'avg listen: {val}',
    track_length:    'track length: {val}',
    avg_listen_card: 'avg listen {val}',
    total_listen_card:'total {val}',
    open_suno:       '↗ open on Suno',
    time_h:          'h',
    time_min:        'min',
    months: ['January','February','March','April','May','June','July','August','September','October','November','December'],
  },
  uk: {
    hero_sub:        'Твоя музична історія в цифрах',
    updated_at:      'Оновлено {val}',
    sec_now:         '⚡\u00a0 Прямо зараз',
    sec_months:      '📅\u00a0 По місяцях',
    sec_year:        '🏅\u00a0 Підсумки року',
    footer:          'Створено з ♥ Suno RPC\u00a0·\u00a0 Дані зберігаються локально',
    no_months:       'Поки немає завершених місяців — повертайся пізніше! 🎵',
    no_tracks_month: 'У цьому місяці треків не було 🎶',
    no_years:        'Річна статистика зʼявиться після 31 грудня ✨',
    no_current:      'Починай слухати музику — статистика зʼявиться тут! 🎵',
    empty_now:       'Поки порожньо',
    total_plays:     '🎧 Усього прослуховувань',
    time_in_music:   '⏱ Часу в музиці',
    time_in_music_year: '⏱ Часу в музиці за рік',
    total_year:      '🎧 Усього за рік',
    already_listened:'🎧 Вже прослухано',
    top3_month:      '🏆 Топ-3 треки місяця',
    top10_year:      '🏆 Топ-10 треків року',
    leaders_now:     '🌟 Лідери прямо зараз',
    plays_by_month:  '📊 Прослуховування по місяцях',
    now_playing:     '{month} — іде прямо зараз',
    plays:           '▶ {count} разів',
    avg_listen:      'зазвичай слухаю: {val}',
    track_length:    'довжина треку: {val}',
    avg_listen_card: 'зазвичай слухаю {val}',
    total_listen_card:'усього {val}',
    open_suno:       '↗ відкрити на Suno',
    time_h:          'год',
    time_min:        'хв',
    months: ['Січень','Лютий','Березень','Квітень','Травень','Червень','Липень','Серпень','Вересень','Жовтень','Листопад','Грудень'],
  },
  de: {
    hero_sub:        'Deine Musikgeschichte in Zahlen',
    updated_at:      'Aktualisiert {val}',
    sec_now:         '⚡\u00a0 Gerade jetzt',
    sec_months:      '📅\u00a0 Nach Monat',
    sec_year:        '🏅\u00a0 Jahresrückblick',
    footer:          'Erstellt mit ♥ Suno RPC\u00a0·\u00a0 Daten lokal gespeichert',
    no_months:       'Noch keine abgeschlossenen Monate — komm später wieder! 🎵',
    no_tracks_month: 'Keine Tracks in diesem Monat 🎶',
    no_years:        'Jahresstatistik erscheint nach dem 31. Dezember ✨',
    no_current:      'Fang an zu hören — deine Statistik erscheint hier! 🎵',
    empty_now:       'Noch leer',
    total_plays:     '🎧 Gesamte Wiedergaben',
    time_in_music:   '⏱ Zeit in der Musik',
    time_in_music_year: '⏱ Zeit in der Musik dieses Jahr',
    total_year:      '🎧 Gesamt dieses Jahr',
    already_listened:'🎧 Bereits gehört',
    top3_month:      '🏆 Top 3 Tracks des Monats',
    top10_year:      '🏆 Top 10 Tracks des Jahres',
    leaders_now:     '🌟 Spitzenreiter gerade jetzt',
    plays_by_month:  '📊 Wiedergaben pro Monat',
    now_playing:     '{month} — läuft gerade',
    plays:           '▶ {count}× gespielt',
    avg_listen:      'Ø gehört: {val}',
    track_length:    'Tracklänge: {val}',
    avg_listen_card: 'Ø gehört {val}',
    total_listen_card:'gesamt {val}',
    open_suno:       '↗ auf Suno öffnen',
    time_h:          'Std',
    time_min:        'Min',
    months: ['Januar','Februar','März','April','Mai','Juni','Juli','August','September','Oktober','November','Dezember'],
  },
  es: {
    hero_sub:        'Tu historia musical en números',
    updated_at:      'Actualizado {val}',
    sec_now:         '⚡\u00a0 Ahora mismo',
    sec_months:      '📅\u00a0 Por mes',
    sec_year:        '🏅\u00a0 Resumen anual',
    footer:          'Hecho con ♥ Suno RPC\u00a0·\u00a0 Datos guardados localmente',
    no_months:       '¡Aún no hay meses completados — vuelve más tarde! 🎵',
    no_tracks_month: 'No hubo pistas este mes 🎶',
    no_years:        'Las estadísticas anuales aparecerán después del 31 de diciembre ✨',
    no_current:      '¡Empieza a escuchar — tus estadísticas aparecerán aquí! 🎵',
    empty_now:       'Vacío por ahora',
    total_plays:     '🎧 Total de reproducciones',
    time_in_music:   '⏱ Tiempo en música',
    time_in_music_year: '⏱ Tiempo en música este año',
    total_year:      '🎧 Total este año',
    already_listened:'🎧 Ya escuchado',
    top3_month:      '🏆 Top 3 pistas del mes',
    top10_year:      '🏆 Top 10 pistas del año',
    leaders_now:     '🌟 Líderes ahora mismo',
    plays_by_month:  '📊 Reproducciones por mes',
    now_playing:     '{month} — en curso',
    plays:           '▶ {count} veces',
    avg_listen:      'escucha media: {val}',
    track_length:    'duración: {val}',
    avg_listen_card: 'escucho normalmente {val}',
    total_listen_card:'total {val}',
    open_suno:       '↗ abrir en Suno',
    time_h:          'h',
    time_min:        'min',
    months: ['Enero','Febrero','Marzo','Abril','Mayo','Junio','Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre'],
  },
  zh: {
    hero_sub:        '你的音乐历史数据',
    updated_at:      '更新于 {val}',
    sec_now:         '⚡\u00a0 正在进行',
    sec_months:      '📅\u00a0 按月统计',
    sec_year:        '🏅\u00a0 年度回顾',
    footer:          '由 Suno RPC 制作 ♥\u00a0·\u00a0 数据本地存储',
    no_months:       '暂无已完成的月份 — 稍后再来！🎵',
    no_tracks_month: '本月没有曲目 🎶',
    no_years:        '年度统计将在12月31日后出现 ✨',
    no_current:      '开始聆听音乐吧 — 统计数据将在此显示！🎵',
    empty_now:       '暂时为空',
    total_plays:     '🎧 总播放次数',
    time_in_music:   '⏱ 音乐时长',
    time_in_music_year: '⏱ 本年度音乐时长',
    total_year:      '🎧 本年度合计',
    already_listened:'🎧 已播放',
    top3_month:      '🏆 本月前3名',
    top10_year:      '🏆 年度前10名',
    leaders_now:     '🌟 当前热门',
    plays_by_month:  '📊 每月播放量',
    now_playing:     '{month} — 进行中',
    plays:           '▶ 播放 {count} 次',
    avg_listen:      '平均收听: {val}',
    track_length:    '曲目时长: {val}',
    avg_listen_card: '通常听 {val}',
    total_listen_card:'共 {val}',
    open_suno:       '↗ 在 Suno 上打开',
    time_h:          '时',
    time_min:        '分',
    months: ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'],
  },
  ja: {
    hero_sub:        'あなたの音楽ヒストリー',
    updated_at:      '更新日時: {val}',
    sec_now:         '⚡\u00a0 今すぐ',
    sec_months:      '📅\u00a0 月別統計',
    sec_year:        '🏅\u00a0 年間ベスト',
    footer:          'Suno RPC で作成 ♥\u00a0·\u00a0 データはローカルに保存',
    no_months:       'まだ完了した月はありません — 後で戻ってください！🎵',
    no_tracks_month: '今月はトラックがありませんでした 🎶',
    no_years:        '年間統計は12月31日以降に表示されます ✨',
    no_current:      '音楽を聴き始めましょう — 統計がここに表示されます！🎵',
    empty_now:       'まだ空です',
    total_plays:     '🎧 総再生数',
    time_in_music:   '⏱ 音楽時間',
    time_in_music_year: '⏱ 今年の音楽時間',
    total_year:      '🎧 今年の合計',
    already_listened:'🎧 再生済み',
    top3_month:      '🏆 今月のトップ3',
    top10_year:      '🏆 年間トップ10',
    leaders_now:     '🌟 今のリーダー',
    plays_by_month:  '📊 月別再生数',
    now_playing:     '{month} — 進行中',
    plays:           '▶ {count}回再生',
    avg_listen:      '平均視聴: {val}',
    track_length:    '曲の長さ: {val}',
    avg_listen_card: '通常 {val} 聴く',
    total_listen_card:'合計 {val}',
    open_suno:       '↗ Sunoで開く',
    time_h:          '時間',
    time_min:        '分',
    months: ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'],
  }
};

var _currentLang = localStorage.getItem('suno_lang') || 'ru';

function setLang(lang) {
  if (!I18N[lang]) return;
  _currentLang = lang;
  localStorage.setItem('suno_lang', lang);
  document.querySelectorAll('.lang-btn').forEach(function(btn){
    btn.classList.toggle('active', btn.getAttribute('onclick') === "setLang('" + lang + "')");
  });
  applyLang(lang);
}

function applyLang(lang) {
  var T = I18N[lang] || I18N['ru'];
  document.querySelectorAll('[data-i18n]').forEach(function(el){
    var key = el.getAttribute('data-i18n');
    if (!T[key]) return;
    var tpl = T[key];
    var val = el.getAttribute('data-val') || '';
    var count = el.getAttribute('data-count') || '';

    // Перевод названия месяца если есть data-month-key
    var monthKey = el.getAttribute('data-month-key') || '';
    var month = el.getAttribute('data-month') || '';
    if (monthKey && T.months) {
      var parts = monthKey.split('-');
      var mIdx = parseInt(parts[1], 10) - 1;
      if (T.months[mIdx]) {
        month = T.months[mIdx] + ' ' + parts[0];
        el.setAttribute('data-month', month);
      }
    }

    tpl = tpl.replace('{val}', val).replace('{month}', month).replace('{count}', count);
    el.textContent = tpl;
  });

  // Форматируем .i18n-time спаны с data-seconds
  var h_unit   = T.time_h   || 'ч';
  var min_unit = T.time_min || 'мин';
  document.querySelectorAll('.i18n-time[data-seconds]').forEach(function(el){
    var s = parseInt(el.getAttribute('data-seconds'), 10) || 0;
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    el.textContent = h > 0 ? (h + ' ' + h_unit + ' ' + m + ' ' + min_unit) : (m + ' ' + min_unit);
  });

  // Переводим названия месяцев в кнопках табов
  if (T.months) {
    document.querySelectorAll('.tab-btn[data-month-label]').forEach(function(btn){
      // data-month-label содержит "Январь 2026" — обновляем на нужный язык
      // читаем исходный month-key из id таба
      var mk = btn.getAttribute('onclick').match(/'([^']+)'/);
      if (!mk) return;
      var parts = mk[1].split('-');
      var mIdx = parseInt(parts[1], 10) - 1;
      if (T.months[mIdx]) {
        btn.textContent = T.months[mIdx] + ' ' + parts[0];
      }
    });
  }
}

// Init on load
(function(){
  setLang(_currentLang);
})();

// ── HTML Export ──────────────────────────────────────────────────────────────
(function() {
  var btn = document.getElementById('btn-save-html');
  var date = new Date();
  var fname = 'suno_stats_' + date.getFullYear() + '_' +
    String(date.getMonth()+1).padStart(2,'0') + '_' +
    String(date.getDate()).padStart(2,'0') + '.html';
  btn.setAttribute('download', fname);
  btn.setAttribute('href', window.location.href);
})();
</script>
</body>
</html>
"""

