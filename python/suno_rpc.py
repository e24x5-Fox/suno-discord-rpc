#!/usr/bin/env python3
"""
suno_rpc.py — Suno AI → Discord Rich Presence, headless-бэкенд.

Интерфейс (окно, трей, меню) живёт в Electron-приложении desktop/ и общается
с этим процессом по control API на 127.0.0.1:6972 (см. раздел «CONTROL API»).
Сам по себе бэкенд окон не показывает — запускать его руками имеет смысл
только для отладки.

Зависимости: pip install pypresence websockets aiohttp
"""

import asyncio
import json
import time
import sys
import threading
import io
import os
import ctypes
import configparser
import re

# ── Защита от повторного запуска ──────────────────────────────────────────────
# Единственность интерфейса обеспечивает Electron (requestSingleInstanceLock),
# но бэкенд может остаться «сиротой» от упавшего родителя и продолжать держать
# порты 6969/6970/6971/6972 — тогда новый бэкенд молча проиграет борьбу за них.
# Мьютекс делает такой конфликт явным: второй процесс сразу выходит с кодом 3,
# и Electron показывает это в логах вместо загадочно неработающего приложения.
def _ensure_single_instance():
    # ctypes.windll.kernel32 + ctypes.GetLastError() ненадёжны для этой проверки:
    # между вызовом CreateMutexW и чтением кода ошибки ctypes может незаметно
    # выполнить другие Win32-вызовы (маршалинг аргументов и т.п.), которые
    # сбрасывают код последней ошибки — и ERROR_ALREADY_EXISTS теряется, второй процесс
    # думает, что мьютекса ещё нет, и продолжает запуск как обычно (что и
    # происходило: два SunoRPC.exe работали одновременно, конкурируя за порт
    # 6969 и за Discord RPC — Discord иногда показывал протухший статус от
    # процесса-неудачника). WinDLL(..., use_last_error=True) + get_last_error()
    # — задокументированный надёжный способ получить именно код ошибки СВОЕГО
    # вызова, а не что попало из внутренней кухни ctypes.
    ERROR_ALREADY_EXISTS = 183
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    mutex = kernel32.CreateMutexW(None, False, "SunoRPC_SingleInstance_Mutex")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        print("Бэкенд уже запущен (мьютекс держит другой процесс) — выходим.")
        sys.exit(3)
    return mutex

_single_instance_mutex = _ensure_single_instance()

# ── Безопасный stdout/stderr для windowed-сборки (console=False) ─────────────
# PyInstaller в windowed-режиме не выделяет консоль: где-то в фоновом asyncio-потоке
# запись в исходный sys.stdout/stderr роняет поток без единого следа (окно и Discord
# уже успевают подняться до этого места, поэтому баг незаметен на первый взгляд).
# Когда бэкенд запущен из Electron через spawn со stdio-пайпами, sys.stdout реален
# и подменять его НЕ надо: родитель читает эти строки и показывает их в своём логе.
# Файл нужен только когда потока вывода нет вообще (запуск .exe двойным кликом).
if getattr(sys, 'frozen', False) and (sys.stdout is None or sys.stderr is None):
    _console_log_dir = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "SunoRPC")
    os.makedirs(_console_log_dir, exist_ok=True)
    _console_log = open(os.path.join(_console_log_dir, "suno_rpc_console.log"),
                         "w", encoding="utf-8", buffering=1)
    sys.stdout = _console_log
    sys.stderr = _console_log
else:
    # Когда вывод уходит в пайп родителя (Electron запускает бэкенд через spawn),
    # Python выбирает кодировку по системной кодовой странице Windows — здесь это
    # cp1251, в которой эмодзи не существует. Любой log_event с эмодзи — а их тут
    # в каждой второй строке — валил процесс UnicodeEncodeError'ом ещё на старте,
    # до запуска серверов, и Electron видел просто мгновенно умерший бэкенд.
    # Раньше баг не проявлялся: вывод подменялся на UTF-8-файл (ветка выше).
    # line_buffering=True здесь так же обязателен, как кодировка: без него вывод
    # в пайп копится блоками по 8 КБ, и родитель получает логи пачками с большой
    # задержкой, а при падении процесса теряет их совсем. В разработке это
    # прикрывал флаг -u, но у собранного PyInstaller'ом exe его передать негде.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            pass   # не текстовый поток или уже подменён — не критично

# ── Стартовое сообщение ──────────────────────────────────────────────────────
print("=" * 50)
print("  🎵 Suno → Discord Rich Presence")
print("=" * 50)
print("Бэкенд. Окно и трей — в Electron-приложении (desktop/).")
print("Этот вывод читает интерфейс и показывает на вкладке «Логи».")
# ─────────────────────────────────────────────────────────────────────────────

# ── Логи для вкладки "Логи" в веб-интерфейсе ──────────────────────────────────
# В релизной сборке нет консоли, поэтому единственный способ для пользователя
# увидеть, что происходит, — вкладка "Логи" в окне приложения.
# error_log — только реальные ошибки (считается в бейдже-предупреждении).
# event_log — обычные события (подключения, треки, старт сервисов), для полной картины.
MAX_ERROR_LOG = 200
MAX_EVENT_LOG = 300
error_log: list = []   # [(timestamp, message), ...]
event_log: list = []   # [(timestamp, message), ...]


def log_error(msg: str):
    """Печатает сообщение в консоль (если есть) и сохраняет для вкладки "Логи"."""
    print(msg)
    error_log.append((time.time(), msg))
    if len(error_log) > MAX_ERROR_LOG:
        del error_log[0]


def log_event(msg: str):
    """Печатает сообщение в консоль и сохраняет как обычное событие для вкладки "Логи"."""
    print(msg)
    event_log.append((time.time(), msg))
    if len(event_log) > MAX_EVENT_LOG:
        del event_log[0]


try:
    import suno_stats
    _STATS_AVAILABLE = True
except ImportError:
    _STATS_AVAILABLE = False
    log_error("⚠️  suno_stats.py не найден — статистика отключена")

try:
    import youtube_stats
    _YT_STATS_AVAILABLE = _STATS_AVAILABLE   # отчёт рисуется шаблоном из suno_stats
except ImportError:
    _YT_STATS_AVAILABLE = False
    log_error("⚠️  youtube_stats.py не найден — статистика YouTube отключена")

try:
    import websockets
except ImportError:
    print("pip install websockets"); sys.exit(1)

try:
    from aiohttp import web as aiohttp_web
    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False
    log_error("⚠️  aiohttp не найден — мобильная синхронизация отключена. Установите: pip install aiohttp")

MOBILE_SYNC_PORT = 6971
CONTROL_PORT     = 6972   # control API для Electron-интерфейса (только 127.0.0.1)

try:
    from pypresence import AioPresence
except ImportError:
    print("pip install pypresence"); sys.exit(1)

# ─────────────────────────────────────────────
def _app_dir():
    """Папка для конфига: AppData\SunoRPC в .exe, рядом со скриптом при разработке."""
    if getattr(sys, 'frozen', False):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "SunoRPC")
        os.makedirs(path, exist_ok=True)
        return path
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_FILE       = os.path.join(_app_dir(), "suno_rpc.cfg")
DISCORD_CLIENT_ID = "1426921356527927399"
WEBSOCKET_PORT    = 6969
UPDATE_INTERVAL   = 15
# ─────────────────────────────────────────────

rpc             = None
last_update     = 0
last_data       = None
discord_ok      = False
ws_clients      = 0
async_loop      = None   # ссылка на asyncio loop фонового потока
broadcast_clients: set = set()   # подписчики broadcast API

# ── Конфиг ────────────────────────────────────
cfg = configparser.ConfigParser()
BROADCAST_PORT    = 6970

# Ветка, а не тег: ссылка должна оставаться рабочей и после добавления новых
# иконок, без правки конфигов у тех, кто уже поставил приложение.
ICON_ASSET_BASE = ("https://raw.githubusercontent.com/e24x5-Fox/suno-discord-rpc/"
                   "desktop-app/desktop/src/icons/")

cfg["settings"] = {
    "discord_client_id": DISCORD_CLIENT_ID,
    "websocket_port":    str(WEBSOCKET_PORT),
    "broadcast_port":    str(BROADCAST_PORT),
    "broadcast_enabled": "true",
    "update_interval":   str(UPDATE_INTERVAL),
    "show_elapsed":      "true",
    "autostart_discord": "true",
    "activity_type":     "playing",
    # Шапка активности в Discord — это глагол типа («Слушает», «Смотрит») плюс
    # ИМЯ, которое по умолчанию берётся из самого Discord-приложения. Пустое
    # значение = оставить имя приложения; заданное — подменить его.
    "app_name":          "",
    "youtube_enabled":       "true",
    "youtube_activity_type": "watching",
    "youtube_app_name":      "YouTube",
    # ── Иконка приложения в самой активности ────────────────────────────────
    # Какой вариант иконки выбран, знает Electron (ui-settings.json): трей надо
    # нарисовать до того, как бэкенд поднимется. Сюда значение приезжает через
    # /api/set_icon_variant и хранится, чтобы после перезапуска бэкенда картинка
    # была верной ещё до того, как интерфейс успеет о ней сообщить.
    "icon_variant":      "play",
    # Ключа icon_asset_base здесь намеренно нет, хотя app_icon_url() его читает:
    # попав в suno_rpc.cfg, он застыл бы там навсегда и пережил бы обновление
    # приложения — при переезде иконок на другую ветку у всех, кто уже запускал
    # программу, ссылки остались бы старыми. Значение по умолчанию берётся из
    # константы ICON_ASSET_BASE, а ключ в конфиге остаётся способом переопределить
    # адрес вручную.
    # Маленький кружок поверх обложки трека.
    "app_icon_small":    "true",
}
cfg["buttons"] = {
    "btn1_enabled":  "false",
    "btn1_label":    "Слушать на Suno",
    "btn1_auto_url": "false",
    "btn1_url":      "https://suno.com",
    "btn2_enabled":  "false",
    "btn2_label":    "Мой профиль",
    "btn2_url":      "",
}
# Шаблон на случай, когда ни одно расширение не присылает данных: без него в
# Discord навсегда зависал последний трек, доигравший полчаса назад.
cfg["idle"] = {
    "enabled":       "true",
    # Через сколько секунд без воспроизведения показывать шаблон. 0 — сразу,
    # как только нажал паузу; большое значение — фактически только когда
    # расширение отключилось совсем.
    "after_seconds": "30",
    "activity_type": "playing",
    "app_name":      "",
    "details":       "Ничего не играет",
    "state":         "Слушал {tracks} треков · {hours}",
    # Пустой large_image + use_app_icon = "true" означает «взять иконку
    # приложения»: у шаблона ожидания своей картинки обычно нет, а показывать
    # в тишине именно значок приложения — самое осмысленное.
    "large_image":   "",
    "use_app_icon":  "true",
    "large_text":    "",
    "small_text":    "",
    "show_timer":    "false",
    "btn1_enabled":  "false",
    "btn1_label":    "",
    "btn1_url":      "",
    "btn2_enabled":  "false",
    "btn2_label":    "",
    "btn2_url":      "",
}
# Кнопки для YouTube отдельные: подпись «Слушать на Suno» под видео выглядит
# нелепо, а авто-ссылка должна вести на видео, а не на трек.
cfg["buttons_youtube"] = {
    "btn1_enabled":  "true",
    "btn1_label":    "Открыть видео",
    "btn1_auto_url": "true",
    "btn1_url":      "https://youtube.com",
    "btn2_enabled":  "false",
    "btn2_label":    "Мой канал",
    "btn2_url":      "",
}

def app_icon_url(circle: bool = False) -> str:
    """Ссылка на картинку выбранной иконки приложения для Discord.

    circle=True — версия для маленького кружка рядом с обложкой. Он обрезает
    картинку ровно по вписанной окружности, срезая углы квадратной плашки, а
    вместе с ними и уголок, ради которого у каждой иконки два варианта. В
    icons/discord/ лежит та же иконка, уменьшенная так, чтобы целиком попасть
    в круг (build-icons.py, fit_for_circle).

    Пустая строка означает «иконки нет» — вызывающий должен молча обойтись без
    неё, а не подставлять заглушку: неверная ссылка в large_image/small_image
    даёт у Discord пустой серый квадрат, что хуже отсутствия картинки.
    """
    base    = (cfg["settings"].get("icon_asset_base") or ICON_ASSET_BASE).strip()
    variant = (cfg["settings"].get("icon_variant") or "").strip()
    if not base or not variant:
        return ""
    if not base.endswith("/"):
        base += "/"
    if circle:
        return f"{base}discord/{variant}-circle.png"
    return f"{base}source/{variant}.png"


def load_config():
    global DISCORD_CLIENT_ID, WEBSOCKET_PORT, UPDATE_INTERVAL, BROADCAST_PORT
    if os.path.exists(CONFIG_FILE):
        try:
            cfg.read(CONFIG_FILE, encoding="utf-8")
        except UnicodeDecodeError:
            # Конфиги, записанные прошлыми версиями, лежат в системной кодировке
            # (cp1251 на русской Windows) — в них кириллические подписи кнопок.
            # Читаем как есть, а ближайшее сохранение перепишет файл в UTF-8.
            cfg.read(CONFIG_FILE, encoding="cp1251")
    DISCORD_CLIENT_ID = cfg["settings"].get("discord_client_id", DISCORD_CLIENT_ID)
    WEBSOCKET_PORT    = int(cfg["settings"].get("websocket_port",    WEBSOCKET_PORT))
    BROADCAST_PORT    = int(cfg["settings"].get("broadcast_port",    BROADCAST_PORT))
    UPDATE_INTERVAL   = int(cfg["settings"].get("update_interval",   UPDATE_INTERVAL))

def save_config():
    # Явный UTF-8: без него open() берёт системную кодировку, и конфиг с
    # кириллическими подписями кнопок оказывался в cp1251 — нечитаемым ничем,
    # кроме русской Windows, и ломающимся на любом символе вне неё.
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        cfg.write(f)

load_config()


def get_local_ip() -> str:
    """IP компьютера в локальной сети (для Broadcast API / мобильной синхронизации)."""
    try:
        import socket as _socket
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ── Статистика ────────────────────────────────────────────────────────────────
def _on_stats_ready(monthly: list = None, yearly: list = None):
    """Вызывается из suno_stats когда готова месячная/годовая статистика."""
    if _STATS_AVAILABLE:
        suno_stats.generate_html()

if _STATS_AVAILABLE:
    suno_stats.register_callback(_on_stats_ready)

# ── Состояние для UI ──────────────────────────
state = {
    "title":     "Ожидание трека...",
    "artist":    "—",
    "elapsed":   0,
    "duration":  0,
    "is_paused": True,
    "discord":   False,
    "extension": False,
    "source":    "",       # какой источник сейчас в Discord: "suno" | "youtube" | ""
}


# ══════════════════════════════════════════════
#  ИСТОЧНИКИ АКТИВНОСТИ (Suno / YouTube)
# ══════════════════════════════════════════════
#
# К серверу подключаются два независимых расширения: suno-расширение (extension/)
# и youtube-расширение (youtube-extension/). Каждое шлёт свои данные со своим
# полем "source", а решение, чья активность попадёт в Discord, принимается
# здесь — расширения друг о друге не знают и договориться между собой не могут.
#
# Правило приоритета (выбрано пользователем):
#   играет Suno            → Suno, что бы ни делал YouTube
#   Suno на паузе/молчит   → YouTube, если он играет
#   оба на паузе           → тот, что играл последним
#
# «Молчит» определяется по времени последнего сообщения, а не по разрыву
# WebSocket: расширение держит соединение открытым, даже когда ни одной вкладки
# suno.com/youtube.com нет, поэтому сам факт подключения ничего не говорит о
# том, играет ли там что-то.

# Нет сообщений дольше — источник считается пропавшим. Не меньше 15 секунд:
# service worker расширения в Chromium засыпает по таймауту простоя, и короткие
# паузы в потоке данных — норма, а не признак того, что музыку выключили.
SOURCE_STALE_SEC = 15

sources: dict = {}      # "suno"/"youtube" -> {"data", "updated", "playing_at"}
active_source = None


def _note_source(name: str, data: dict):
    """Запоминает свежие данные источника и момент, когда он последний раз играл."""
    now = time.time()
    rec = sources.setdefault(name, {"data": None, "updated": 0.0, "playing_at": 0.0})
    rec["data"] = data
    rec["updated"] = now
    if not bool(data.get("isPaused", False)):
        rec["playing_at"] = now


no_play_since = None   # момент, с которого ни один источник не играет


def _refresh_play_clock():
    """Отмечает, когда воспроизведение прекратилось во всех источниках.

    Считать «ожиданием» только полное исчезновение источников оказалось
    неправильно: расширение продолжает слать данные, пока открыта вкладка, даже
    если там пауза, — и шаблон ожидания не наступал никогда, пока вкладки не
    закроешь. Отсчёт ведётся именно от прекращения ВОСПРОИЗВЕДЕНИЯ.
    """
    global no_play_since
    live = _live_sources()
    if any(not bool(r["data"].get("isPaused", False)) for r in live.values()):
        no_play_since = None
    elif no_play_since is None:
        no_play_since = time.time()


def _idle_after_seconds() -> int:
    try:
        return max(0, int(cfg["idle"].get("after_seconds", "30")))
    except (ValueError, KeyError):
        return 30


def idle_due() -> bool:
    """Пора показывать шаблон: ничего не играет дольше отсрочки."""
    if no_play_since is None:
        return False
    return (time.time() - no_play_since) >= _idle_after_seconds()


def _live_sources() -> dict:
    now = time.time()
    return {n: r for n, r in sources.items()
            if r["data"] and now - r["updated"] <= SOURCE_STALE_SEC}


def pick_active_source():
    """Возвращает имя источника, чья активность должна показываться в Discord."""
    live = _live_sources()
    if not live:
        return None
    playing = {n: r for n, r in live.items() if not bool(r["data"].get("isPaused", False))}
    if "suno" in playing:
        return "suno"
    if playing:
        return max(playing, key=lambda n: playing[n]["playing_at"])

    # Никто не играет. Здесь важна ЛИПКОСТЬ: сначала пробуем оставить тот
    # источник, что показывается сейчас. Раньше сравнивалось время последнего
    # сообщения — и если открыты вкладка Suno и вкладка YouTube, обе на паузе и
    # ни одна ещё не играла (playing_at у обеих 0), выбор сваливался на «кто
    # прислал последним». Расширения шлют каждые 2 секунды по очереди, поэтому
    # активность в Discord непрерывно мигала между ними.
    if active_source in live:
        return active_source
    return max(live, key=lambda n: live[n]["playing_at"])



# ══════════════════════════════════════════════
#  ЛОГИКА ИНТЕРФЕЙСА
# ══════════════════════════════════════════════

class Api:
    """Операции, которые интерфейс выполняет над бэкендом.

    Раньше класс отдавался в pywebview как js_api и вызывался из JS напрямую.
    Теперь интерфейс — отдельный процесс (Electron), и те же методы публикуются
    по HTTP в start_control_server(). Имена методов и форма ответов сохранены
    один в один: фронтенд из web_ui/index.html переехал в desktop/src/index.html
    почти без правок именно поэтому — не меняй их в одиночку."""

    def get_state(self):
        s = state
        bc_enabled = cfg["settings"].get("broadcast_enabled", "true").lower() == "true"
        return {
            "title":             s["title"],
            "artist":            s["artist"],
            "elapsed":           s["elapsed"],
            "duration":          s["duration"],
            "is_paused":         s["is_paused"],
            "discord":           s["discord"],
            "extension":         s["extension"],
            "broadcast_enabled": bc_enabled,
            "broadcast_count":   len(broadcast_clients),
            "error_count":       len(error_log),
            "source":            s["source"],
        }

    def get_config(self):
        b  = cfg["buttons"] if "buttons" in cfg else {}
        yb = cfg["buttons_youtube"] if "buttons_youtube" in cfg else {}
        idl = cfg["idle"] if "idle" in cfg else {}
        return {
            "idle_enabled":       idl.get("enabled", "true").lower() == "true",
            "idle_after_seconds": idl.get("after_seconds", "30"),
            "idle_activity_type": idl.get("activity_type", "playing"),
            "idle_app_name":      idl.get("app_name", ""),
            "idle_details":       idl.get("details", ""),
            "idle_state":         idl.get("state", ""),
            "idle_large_image":   idl.get("large_image", ""),
            "idle_use_app_icon":  idl.get("use_app_icon", "true").lower() == "true",
            "idle_large_text":    idl.get("large_text", ""),
            "idle_small_text":    idl.get("small_text", ""),
            "idle_show_timer":    idl.get("show_timer", "false").lower() == "true",
            "idle_btn1_enabled":  idl.get("btn1_enabled", "false") == "true",
            "idle_btn1_label":    idl.get("btn1_label", ""),
            "idle_btn1_url":      idl.get("btn1_url", ""),
            "idle_btn2_enabled":  idl.get("btn2_enabled", "false") == "true",
            "idle_btn2_label":    idl.get("btn2_label", ""),
            "idle_btn2_url":      idl.get("btn2_url", ""),
            "app_icon_small":        cfg["settings"].get("app_icon_small", "true").lower() == "true",
            "youtube_enabled":       cfg["settings"].get("youtube_enabled", "true").lower() == "true",
            "youtube_activity_type": cfg["settings"].get("youtube_activity_type", "watching"),
            "youtube_app_name":      cfg["settings"].get("youtube_app_name", "YouTube"),
            "yt_btn1_enabled":       yb.get("btn1_enabled", "true") == "true",
            "yt_btn1_label":         yb.get("btn1_label", "Открыть видео"),
            "yt_btn1_auto_url":      yb.get("btn1_auto_url", "true") == "true",
            "yt_btn1_url":           yb.get("btn1_url", ""),
            "yt_btn2_enabled":       yb.get("btn2_enabled", "false") == "true",
            "yt_btn2_label":         yb.get("btn2_label", "Мой канал"),
            "yt_btn2_url":           yb.get("btn2_url", ""),
            "discord_client_id": cfg["settings"].get("discord_client_id", ""),
            "websocket_port":    cfg["settings"].get("websocket_port", "6969"),
            "update_interval":   cfg["settings"].get("update_interval", "15"),
            "show_elapsed":      cfg["settings"].get("show_elapsed", "true").lower() == "true",
            "autostart_discord": cfg["settings"].get("autostart_discord", "true").lower() == "true",
            "broadcast_enabled": cfg["settings"].get("broadcast_enabled", "true").lower() == "true",
            "broadcast_port":    cfg["settings"].get("broadcast_port", "6970"),
            "local_ip":          get_local_ip(),
            "activity_type":     cfg["settings"].get("activity_type", "playing"),
            "app_name":          cfg["settings"].get("app_name", ""),
            "btn1_enabled":      b.get("btn1_enabled", "false") == "true",
            "btn1_label":        b.get("btn1_label", "Слушать на Suno"),
            "btn1_auto_url":     b.get("btn1_auto_url", "false") == "true",
            "btn1_url":          b.get("btn1_url", "https://suno.com"),
            "btn2_enabled":      b.get("btn2_enabled", "false") == "true",
            "btn2_label":        b.get("btn2_label", "Мой профиль"),
            "btn2_url":          b.get("btn2_url", ""),
        }

    def save_config(self, payload):
        global DISCORD_CLIENT_ID, WEBSOCKET_PORT, UPDATE_INTERVAL, last_update, idle_shown

        # Пустой payload означает, что тело запроса не разобралось (битый JSON,
        # неверная кодировка) — а не «пользователь очистил все поля». Раньше
        # такой запрос молча проходил дальше и переписывал КАЖДЫЙ ключ пустой
        # строкой: одна неудачная отправка стирала Client ID, кнопки и весь
        # шаблон ожидания разом. Форма интерфейса всегда шлёт полный набор
        # полей, поэтому отсутствие обязательных ключей — верный признак сбоя.
        if not isinstance(payload, dict) or "websocket_port" not in payload:
            log_error("⚠️  Настройки не сохранены: пустой или неполный запрос")
            return {"ok": False, "error": "empty payload"}

        new_id = (payload.get("discord_client_id") or "").strip()
        if new_id:
            DISCORD_CLIENT_ID = new_id
            cfg["settings"]["discord_client_id"] = new_id
        try:
            p = int(payload.get("websocket_port"))
            if 1024 <= p <= 65535:
                WEBSOCKET_PORT = p
                cfg["settings"]["websocket_port"] = str(p)
        except (TypeError, ValueError):
            pass
        try:
            i = int(payload.get("update_interval"))
            if 1 <= i <= 300:
                UPDATE_INTERVAL = i
                cfg["settings"]["update_interval"] = str(i)
        except (TypeError, ValueError):
            pass

        cfg["settings"]["show_elapsed"]      = str(bool(payload.get("show_elapsed"))).lower()
        cfg["settings"]["app_icon_small"]    = str(bool(payload.get("app_icon_small"))).lower()
        cfg["settings"]["autostart_discord"] = str(bool(payload.get("autostart_discord"))).lower()
        cfg["settings"]["activity_type"]     = payload.get("activity_type") or "playing"
        cfg["settings"]["app_name"]          = (payload.get("app_name") or "").strip()
        cfg["settings"]["broadcast_enabled"] = str(bool(payload.get("broadcast_enabled"))).lower()
        cfg["settings"]["broadcast_port"]    = str(payload.get("broadcast_port") or "6970").strip()

        if "buttons" not in cfg:
            cfg["buttons"] = {}
        cfg["buttons"]["btn1_enabled"]  = str(bool(payload.get("btn1_enabled"))).lower()
        cfg["buttons"]["btn1_auto_url"] = str(bool(payload.get("btn1_auto_url"))).lower()
        cfg["buttons"]["btn1_label"]    = (payload.get("btn1_label") or "").strip()
        cfg["buttons"]["btn1_url"]      = (payload.get("btn1_url") or "").strip()
        cfg["buttons"]["btn2_enabled"]  = str(bool(payload.get("btn2_enabled"))).lower()
        cfg["buttons"]["btn2_label"]    = (payload.get("btn2_label") or "").strip()
        cfg["buttons"]["btn2_url"]      = (payload.get("btn2_url") or "").strip()

        cfg["settings"]["youtube_enabled"]       = str(bool(payload.get("youtube_enabled"))).lower()
        cfg["settings"]["youtube_activity_type"] = payload.get("youtube_activity_type") or "watching"
        cfg["settings"]["youtube_app_name"]      = (payload.get("youtube_app_name") or "").strip()

        if "buttons_youtube" not in cfg:
            cfg["buttons_youtube"] = {}
        yb = cfg["buttons_youtube"]
        yb["btn1_enabled"]  = str(bool(payload.get("yt_btn1_enabled"))).lower()
        yb["btn1_auto_url"] = str(bool(payload.get("yt_btn1_auto_url"))).lower()
        yb["btn1_label"]    = (payload.get("yt_btn1_label") or "").strip()
        yb["btn1_url"]      = (payload.get("yt_btn1_url") or "").strip()
        yb["btn2_enabled"]  = str(bool(payload.get("yt_btn2_enabled"))).lower()
        yb["btn2_label"]    = (payload.get("yt_btn2_label") or "").strip()
        yb["btn2_url"]      = (payload.get("yt_btn2_url") or "").strip()

        if "idle" not in cfg:
            cfg["idle"] = {}
        idl = cfg["idle"]
        idl["enabled"]       = str(bool(payload.get("idle_enabled"))).lower()
        try:
            idl["after_seconds"] = str(max(0, min(3600, int(payload.get("idle_after_seconds")))))
        except (TypeError, ValueError):
            pass
        idl["activity_type"] = payload.get("idle_activity_type") or "playing"
        idl["app_name"]      = (payload.get("idle_app_name") or "").strip()
        idl["details"]       = (payload.get("idle_details") or "").strip()
        idl["state"]         = (payload.get("idle_state") or "").strip()
        idl["large_image"]   = (payload.get("idle_large_image") or "").strip()
        idl["use_app_icon"]  = str(bool(payload.get("idle_use_app_icon"))).lower()
        idl["large_text"]    = (payload.get("idle_large_text") or "").strip()
        idl["small_text"]    = (payload.get("idle_small_text") or "").strip()
        idl["show_timer"]    = str(bool(payload.get("idle_show_timer"))).lower()
        idl["btn1_enabled"]  = str(bool(payload.get("idle_btn1_enabled"))).lower()
        idl["btn1_label"]    = (payload.get("idle_btn1_label") or "").strip()
        idl["btn1_url"]      = (payload.get("idle_btn1_url") or "").strip()
        idl["btn2_enabled"]  = str(bool(payload.get("idle_btn2_enabled"))).lower()
        idl["btn2_label"]    = (payload.get("idle_btn2_label") or "").strip()
        idl["btn2_url"]      = (payload.get("idle_btn2_url") or "").strip()

        save_config()

        # Если сейчас показывается шаблон, правки должны быть видны сразу, а не
        # после следующего трека — поэтому сбрасываем флаг «уже показан».
        idle_shown = False

        last_update = 0  # принудительно обновить Discord при следующем треке
        if async_loop and last_data:
            asyncio.run_coroutine_threadsafe(update_presence(last_data), async_loop)
        return {"ok": True}

    def reconnect_discord(self):
        if async_loop:
            asyncio.run_coroutine_threadsafe(_reconnect_discord_async(), async_loop)
        return {"ok": True}

    def set_icon_variant(self, variant: str):
        """Принимает выбранный в интерфейсе вариант иконки.

        Выбор живёт у Electron, но Discord картинку с диска не возьмёт, поэтому
        бэкенду нужно знать имя варианта — из него собирается публичная ссылка
        (app_icon_url). Сброс last_update и idle_shown обязателен: без него
        новая иконка доехала бы до Discord только со следующим треком, а в
        тишине — не доехала бы вовсе, потому что шаблон уже показан.
        """
        global last_update, idle_shown
        variant = (variant or "").strip()
        # Имя подставляется в URL, поэтому пускаем только то, из чего состоят
        # слаги вариантов: путь наружу и любые кавычки исключены.
        if not variant or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", variant):
            return {"ok": False, "error": "bad variant"}
        if cfg["settings"].get("icon_variant") != variant:
            cfg["settings"]["icon_variant"] = variant
            save_config()
            last_update = 0
            idle_shown  = False
            log_event(f"🖼  Иконка приложения: {variant}")
        return {"ok": True}

    def disconnect_discord(self):
        if async_loop:
            asyncio.run_coroutine_threadsafe(_disconnect_discord_async(), async_loop)
        return {"ok": True}

    def get_logs(self):
        combined = [(ts, msg, "error") for ts, msg in error_log] \
                 + [(ts, msg, "info")  for ts, msg in event_log]
        combined.sort(key=lambda t: t[0])
        return [
            {"ts": ts, "time": time.strftime("%H:%M:%S", time.localtime(ts)), "msg": msg, "level": level}
            for ts, msg, level in combined
        ]

    def get_stats_summary(self):
        if not _STATS_AVAILABLE:
            return {"available": False, "count": 0, "time_str": "0м", "months": [], "years": [],
                    "youtube": {"available": False, "count": 0, "time_str": "0м", "months": [], "years": []}}
        month_data = suno_stats.current_month_preview()
        count = len(month_data)
        secs  = sum(t.get("total_listen_seconds", 0) for t in month_data.values())
        h, rem = divmod(int(secs), 3600)
        m, _s  = divmod(rem, 60)
        time_str = f"{h}ч {m}м" if h > 0 else f"{m}м"

        yt = {"available": False, "count": 0, "time_str": "0м", "months": [], "years": []}
        if _YT_STATS_AVAILABLE:
            yt = youtube_stats.summary()
            yt["available"] = True

        return {
            "available": True,
            "count":     count,
            "time_str":  time_str,
            "months":    suno_stats.has_monthly_stats(),
            "years":     suno_stats.has_yearly_stats(),
            "youtube":   yt,
        }

    def generate_stats_html(self, page: str = "suno"):
        """Пересобирает отчёт и отдаёт путь к нужной странице.

        Страницы Suno и YouTube лежат рядом и ссылаются друг на друга кнопкой в
        шапке, поэтому пересобирать надо обе: иначе переход по кнопке открыл бы
        соседнюю страницу в том виде, в каком её оставили в прошлый раз.
        Страница YouTube генерируется первой — кнопка на неё появляется в отчёте
        Suno только если файл уже существует.
        """
        if not _STATS_AVAILABLE:
            return {"ok": False, "path": ""}
        if _YT_STATS_AVAILABLE:
            try:
                youtube_stats.generate_html()
            except Exception as e:
                log_error(f"⚠️  Ошибка генерации отчёта YouTube: {e}")
        suno_stats.generate_html()
        if page == "youtube" and _YT_STATS_AVAILABLE:
            return {"ok": True, "path": youtube_stats.HTML_FILE}
        return {"ok": True, "path": suno_stats.HTML_FILE}


async def shutdown(reason: str = "команда интерфейса"):
    """Снимает статус в Discord и убивает процесс.

    rpc.clear() перед выходом — не косметика: Discord оставляет последний
    presence висеть, пока клиент сам не заметит обрыв сокета, поэтому без
    очистки в профиле ещё какое-то время показывается давно доигравший трек."""
    log_event(f"👋 Остановка ({reason})...")
    if rpc:
        try:
            await rpc.clear()
        except Exception:
            pass
    # sys.exit(0) не годится: shutdown() выполняется в asyncio-потоке, а
    # SystemExit из не-главного потока CPython завершает только этот поток.
    # Процесс при этом оставался жив "призраком" — без интерфейса, но с
    # работающими WebSocket-серверами и Discord RPC, из-за чего казалось, будто
    # приложение закрыто, хотя оно исправно слало данные. os._exit() убивает
    # процесс целиком из любого потока; вся ручная очистка сделана выше.
    os._exit(0)


# ══════════════════════════════════════════════
#  DISCORD RPC
# ══════════════════════════════════════════════

DISCORD_CONNECT_TIMEOUT = 10   # секунд


async def connect_discord(quiet: bool = False):
    """Подключается к локальному Discord, не подвешивая приложение навсегда.

    quiet=True глушит запись в журнал ошибок: сторож discord_watcher() ходит
    сюда раз в DISCORD_RETRY_SEC секунд, и пока Discord просто не запущен, он
    бы забил журнал одной и той же строкой.

    pypresence ходит в Discord через именованный пайп Windows и, если клиент не
    запущен, не отваливается с ошибкой, а ждёт неопределённо долго. Пока
    интерфейс был в этом же процессе, зависание было незаметно — окно и трей
    поднимались отдельно. Теперь интерфейс ждёт control API, поэтому висящий
    connect выглядел бы как «бэкенд не запустился», и нужен явный таймаут.
    """
    global rpc, discord_ok
    try:
        rpc = AioPresence(DISCORD_CLIENT_ID)
        await asyncio.wait_for(rpc.connect(), timeout=DISCORD_CONNECT_TIMEOUT)
        discord_ok = True
        state["discord"] = True
        log_event("✅ Discord подключён!")
        return True
    except asyncio.TimeoutError:
        if not quiet:
            log_error(f"⚠️  Discord не ответил за {DISCORD_CONNECT_TIMEOUT} с — "
                      f"вероятно, клиент не запущен. Подключение произойдёт само, когда вы его откроете.")
        rpc = None
        discord_ok = False
        state["discord"] = False
        return False
    except Exception as e:
        if not quiet:
            log_error(f"⚠️  Discord недоступен: {e}")
        rpc = None
        discord_ok = False
        state["discord"] = False
        return False


# ── Автовосстановление связи ───────────────────────────────────────────────
# Пайп Discord закрывается не только когда клиент выключают: он рвётся при
# перезапуске и автообновлении Discord и при выходе компьютера из сна. Сам по
# себе разрыв ничем себя не выдаёт — pypresence узнаёт о нём только в момент
# отправки. До появления этих двух функций мёртвое соединение жило до ручного
# «Переподключить»: state["discord"] оставался True, интерфейс писал «Discord
# подключён», а в профиле не было ничего (жалоба пользователя 2026-08-31).
DISCORD_RETRY_SEC = 15   # как часто сторож пробует поднять связь заново


def _mark_discord_lost(err) -> None:
    """Пометить соединение мёртвым, чтобы discord_watcher() поднял его заново.

    Сбрасывает заодно idle_shown и last_update: после восстановления активность
    должна выставиться сразу. Без сброса шаблон ожидания считался бы уже
    показанным и не переотправлялся бы уже никогда — активность так и осталась
    бы пустой при полностью живом соединении.
    """
    global rpc, discord_ok, idle_shown, last_update
    log_error(f"⚠️  Связь с Discord потеряна ({err}) — восстанавливаю...")
    if rpc is not None:
        try:
            rpc.close()
        except Exception:
            pass
    rpc = None
    discord_ok = False
    state["discord"] = False
    idle_shown  = False
    last_update = 0


async def discord_watcher():
    """Поднимает связь с Discord, пока она не восстановится.

    Нужен именно фоновый цикл, а не одна попытка при старте: приложение
    штатно запускается раньше Discord (автозагрузка Windows), и одноразовый
    connect оставлял rpc = None до конца сеанса.
    """
    reported = False
    while True:
        await asyncio.sleep(DISCORD_RETRY_SEC)
        if rpc is not None and discord_ok:
            reported = False
            continue
        if cfg["settings"].get("autostart_discord", "true").lower() != "true":
            continue
        reported = not await connect_discord(quiet=reported)


async def _reconnect_discord_async():
    global rpc, discord_ok, idle_shown, last_update
    log_event("🔄 Переподключение к Discord...")
    discord_ok = False
    state["discord"] = False
    # Иначе после ручного переподключения в режиме ожидания шаблон считался бы
    # уже показанным и активность оставалась бы пустой до следующего трека.
    idle_shown  = False
    last_update = 0
    if rpc:
        try:
            await rpc.clear()
            rpc.close()
        except Exception:
            pass
        rpc = None
    await connect_discord()


async def _disconnect_discord_async():
    global rpc, discord_ok
    log_event("✗  Отключение от Discord...")
    discord_ok = False
    state["discord"] = False
    if rpc:
        try:
            await rpc.clear()
            rpc.close()
        except Exception:
            pass
        rpc = None
    print("RPC отключён")


def _fmt_listen_time(secs: int) -> str:
    h, rem = divmod(int(secs), 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}ч {m}м"
    if m > 0:
        return f"{m}м {s}с"
    return f"{s}с"


ACTIVITY_TYPES = {"playing": 0, "listening": 2, "watching": 3}


async def update_presence(data: dict, source: str = "suno"):
    global last_update, rpc
    if rpc is None:
        return
    now = time.time()
    if now - last_update < UPDATE_INTERVAL:
        return
    is_youtube = source == "youtube"
    try:
        title     = (data.get("title")  or ("Видео без названия" if is_youtube else "Неизвестный трек"))[:128]
        artist    = (data.get("artist") or ("YouTube" if is_youtube else "Suno AI"))[:128]
        cover     = data.get("coverUrl") or ""
        duration  = int(data.get("duration") or 0)
        elapsed   = int(data.get("elapsed")  or 0)
        is_paused = bool(data.get("isPaused", False))

        ts_now   = int(time.time())
        ts_start = ts_now - elapsed
        ts_end   = ts_start + duration if duration > 0 else None

        show_elapsed = cfg["settings"].get("show_elapsed", "true").lower() == "true"

        if is_youtube:
            act_key = cfg["settings"].get("youtube_activity_type", "watching")
        else:
            act_key = cfg["settings"].get("activity_type", "playing")
        act_type = ACTIVITY_TYPES.get(act_key, 0)

        # ── Третья строка (large_text): сколько раз и сколько времени ───────
        # У каждого источника своя статистика и своя формулировка — считать
        # видео «прослушиваниями» в общем топе пользователь не захотел.
        large_text_val = title
        try:
            if is_youtube and _YT_STATS_AVAILABLE:
                st = youtube_stats.get_video_stats(
                    video_url = data.get("trackUrl") or data.get("url") or "",
                    title     = title,
                    channel   = artist,
                )
                if st["count"] > 0:
                    large_text_val = f"▶ {st['count']}× · {_fmt_listen_time(st['total_listen_seconds'])} всего"
            elif not is_youtube and _STATS_AVAILABLE:
                st = suno_stats.get_track_stats(
                    track_url = data.get("trackUrl") or data.get("url") or "",
                    title     = title,
                    artist    = artist,
                )
                if st["count"] > 0:
                    large_text_val = f"🎧 {st['count']}× · {_fmt_listen_time(st['total_listen_seconds'])} всего"
        except Exception:
            pass

        # Имя в шапке: без подмены у видео с YouTube было бы написано
        # «Смотрит Suno AI» — имя Discord-приложения, к которому подключён RPC.
        # Discord принимает поле name в SET_ACTIVITY и показывает именно его.
        name_override = (cfg["settings"].get(
            "youtube_app_name" if is_youtube else "app_name", "") or "").strip()

        kwargs = dict(
            activity_type=act_type,
            # У видео вторая строка — просто название канала: приписка «by»
            # уместна для исполнителя трека, но не для канала.
            details=title,
            state=artist if is_youtube else f"by {artist}",
            large_text=large_text_val,
            small_text="YouTube" if is_youtube else "Suno AI",
            large_image=cover if cover else (
                "https://www.youtube.com/favicon.ico" if is_youtube else "https://suno.com/favicon.ico"),
        )

        # Маленький кружок поверх обложки — иконка приложения. Discord рисует
        # его ТОЛЬКО когда задан small_image: одного small_text для этого мало,
        # и до появления этой строки подпись «Suno AI» никуда не попадала.
        if cfg["settings"].get("app_icon_small", "true").lower() == "true":
            icon = app_icon_url(circle=True)
            if icon:
                kwargs["small_image"] = icon
        if name_override:
            kwargs["name"] = name_override[:128]

        if show_elapsed:
            if not is_paused and ts_end:
                kwargs["start"] = ts_start
                kwargs["end"]   = ts_end
            else:
                kwargs["start"] = ts_start

        # Кнопки — своя секция конфига на каждый источник: под видео нужна
        # подпись «Открыть видео» и авто-ссылка на ролик, а не на трек Suno.
        buttons = []
        btn_section = "buttons_youtube" if is_youtube else "buttons"
        b = cfg[btn_section] if btn_section in cfg else {}
        track_url = data.get("trackUrl") or ""

        if b.get("btn1_enabled", "false") == "true":
            lbl = (b.get("btn1_label") or "Кнопка 1")[:32]
            if b.get("btn1_auto_url", "false") == "true":
                url = track_url  # авто: ссылка на текущий трек/видео
            else:
                url = b.get("btn1_url", "").strip()
            if url:
                buttons.append({"label": lbl, "url": url})

        if b.get("btn2_enabled", "false") == "true":
            lbl = (b.get("btn2_label") or "Кнопка 2")[:32]
            url = b.get("btn2_url", "").strip()
            if url:
                buttons.append({"label": lbl, "url": url})

        print(f"🔘 Кнопки: {buttons if buttons else 'нет'}")
        if buttons:
            kwargs["buttons"] = buttons

        await rpc.update(**kwargs)
        last_update = now

        sym = "⏸" if is_paused else "▶"
        me, se = divmod(elapsed, 60)
        md, sd = divmod(duration, 60)
        tag = "YT" if is_youtube else "SUNO"
        print(f"{sym}  [{tag}] {title} — {artist}  [{me}:{se:02d} / {md}:{sd:02d}]")

    except Exception as e:
        # Ошибка отправки — почти всегда закрытый пайп. Поднимать связь прямо
        # здесь нельзя: connect висит до DISCORD_CONNECT_TIMEOUT и подвесил бы
        # вместе с собой обработку сообщений расширения. Этим занят сторож.
        _mark_discord_lost(e)


# ══════════════════════════════════════════════
#  BROADCAST API (подписчики)
# ══════════════════════════════════════════════

async def broadcast(payload: dict):
    """Разослать данные всем подписчикам Broadcast API."""
    if not broadcast_clients:
        return
    msg = json.dumps(payload, ensure_ascii=False)
    dead = set()
    for ws in broadcast_clients:
        try:
            await ws.send(msg)
        except Exception:
            dead.add(ws)
    broadcast_clients.difference_update(dead)


async def handle_subscriber(websocket):
    """Обработчик входящего подписчика на Broadcast API."""
    broadcast_clients.add(websocket)
    addr = websocket.remote_address
    log_event(f"📡 Новый подписчик Broadcast API: {addr[0]}:{addr[1]}  (всего: {len(broadcast_clients)})")
    # Сразу отправить последнее состояние если есть
    if last_data:
        try:
            await websocket.send(json.dumps(_make_broadcast_payload(last_data), ensure_ascii=False))
        except Exception:
            pass
    try:
        await websocket.wait_closed()
    except Exception:
        pass
    broadcast_clients.discard(websocket)
    log_event(f"📡 Подписчик отключился  (осталось: {len(broadcast_clients)})")


def _make_broadcast_payload(data: dict, source: str = "suno") -> dict:
    return {
        "event":     "track_update",
        # Подписчики (оверлей и т.п.) по этому полю понимают, что играет:
        # аудио-данные для FFT приходят только от Suno, у видео их нет.
        "source":    source,
        "title":     data.get("title")    or "Неизвестный трек",
        "artist":    data.get("artist")   or "Suno AI",
        "url":       data.get("url")      or "",
        "coverUrl":  data.get("coverUrl") or "",
        "duration":  int(data.get("duration") or 0),
        "elapsed":   int(data.get("elapsed")  or 0),
        "isPaused":  bool(data.get("isPaused", False)),
        "timestamp": int(time.time()),
    }


# ══════════════════════════════════════════════
#  WEBSOCKET СЕРВЕР (приём от расширения)
# ══════════════════════════════════════════════


# ══════════════════════════════════════════════
#  ШАБЛОН ОЖИДАНИЯ
# ══════════════════════════════════════════════

IDLE_CHECK_SEC = 5    # как часто проверять, не пропали ли источники
idle_shown = False    # шаблон уже выставлен — не переставлять его каждые 5 секунд
idle_shown_at = 0.0   # когда именно выставлен: раз в минуту шаблон шлётся заново
# Перезапуск Discord во время тишины иначе проходил незамеченным: активности в
# профиле уже нет, а приложение молчит и потому не узнаёт о закрытом пайпе.
# Повторная отправка того же шаблона возвращает активность и служит проверкой
# связи. Раз в минуту — Discord разрешает пять обновлений за двадцать секунд.
IDLE_REFRESH_SEC = 60


def _idle_placeholders() -> dict:
    """Значения для подстановки в поля шаблона.

    Считаются на момент показа, а не один раз при старте: пользователь может
    просидеть в ожидании час, за который статистика успеет измениться.
    """
    tracks, secs, last_title, last_artist = 0, 0, "", ""
    if _STATS_AVAILABLE:
        try:
            month = suno_stats.current_month_preview()
            tracks = len(month)
            secs = sum(t.get("total_listen_seconds", 0) for t in month.values())
        except Exception:
            pass
    if last_data:
        last_title  = last_data.get("title")  or ""
        last_artist = last_data.get("artist") or ""
    h, rem = divmod(int(secs), 3600)
    m, _s = divmod(rem, 60)
    return {
        "tracks":      str(tracks),
        "hours":       f"{h}ч {m}м" if h > 0 else f"{m}м",
        "last_track":  last_title,
        "last_artist": last_artist,
    }


def _fill_template(text: str, values: dict) -> str:
    """Подставляет {tracks} и прочее. Неизвестные фигурные скобки не трогаем:
    str.format() на них падал бы KeyError и ронял весь показ шаблона из-за
    случайной скобки в тексте пользователя.
    """
    out = text or ""
    for key, val in values.items():
        out = out.replace("{" + key + "}", val)
    return out


async def apply_idle_presence():
    """Показывает шаблон ожидания или снимает активность совсем.

    Вызывается только когда pick_active_source() вернул None. Отдельно от
    update_presence(): та троттлится по UPDATE_INTERVAL и завязана на данные
    источника, а здесь показывать нечего — нужен разовый показ.
    """
    global last_update
    if rpc is None:
        return False

    if cfg["idle"].get("enabled", "true").lower() != "true":
        # Шаблон выключен — честно снимаем активность. Оставлять висеть трек,
        # который давно доиграл, хуже, чем не показывать ничего.
        try:
            await rpc.clear()
            log_event("💤 Источников нет — активность снята")
            return True
        except Exception as e:
            _mark_discord_lost(f"снятие активности: {e}")
            return False

    i = cfg["idle"]
    values = _idle_placeholders()
    kwargs = dict(
        activity_type=ACTIVITY_TYPES.get(i.get("activity_type", "playing"), 0),
        details=_fill_template(i.get("details", ""), values)[:128] or None,
        state=_fill_template(i.get("state", ""), values)[:128] or None,
    )

    name = _fill_template(i.get("app_name", ""), values).strip()
    if name:
        kwargs["name"] = name[:128]

    # Картинка задаётся ссылкой или ключом ассета из Discord Developer Portal:
    # локальные файлы приложения сюда не подходят — Discord тянет изображение
    # сам и до диска пользователя не достаёт.
    # Включённая галочка «взять иконку приложения» ГЛАВНЕЕ поля с картинкой.
    # Обратный приоритет выглядит логично на бумаге, но на деле поле почти у
    # всех непустое — там годами лежит ключ ассета из Developer Portal, о
    # котором никто не помнит. Пользователь ставит галочку, видит в Discord
    # прежнюю стандартную картинку и не понимает, почему (жалоба 2026-09-01).
    # Поле остаётся способом задать свою картинку — при снятой галочке.
    if i.get("use_app_icon", "true").lower() == "true":
        img = app_icon_url() or (i.get("large_image", "") or "").strip()
    else:
        img = (i.get("large_image", "") or "").strip()
    if img:
        kwargs["large_image"] = img
        lt = _fill_template(i.get("large_text", ""), values).strip()
        if lt:
            kwargs["large_text"] = lt[:128]
    st = _fill_template(i.get("small_text", ""), values).strip()
    if st:
        kwargs["small_text"] = st[:128]

    if i.get("show_timer", "false").lower() == "true":
        kwargs["start"] = int(time.time())

    buttons = []
    for n in ("btn1", "btn2"):
        if i.get(f"{n}_enabled", "false") == "true":
            url = (i.get(f"{n}_url", "") or "").strip()
            lbl = _fill_template(i.get(f"{n}_label", ""), values).strip()[:32]
            if url and lbl:
                buttons.append({"label": lbl, "url": url})
    if buttons:
        kwargs["buttons"] = buttons

    try:
        await rpc.update(**kwargs)
        last_update = time.time()
        log_event(f"💤 Шаблон ожидания: {kwargs.get('details') or '—'}")
        return True
    except Exception as e:
        # Раньше здесь был только лог, и закрытый пайп оставался незамеченным:
        # ровно так связь с Discord и умирала молча. Отправка шаблона — самая
        # частая, а в тишине и единственная точка, где обрыв вообще заметен.
        _mark_discord_lost(f"шаблон ожидания: {e}")
        return False


async def idle_watcher():
    """Следит за исчезновением источников.

    Нужен именно фоновый цикл: обычный путь обновления активности событийный —
    он выполняется, когда расширение прислало данные. Когда присылать перестают,
    не срабатывает ничего, и без этого сторожа в Discord так и висел бы
    последний трек.
    """
    global active_source, idle_shown, idle_shown_at, last_update
    while True:
        await asyncio.sleep(IDLE_CHECK_SEC)
        try:
            _refresh_play_clock()

            if not idle_due():
                if idle_shown:
                    # Воспроизведение вернулось — показать трек немедленно,
                    # не дожидаясь UPDATE_INTERVAL после шаблона.
                    idle_shown = False
                    last_update = 0
                continue

            if pick_active_source() is None and active_source is not None:
                # Источники не просто молчат, а исчезли совсем — чистим то, что
                # показывает окно приложения.
                active_source = None
                state["title"]     = "Ожидание трека..."
                state["artist"]    = "—"
                state["is_paused"] = True
                state["elapsed"]   = 0
                state["duration"]  = 0
            state["source"] = "idle"

            if idle_shown and time.time() - idle_shown_at < IDLE_REFRESH_SEC:
                continue
            # Флаг ставим только на успешной отправке: иначе оборванная связь
            # считалась бы показанным шаблоном и повтора уже не случилось бы.
            if await apply_idle_presence():
                idle_shown    = True
                idle_shown_at = time.time()
        except Exception as e:
            log_error(f"⚠️  Ошибка сторожа ожидания: {e}")


async def process_source_update(data: dict):
    """Принимает состояние одного источника и обновляет всё остальное.

    Вызывается из ДВУХ транспортов: WebSocket (suno-расширение) и HTTP
    (youtube-расширение, см. handle_source_update). Логика одна и та же —
    держать её в одном месте обязательно, иначе арбитраж и статистика начнут
    расходиться между источниками.
    """
    global last_data, active_source, last_update, idle_shown

    # Расширения не знают друг о друге: каждое просто шлёт своё состояние со
    # своим "source", а кто попадёт в Discord, решает pick_active_source().
    source = (data.get("source") or "suno").lower()
    if source not in ("suno", "youtube"):
        source = "suno"

    if source == "youtube" and \
            cfg["settings"].get("youtube_enabled", "true").lower() != "true":
        return

    paused = bool(data.get("isPaused", False))

    # ── Статистика: у каждого источника своя ────────────────────────────────
    # Пишем ДО арбитража и независимо от него: если ты слушаешь музыку, а видео
    # параллельно доигрывает, просмотр всё равно твой — то, что его не видно в
    # Discord, статистики не касается.
    if not paused:
        try:
            if source == "suno" and _STATS_AVAILABLE:
                suno_stats.on_track_update(
                    title     = data.get("title")    or "",
                    artist    = data.get("artist")   or "",
                    cover_url = data.get("coverUrl") or "",
                    track_url = data.get("trackUrl") or "",
                    elapsed   = int(data.get("elapsed")  or 0),
                    duration  = int(data.get("duration") or 0),
                    is_paused = False,
                )
            elif source == "youtube" and _YT_STATS_AVAILABLE:
                youtube_stats.on_video_update(
                    title     = data.get("title")    or "",
                    channel   = data.get("artist")   or "",
                    thumb_url = data.get("coverUrl") or "",
                    video_url = data.get("trackUrl") or "",
                    elapsed   = int(data.get("elapsed")  or 0),
                    duration  = int(data.get("duration") or 0),
                    is_paused = False,
                )
        except Exception as e:
            log_error(f"⚠️  Ошибка статистики ({source}): {e}")

    # ── Арбитраж источников ─────────────────────────────────────────────────
    _note_source(source, data)
    _refresh_play_clock()
    new_active = pick_active_source()
    if new_active != active_source:
        if new_active:
            log_event(f"🔀 Активность в Discord: {new_active}")
        active_source = new_active
        # Переключение источника показываем сразу: иначе Discord до
        # UPDATE_INTERVAL секунд держал бы активность того, что уже не играет,
        # и переход выглядел бы залипшим.
        last_update = 0

    if not active_source:
        return

    if idle_shown and not idle_due():
        # Вышли из ожидания — показать трек немедленно, не дожидаясь
        # UPDATE_INTERVAL, иначе поверх шаблона он появился бы с задержкой.
        #
        # Условие «and not idle_due()» обязательно: без него флаг сбрасывало
        # ЛЮБОЕ входящее обновление, включая приходящие раз в две секунды от
        # источника на паузе. Сторож тут же считал шаблон непоказанным и слал
        # его в Discord заново — каждые IDLE_CHECK_SEC секунд, бесконечно.
        idle_shown = False
        last_update = 0

    adata = sources[active_source]["data"]
    last_data = adata

    state["title"]     = adata.get("title")  or "Неизвестный трек"
    state["artist"]    = adata.get("artist") or "—"
    state["elapsed"]   = int(adata.get("elapsed")  or 0)
    state["duration"]  = int(adata.get("duration") or 0)
    state["is_paused"] = bool(adata.get("isPaused", False))
    state["source"]    = "idle" if idle_due() else active_source
    # Расширение может общаться и по HTTP (см. handle_source_update), а тогда
    # счётчик WebSocket-клиентов о нём ничего не знает — без этой строки
    # интерфейс писал бы «расширение не подключено» при работающем YouTube.
    state["extension"] = True

    if idle_due():
        # Активностью владеет шаблон. Без этой отсечки приходящие раз в две
        # секунды обновления поставленного на паузу трека затирали бы его.
        return

    await update_presence(adata, active_source)

    if cfg["settings"].get("broadcast_enabled", "true").lower() == "true" and broadcast_clients:
        await broadcast(_make_broadcast_payload(adata, active_source))


async def handle_client(websocket):
    global last_data, ws_clients, active_source, last_update
    ws_clients += 1
    state["extension"] = True
    log_event("🔌 Расширение подключилось!")

    broadcast_enabled = cfg["settings"].get("broadcast_enabled", "true").lower() == "true"

    try:
        async for message in websocket:
            try:
                data = json.loads(message)

                # ── Пинг для авто-задержки звука (синхронизация с визуалом) ──
                # Расширение меряет RTT, чтобы само подстроить DelayNode под
                # текущую задержку конвейера — см. extension/background.js.
                if data.get("type") == "PING":
                    await websocket.send(json.dumps({"type": "PONG", "ts": data.get("ts")}))
                    continue

                # ── Аудио данные (бас/мид/хай, посчитанные по реальным Гц
                # в offscreen.js, где известна настоящая sampleRate) — быстрый
                # путь, только бродкаст ──
                if data.get("type") == "AUDIO_DATA":
                    if broadcast_enabled and broadcast_clients:
                        await broadcast({
                            "event":    "audio_data",
                            "bass":     data.get("bass", 0),
                            "mid":      data.get("mid", 0),
                            "high":     data.get("high", 0),
                            "volume":   data.get("volume", 0),
                            "spectrum": data.get("spectrum", []),
                        })
                    continue

                # ── Данные трека / видео ─────────────────────────────────
                await process_source_update(data)

            except json.JSONDecodeError:
                pass
    except Exception:
        pass

    ws_clients -= 1
    if ws_clients <= 0:
        ws_clients = 0
        # Если по HTTP всё ещё приходят данные (youtube-расширение), считать
        # расширения отключёнными нельзя — оно живо, просто без WebSocket.
        state["extension"] = bool(_live_sources())
        state["title"]     = "Ожидание трека..."
        state["artist"]    = "—"
        state["is_paused"] = True
        state["source"]    = ""
        sources.clear()
        active_source = None
    log_event("🔌 Расширение отключилось")


async def ws_main():
    broadcast_enabled = cfg["settings"].get("broadcast_enabled", "true").lower() == "true"
    log_event(f"🌐 WebSocket (расширение) запущен на порту {WEBSOCKET_PORT}")
    async with websockets.serve(handle_client, "localhost", WEBSOCKET_PORT):
        if broadcast_enabled:
            log_event(f"📡 Broadcast API запущен на порту {BROADCAST_PORT}  (0.0.0.0 — доступен по сети)")
            async with websockets.serve(handle_subscriber, "0.0.0.0", BROADCAST_PORT):
                await asyncio.Future()
        else:
            await asyncio.Future()



# ══════════════════════════════════════════════
#  CONTROL API (для Electron-интерфейса desktop/)
# ══════════════════════════════════════════════

async def start_control_server():
    """HTTP API, через который Electron-интерфейс управляет бэкендом.

    Слушает ТОЛЬКО 127.0.0.1, в отличие от мобильной синхронизации на
    0.0.0.0:6971. Разница принципиальная: здесь есть смена конфига (включая
    Discord Client ID) и остановка приложения — отдавать это в локальную сеть
    нельзя. Если понадобится доступ с другого устройства, это должен быть
    отдельный эндпоинт с авторизацией, а не расширение биндинга у этого.

    Методы 1:1 повторяют класс Api — фронтенд вызывает их по именам
    (/api/get_state, /api/save_config, ...), поэтому добавление метода в Api
    автоматически ничего не публикует: маршрут надо завести и здесь.
    """
    if not _AIOHTTP_AVAILABLE:
        log_error("❌ aiohttp не найден — интерфейс не сможет подключиться. "
                  "Установите: pip install aiohttp")
        return

    api = Api()

    async def _body(request):
        try:
            return await request.json()
        except Exception:
            return {}

    async def h_ping(request):
        # Electron ждёт готовности бэкенда именно по этому эндпоинту, прежде чем
        # показывать окно — иначе интерфейс успевает отрисоваться раньше сервера
        # и первый же get_state падает с ECONNREFUSED.
        return aiohttp_web.json_response({"ok": True, "pid": os.getpid()})

    async def h_get_state(request):
        return aiohttp_web.json_response(api.get_state())

    async def h_get_config(request):
        return aiohttp_web.json_response(api.get_config())

    async def h_save_config(request):
        return aiohttp_web.json_response(api.save_config(await _body(request)))

    async def h_reconnect(request):
        return aiohttp_web.json_response(api.reconnect_discord())

    async def h_set_icon(request):
        body = await _body(request)
        return aiohttp_web.json_response(api.set_icon_variant(body.get("variant") or ""))

    async def h_disconnect(request):
        return aiohttp_web.json_response(api.disconnect_discord())

    async def h_get_logs(request):
        return aiohttp_web.json_response(api.get_logs())

    async def h_stats_summary(request):
        return aiohttp_web.json_response(api.get_stats_summary())

    async def h_stats_html(request):
        body = await _body(request)
        return aiohttp_web.json_response(api.generate_stats_html(body.get("page") or "suno"))

    async def h_source_update(request):
        # Транспорт для youtube-расширения. Через WebSocket оно ходить не может:
        # Firefox не выпускает ws:// из контекста расширения — либо блокирует
        # запрос, либо молча повышает его до wss:// и упирается в TLS на обычном
        # сервере (проверено на Firefox 154). Обычный HTTP на localhost при этом
        # проходит, поэтому здесь тот же поток данных, только POST'ом.
        await process_source_update(await _body(request))
        return aiohttp_web.json_response({"ok": True})

    async def h_quit(request):
        # Ответ надо успеть отдать до os._exit(), иначе Electron видит обрыв
        # соединения и не может отличить штатный выход от падения бэкенда.
        asyncio.get_running_loop().call_later(
            0.2, lambda: asyncio.ensure_future(shutdown("команда интерфейса")))
        return aiohttp_web.json_response({"ok": True})

    # Порт слушает только 127.0.0.1, но этого мало: на localhost может зайти и
    # любая открытая в браузере страница. Простые POST-запросы уходят на сервер
    # даже без CORS (браузер прячет только ОТВЕТ), так что без проверки чужая
    # вкладка могла бы переписать конфиг или закрыть приложение.
    #
    # Отличаем своих по заголовку Origin: у расширений это moz-extension://
    # или chrome-extension://, у обычных страниц — http(s)://, а нативные
    # клиенты (интерфейс Electron, curl) Origin не шлют вовсе.
    ALLOWED_ORIGIN_PREFIXES = ("moz-extension://", "chrome-extension://",
                               "safari-web-extension://")

    @aiohttp_web.middleware
    async def origin_guard(request, handler):
        origin = request.headers.get("Origin")
        if origin and not origin.startswith(ALLOWED_ORIGIN_PREFIXES):
            log_error(f"⛔ Запрос к control API отклонён, Origin={origin}")
            return aiohttp_web.json_response({"ok": False, "error": "origin not allowed"},
                                             status=403)
        resp = await handler(request)
        if origin:
            # Эхо конкретного Origin, а не «*»: разрешение остаётся именным.
            resp.headers["Access-Control-Allow-Origin"]  = origin
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    app = aiohttp_web.Application(middlewares=[origin_guard])
    app.router.add_get ("/api/ping",              h_ping)
    app.router.add_get ("/api/get_state",         h_get_state)
    app.router.add_get ("/api/get_config",        h_get_config)
    app.router.add_post("/api/save_config",       h_save_config)
    app.router.add_post("/api/reconnect_discord", h_reconnect)
    app.router.add_post("/api/set_icon_variant",  h_set_icon)
    app.router.add_post("/api/disconnect_discord", h_disconnect)
    app.router.add_get ("/api/get_logs",          h_get_logs)
    app.router.add_get ("/api/get_stats_summary", h_stats_summary)
    app.router.add_post("/api/generate_stats_html", h_stats_html)
    app.router.add_post("/api/quit",              h_quit)
    app.router.add_post("/api/source_update",     h_source_update)
    app.router.add_route("OPTIONS", "/api/source_update", lambda r: aiohttp_web.Response())

    runner = aiohttp_web.AppRunner(app)
    await runner.setup()
    await aiohttp_web.TCPSite(runner, "127.0.0.1", CONTROL_PORT).start()
    log_event(f"🖥  Control API запущен на http://127.0.0.1:{CONTROL_PORT}")
    await asyncio.Future()


def watch_parent_process():
    """Завершает бэкенд, когда умирает запустивший его Electron.

    Electron отдаёт бэкенду stdin пайпом и при штатном выходе сначала дёргает
    /api/quit. Но если родитель падает или его снимают через диспетчер задач,
    команды не будет — а бэкенд daemon-потоками продолжит держать порты и слать
    presence в Discord, невидимый для пользователя (ровно тот "процесс-призрак",
    из-за которого раньше приходилось искать SunoRPC.exe в диспетчере задач).
    Смерть родителя закрывает пайп, и читающий поток получает EOF — это самый
    надёжный сигнал, доступный без опроса PID.

    Включается ТОЛЬКО флагом --watch-stdin, который передаёт Electron. Без флага
    слежение опасно: при любом запуске, где stdin не подключён к живому писателю
    (запуск из скрипта, перенаправление из nul, фоновая задача), readline сразу
    возвращает EOF, и бэкенд молча выходит с кодом 0 через доли секунды после
    старта — снаружи это выглядит как «сервер не поднимается» без всякой ошибки.
    """
    if "--watch-stdin" not in sys.argv or sys.stdin is None:
        return

    def _wait_eof():
        try:
            while sys.stdin.readline():
                pass
        except Exception:
            pass
        # Тут уже не до graceful-очистки: родителя нет, и Discord всё равно
        # заметит обрыв. Просто не оставляем процесс висеть.
        os._exit(0)

    threading.Thread(target=_wait_eof, daemon=True).start()


# ══════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════

# ══════════════════════════════════════════════
#  МОБИЛЬНАЯ СИНХРОНИЗАЦИЯ (HTTP сервер для телефона)
# ══════════════════════════════════════════════

async def handle_mobile_sync(request):
    """Принимает историю прослушиваний с телефона → пишет в suno_stats_mobile.json."""
    try:
        body = await request.json()
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

    history = body.get("history", [])
    if not history:
        return aiohttp_web.json_response({"ok": True, "processed": 0})

    processed = 0
    if _STATS_AVAILABLE:
        for entry in history:
            title         = entry.get("title")              or ""
            artist        = entry.get("artist")             or ""
            cover         = entry.get("coverUrl")           or ""
            url           = entry.get("trackUrl")           or ""
            duration      = int(entry.get("duration")       or 0)
            count         = int(entry.get("count")          or 1)
            total_listen  = int(entry.get("totalListenSeconds") or 0)
            daily_seconds = entry.get("dailySeconds")       or {}

            if not title:
                continue

            # Используем record_mobile_play — пишет в отдельный suno_stats_mobile.json
            suno_stats.record_mobile_play(
                title         = title,
                artist        = artist,
                cover_url     = cover,
                track_url     = url,
                elapsed       = (total_listen // count) if count > 0 else max(duration, 30),
                duration      = duration if duration > 0 else 60,
                daily_seconds = daily_seconds,
                count         = count,
            )
            processed += 1

    log_event(f"📱 Мобильная синхронизация: принято {processed} треков")

    # Перегенерируем HTML с мобильными данными
    if _STATS_AVAILABLE and processed > 0:
        try:
            suno_stats.generate_html()
        except Exception as e:
            log_error(f"⚠️  Ошибка генерации HTML: {e}")

    return aiohttp_web.json_response({"ok": True, "processed": processed})


async def start_mobile_sync_server():
    """HTTP сервер для приёма данных с телефона (с CORS)."""
    if not _AIOHTTP_AVAILABLE:
        return

    @aiohttp_web.middleware
    async def cors_middleware(request, handler):
        if request.method == "OPTIONS":
            resp = aiohttp_web.Response()
        else:
            resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    app = aiohttp_web.Application(middlewares=[cors_middleware])
    app.router.add_post("/mobile-sync", handle_mobile_sync)
    app.router.add_route("OPTIONS", "/mobile-sync", lambda r: aiohttp_web.Response())

    runner = aiohttp_web.AppRunner(app)
    await runner.setup()
    site = aiohttp_web.TCPSite(runner, "0.0.0.0", MOBILE_SYNC_PORT)
    await site.start()
    log_event(f"📱 Мобильный синхронизатор запущен на порту {MOBILE_SYNC_PORT} "
              f"(адрес для телефона: http://{get_local_ip()}:{MOBILE_SYNC_PORT})")
    await asyncio.Future()


def run_async_loop():
    global async_loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    async_loop = loop

    async def start():
        # Порядок важен: серверы стартуют сразу, а подключение к Discord уходит
        # в фон. Ждать его здесь нельзя — интерфейс поднимается только после
        # ответа control API, и запущенный без Discord компьютер получал бы
        # пустое окно на все DISCORD_CONNECT_TIMEOUT секунд.
        autostart = cfg["settings"].get("autostart_discord", "true").lower() == "true"
        if autostart:
            asyncio.ensure_future(connect_discord())
        await asyncio.gather(
            ws_main(),
            start_mobile_sync_server(),
            start_control_server(),
            idle_watcher(),
            discord_watcher(),
        )

    loop.run_until_complete(start())


def main():
    # Окон здесь больше нет, поэтому asyncio-циклу не нужен отдельный поток:
    # раньше он уходил в фон только чтобы главный поток отдать GUI-тулкиту.
    # Держать его в главном потоке заодно чинит Ctrl+C при ручной отладке.
    watch_parent_process()
    try:
        run_async_loop()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
