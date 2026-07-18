#!/usr/bin/env python3
"""
suno_rpc.py — Suno AI → Discord Rich Presence + системный трей
Зависимости: pip install pypresence websockets pystray pillow
"""

import asyncio
import json
import time
import sys
import threading
import tkinter as tk
from tkinter import font as tkfont
import io
import os
import configparser
import webbrowser

# ── Стартовое сообщение ──────────────────────────────────────────────────────
print("=" * 50)
print("  🎵 Suno → Discord Rich Presence")
print("=" * 50)
print("Иконка появится в системном трее.")
print("Для остановки — правая кнопка на иконке → Выйти")
# ─────────────────────────────────────────────────────────────────────────────

try:
    import suno_stats
    _STATS_AVAILABLE = True
except ImportError:
    _STATS_AVAILABLE = False
    print("⚠️  suno_stats.py не найден — статистика отключена")

try:
    import websockets
except ImportError:
    print("pip install websockets"); sys.exit(1)

try:
    from aiohttp import web as aiohttp_web
    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False
    print("⚠️  aiohttp не найден — мобильная синхронизация отключена")
    print("   Установите: pip install aiohttp")

MOBILE_SYNC_PORT = 6971

try:
    from pypresence import AioPresence
except ImportError:
    print("pip install pypresence"); sys.exit(1)

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("pip install pystray pillow"); sys.exit(1)

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

# Цвета UI
BG         = "#0e0e10"
BG2        = "#18181b"
BG3        = "#27272a"
BG4        = "#1c1c1f"
ACCENT     = "#8b5cf6"
ACCENT2    = "#a78bfa"
TEXT       = "#f4f4f5"
TEXT2      = "#a1a1aa"
TEXT3      = "#52525b"
GREEN      = "#22c55e"
YELLOW     = "#eab308"
RED        = "#ef4444"

rpc             = None
last_update     = 0
last_data       = None
discord_ok      = False
ws_clients      = 0
async_loop      = None   # ссылка на asyncio loop фонового потока
broadcast_clients: set = set()   # подписчики broadcast API

popup_win   = None
popup_open  = False

# ── Конфиг ────────────────────────────────────
cfg = configparser.ConfigParser()
BROADCAST_PORT    = 6970

cfg["settings"] = {
    "discord_client_id": DISCORD_CLIENT_ID,
    "websocket_port":    str(WEBSOCKET_PORT),
    "broadcast_port":    str(BROADCAST_PORT),
    "broadcast_enabled": "true",
    "update_interval":   str(UPDATE_INTERVAL),
    "show_elapsed":      "true",
    "autostart_discord": "true",
    "activity_type":     "playing",
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

def load_config():
    global DISCORD_CLIENT_ID, WEBSOCKET_PORT, UPDATE_INTERVAL, BROADCAST_PORT
    if os.path.exists(CONFIG_FILE):
        cfg.read(CONFIG_FILE)
    DISCORD_CLIENT_ID = cfg["settings"].get("discord_client_id", DISCORD_CLIENT_ID)
    WEBSOCKET_PORT    = int(cfg["settings"].get("websocket_port",    WEBSOCKET_PORT))
    BROADCAST_PORT    = int(cfg["settings"].get("broadcast_port",    BROADCAST_PORT))
    UPDATE_INTERVAL   = int(cfg["settings"].get("update_interval",   UPDATE_INTERVAL))

def save_config():
    with open(CONFIG_FILE, "w") as f:
        cfg.write(f)

load_config()

# ── Статистика ────────────────────────────────────────────────────────────────
def _on_stats_ready(monthly: list = None, yearly: list = None):
    """Вызывается из suno_stats когда готова месячная/годовая статистика."""
    if _STATS_AVAILABLE:
        suno_stats.generate_html()
    if popup_win:
        popup_win.root.after(0, popup_win.show_stats_button)

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
}

# ══════════════════════════════════════════════
#  ИКОНКА ТРЕЯ
# ══════════════════════════════════════════════

def make_icon(playing: bool, discord: bool) -> Image.Image:
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d    = ImageDraw.Draw(img)
    bg_color = (139, 92, 246, 230) if discord else (60, 60, 70, 200)
    d.rounded_rectangle([0, 0, size-1, size-1], radius=14, fill=bg_color)
    cx, cy = size // 2, size // 2
    if playing:
        pts = [(cx-9, cy-11), (cx+12, cy), (cx-9, cy+11)]
        d.polygon(pts, fill=(255, 255, 255, 255))
    else:
        d.rounded_rectangle([cx-10, cy-10, cx-3, cy+10], radius=2, fill=(255, 255, 255, 200))
        d.rounded_rectangle([cx+3,  cy-10, cx+10, cy+10], radius=2, fill=(255, 255, 255, 200))
    dot_color = (34, 197, 94, 255) if discord else (100, 100, 110, 255)
    d.ellipse([size-16, size-16, size-4, size-4], fill=dot_color)
    return img


# ══════════════════════════════════════════════
#  POPUP ОКНО
# ══════════════════════════════════════════════

def fmt_time(sec: int) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


class PopupWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Suno RPC")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.97)

        self._settings_open = False
        self._w_main = 320
        self._h_main = 210
        self._w_settings = 320
        self._h_settings = 720

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{self._w_main}x{self._h_main}+{sw-self._w_main-16}+{sh-self._h_main-56}")

        self._build()
        self._drag_data = {}
        self.root.bind("<ButtonPress-1>",   self._drag_start)
        self.root.bind("<B1-Motion>",       self._drag_move)

        self.update_ui()

    # ─────────────────────────────────────
    def _build(self):
        r = self.root

        # ══ ГЛАВНАЯ СТРАНИЦА ══
        self.frame_main = tk.Frame(r, bg=BG)
        self.frame_main.pack(fill="both", expand=True)
        self._build_main(self.frame_main)

        # ══ СТРАНИЦА НАСТРОЕК ══
        self.frame_settings = tk.Frame(r, bg=BG)
        self._build_settings(self.frame_settings)

    # ── Главная страница ──────────────────
    def _build_main(self, parent):
        # Шапка
        header = tk.Frame(parent, bg=BG, pady=10, padx=14)
        header.pack(fill="x")

        title_f = tk.Frame(header, bg=BG)
        title_f.pack(side="left")
        tk.Label(title_f, text="SUNO RPC", fg=ACCENT, bg=BG,
                 font=("Courier", 10, "bold")).pack(anchor="w")
        tk.Label(title_f, text="Discord Rich Presence", fg=TEXT3, bg=BG,
                 font=("Courier", 8)).pack(anchor="w")

        btn_row = tk.Frame(header, bg=BG)
        btn_row.pack(side="right")

        # Кнопка настроек ⚙
        btn_cfg = tk.Label(btn_row, text="⚙", fg=TEXT3, bg=BG,
                           font=("Courier", 12), cursor="hand2")
        btn_cfg.pack(side="left", padx=(0, 6))
        btn_cfg.bind("<Button-1>", lambda e: self._show_settings())
        btn_cfg.bind("<Enter>",    lambda e: btn_cfg.configure(fg=ACCENT2))
        btn_cfg.bind("<Leave>",    lambda e: btn_cfg.configure(fg=TEXT3))

        # Кнопка закрыть ✕
        btn_close = tk.Label(btn_row, text="✕", fg=TEXT3, bg=BG,
                             font=("Courier", 12), cursor="hand2")
        btn_close.pack(side="left")
        btn_close.bind("<Button-1>", lambda e: self.hide())
        btn_close.bind("<Enter>",    lambda e: btn_close.configure(fg=RED))
        btn_close.bind("<Leave>",    lambda e: btn_close.configure(fg=TEXT3))

        tk.Frame(parent, bg=BG3, height=1).pack(fill="x")

        # Трек
        track_f = tk.Frame(parent, bg=BG2, padx=14, pady=12)
        track_f.pack(fill="x")

        self.lbl_title = tk.Label(track_f, text="—", fg=TEXT, bg=BG2,
                                  font=("Courier", 11, "bold"), anchor="w", width=30)
        self.lbl_title.pack(anchor="w")

        self.lbl_artist = tk.Label(track_f, text="—", fg=TEXT2, bg=BG2,
                                   font=("Courier", 9), anchor="w")
        self.lbl_artist.pack(anchor="w", pady=(1, 8))

        pb_frame = tk.Frame(track_f, bg=BG3, height=4, bd=0)
        pb_frame.pack(fill="x")
        pb_frame.pack_propagate(False)

        self.pb_fill = tk.Frame(pb_frame, bg=ACCENT, height=4)
        self.pb_fill.place(x=0, y=0, relheight=1, width=0)

        self.lbl_time = tk.Label(track_f, text="0:00 / 0:00", fg=TEXT3, bg=BG2,
                                 font=("Courier", 8))
        self.lbl_time.pack(anchor="e", pady=(4, 0))

        # Статусы
        tk.Frame(parent, bg=BG3, height=1).pack(fill="x")

        status_f = tk.Frame(parent, bg=BG, padx=14, pady=8)
        status_f.pack(fill="x")

        dc_row = tk.Frame(status_f, bg=BG)
        dc_row.pack(fill="x", pady=1)
        self.dot_discord = tk.Label(dc_row, text="●", fg=TEXT3, bg=BG, font=("Courier", 8))
        self.dot_discord.pack(side="left")
        tk.Label(dc_row, text=" Discord", fg=TEXT2, bg=BG, font=("Courier", 9)).pack(side="left")
        self.lbl_discord = tk.Label(dc_row, text="не подключён", fg=TEXT3, bg=BG, font=("Courier", 9))
        self.lbl_discord.pack(side="right")

        ext_row = tk.Frame(status_f, bg=BG)
        ext_row.pack(fill="x", pady=1)
        self.dot_ext = tk.Label(ext_row, text="●", fg=TEXT3, bg=BG, font=("Courier", 8))
        self.dot_ext.pack(side="left")
        tk.Label(ext_row, text=" Расширение", fg=TEXT2, bg=BG, font=("Courier", 9)).pack(side="left")
        self.lbl_ext = tk.Label(ext_row, text="не подключено", fg=TEXT3, bg=BG, font=("Courier", 9))
        self.lbl_ext.pack(side="right")

        bc_row = tk.Frame(status_f, bg=BG)
        bc_row.pack(fill="x", pady=1)
        self.dot_bc = tk.Label(bc_row, text="●", fg=TEXT3, bg=BG, font=("Courier", 8))
        self.dot_bc.pack(side="left")
        tk.Label(bc_row, text=" Broadcast API", fg=TEXT2, bg=BG, font=("Courier", 9)).pack(side="left")
        self.lbl_bc = tk.Label(bc_row, text="0 клиентов", fg=TEXT3, bg=BG, font=("Courier", 9))
        self.lbl_bc.pack(side="right")

        # Кнопки действий
        tk.Frame(parent, bg=BG3, height=1).pack(fill="x")

        action_f = tk.Frame(parent, bg=BG, padx=14, pady=6)
        action_f.pack(fill="x")

        # Переподключить Discord
        btn_reconnect = tk.Label(action_f, text="⟳  Переподключить", fg=TEXT2, bg=BG,
                                 font=("Courier", 9), cursor="hand2")
        btn_reconnect.pack(side="left")
        btn_reconnect.bind("<Button-1>", lambda e: self._reconnect_discord())
        btn_reconnect.bind("<Enter>",    lambda e: btn_reconnect.configure(fg=ACCENT2))
        btn_reconnect.bind("<Leave>",    lambda e: btn_reconnect.configure(fg=TEXT2))

        # Отключить Discord
        self.btn_disconnect = tk.Label(action_f, text="✗  Отключить", fg=TEXT3, bg=BG,
                                       font=("Courier", 9), cursor="hand2")
        self.btn_disconnect.pack(side="right")
        self.btn_disconnect.bind("<Button-1>", lambda e: self._disconnect_discord())
        self.btn_disconnect.bind("<Enter>",    lambda e: self.btn_disconnect.configure(fg=RED))
        self.btn_disconnect.bind("<Leave>",    lambda e: self.btn_disconnect.configure(fg=TEXT3))

        tk.Frame(parent, bg=BG3, height=1).pack(fill="x")

        # Кнопка статистики (скрыта пока нет данных)
        self.btn_stats = tk.Label(parent, text="📊  Статистика", fg=ACCENT, bg=BG,
                                  font=("Courier", 9), pady=5, cursor="hand2")
        self.btn_stats.bind("<Button-1>", lambda e: self._open_stats())
        self.btn_stats.bind("<Enter>",    lambda e: self.btn_stats.configure(fg=ACCENT2))
        self.btn_stats.bind("<Leave>",    lambda e: self.btn_stats.configure(fg=ACCENT))

        # Показываем кнопки сразу если статистика уже есть
        if _STATS_AVAILABLE and (suno_stats.has_monthly_stats() or suno_stats.has_yearly_stats()):
            self.btn_stats.pack()

        tk.Frame(parent, bg=BG3, height=1).pack(fill="x")
        btn_quit = tk.Label(parent, text="Выйти", fg=TEXT3, bg=BG,
                            font=("Courier", 9), pady=7, cursor="hand2")
        btn_quit.pack()
        btn_quit.bind("<Button-1>", lambda e: quit_app())
        btn_quit.bind("<Enter>",    lambda e: btn_quit.configure(fg=RED))
        btn_quit.bind("<Leave>",    lambda e: btn_quit.configure(fg=TEXT3))

    # ── Страница настроек ─────────────────
    def _build_settings(self, parent):
        # Шапка (фиксированная)
        header = tk.Frame(parent, bg=BG, pady=10, padx=14)
        header.pack(fill="x", side="top")

        tk.Label(header, text="НАСТРОЙКИ", fg=ACCENT, bg=BG,
                 font=("Courier", 10, "bold")).pack(side="left")

        btn_back = tk.Label(header, text="← Назад", fg=TEXT3, bg=BG,
                            font=("Courier", 9), cursor="hand2")
        btn_back.pack(side="right")
        btn_back.bind("<Button-1>", lambda e: self._hide_settings())
        btn_back.bind("<Enter>",    lambda e: btn_back.configure(fg=ACCENT2))
        btn_back.bind("<Leave>",    lambda e: btn_back.configure(fg=TEXT3))

        tk.Frame(parent, bg=BG3, height=1).pack(fill="x", side="top")

        # Кнопка сохранить (фиксированная снизу)
        footer = tk.Frame(parent, bg=BG)
        footer.pack(fill="x", side="bottom")
        tk.Frame(footer, bg=BG3, height=1).pack(fill="x")
        self.lbl_save_status = tk.Label(footer, text="", fg=GREEN, bg=BG, font=("Courier", 8))
        self.lbl_save_status.pack(pady=(4, 0))
        btn_save = tk.Label(footer, text="✔  Сохранить", fg=ACCENT, bg=BG,
                            font=("Courier", 10, "bold"), pady=7, cursor="hand2")
        btn_save.pack()
        btn_save.bind("<Button-1>", lambda e: self._save_settings())
        btn_save.bind("<Enter>",    lambda e: btn_save.configure(fg=ACCENT2))
        btn_save.bind("<Leave>",    lambda e: btn_save.configure(fg=ACCENT))

        # Скролл-область
        canvas = tk.Canvas(parent, bg=BG, highlightthickness=0, bd=0)
        scrollbar = tk.Scrollbar(parent, orient="vertical", command=canvas.yview,
                                 bg=BG3, troughcolor=BG, activebackground=ACCENT)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        body = tk.Frame(canvas, bg=BG, padx=14, pady=10)
        body_id = canvas.create_window((0, 0), window=body, anchor="nw")

        def _on_frame_configure(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def _on_canvas_configure(e):
            canvas.itemconfig(body_id, width=e.width)
        body.bind("<Configure>", _on_frame_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        # Мышиный скролл — только когда курсор над canvas или его дочерними виджетами
        def _on_mousewheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        def _bind_mousewheel(e):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_mousewheel(e):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_mousewheel)
        canvas.bind("<Leave>", _unbind_mousewheel)
        body.bind("<Enter>", _bind_mousewheel)
        body.bind("<Leave>", _unbind_mousewheel)

        # ── Discord Client ID ──
        tk.Label(body, text="Discord Client ID", fg=TEXT2, bg=BG,
                 font=("Courier", 9)).pack(anchor="w", pady=(0, 2))

        self._id_var    = tk.StringVar(value=cfg["settings"].get("discord_client_id", ""))
        self._id_shown  = False

        id_row, self.entry_id = self._make_entry_row(
            body, self._id_var, show="•", font=("Courier", 9))
        id_row.pack(fill="x", pady=(0, 10))

        self.btn_eye = tk.Label(id_row, text="👁", fg=TEXT3, bg=BG,
                                font=("Courier", 11), cursor="hand2", padx=4)
        self.btn_eye.pack(side="left")
        self.btn_eye.bind("<Button-1>", lambda e: self._toggle_id_visibility())
        self.btn_eye.bind("<Enter>",    lambda e: self.btn_eye.configure(fg=ACCENT2))
        self.btn_eye.bind("<Leave>",    lambda e: self.btn_eye.configure(fg=TEXT3))

        # ── WebSocket порт ──
        tk.Label(body, text="WebSocket порт", fg=TEXT2, bg=BG,
                 font=("Courier", 9)).pack(anchor="w", pady=(0, 2))

        self._port_var = tk.StringVar(value=cfg["settings"].get("websocket_port", "6969"))
        entry_port = tk.Entry(body, textvariable=self._port_var,
                              bg=BG3, fg=TEXT, insertbackground=ACCENT,
                              relief="flat", font=("Courier", 9),
                              bd=0, highlightthickness=1,
                              highlightbackground=BG3, highlightcolor=ACCENT)
        entry_port.pack(fill="x", ipady=5, ipadx=6, pady=(0, 10))

        # ── Интервал обновления ──
        tk.Label(body, text="Интервал обновления (сек)", fg=TEXT2, bg=BG,
                 font=("Courier", 9)).pack(anchor="w", pady=(0, 2))

        self._interval_var = tk.StringVar(value=cfg["settings"].get("update_interval", "15"))
        entry_interval = tk.Entry(body, textvariable=self._interval_var,
                                  bg=BG3, fg=TEXT, insertbackground=ACCENT,
                                  relief="flat", font=("Courier", 9),
                                  bd=0, highlightthickness=1,
                                  highlightbackground=BG3, highlightcolor=ACCENT)
        entry_interval.pack(fill="x", ipady=5, ipadx=6, pady=(0, 10))

        # ── Переключатели ──
        self._show_elapsed_var = tk.BooleanVar(
            value=cfg["settings"].get("show_elapsed", "true").lower() == "true")
        self._autostart_var = tk.BooleanVar(
            value=cfg["settings"].get("autostart_discord", "true").lower() == "true")

        chk_elapsed = tk.Checkbutton(body, text="Показывать прошедшее время",
                                     variable=self._show_elapsed_var,
                                     bg=BG, fg=TEXT2, selectcolor=BG3,
                                     activebackground=BG, activeforeground=ACCENT2,
                                     font=("Courier", 9), cursor="hand2")
        chk_elapsed.pack(anchor="w", pady=(0, 4))

        chk_auto = tk.Checkbutton(body, text="Автоподключение Discord при старте",
                                  variable=self._autostart_var,
                                  bg=BG, fg=TEXT2, selectcolor=BG3,
                                  activebackground=BG, activeforeground=ACCENT2,
                                  font=("Courier", 9), cursor="hand2")
        chk_auto.pack(anchor="w", pady=(0, 10))

        # ── Broadcast API ──
        tk.Frame(body, bg=BG3, height=1).pack(fill="x", pady=(0, 10))
        tk.Label(body, text="Broadcast API", fg=ACCENT2, bg=BG,
                 font=("Courier", 9, "bold")).pack(anchor="w", pady=(0, 2))
        tk.Label(body, text="Другие приложения получают данные о треке в реальном времени",
                 fg=TEXT3, bg=BG, font=("Courier", 7), justify="left").pack(anchor="w", pady=(0, 6))

        self._broadcast_enabled_var = tk.BooleanVar(
            value=cfg["settings"].get("broadcast_enabled", "true").lower() == "true")

        chk_broadcast = tk.Checkbutton(body, text="Включить Broadcast API",
                                       variable=self._broadcast_enabled_var,
                                       bg=BG, fg=TEXT2, selectcolor=BG3,
                                       activebackground=BG, activeforeground=ACCENT2,
                                       font=("Courier", 9), cursor="hand2")
        chk_broadcast.pack(anchor="w", pady=(0, 6))

        tk.Label(body, text="Порт (по умолчанию 6970)", fg=TEXT2, bg=BG,
                 font=("Courier", 8)).pack(anchor="w")
        self._broadcast_port_var = tk.StringVar(
            value=cfg["settings"].get("broadcast_port", "6970"))
        bp_row, _ = self._make_entry_row(body, self._broadcast_port_var,
                                         font=("Courier", 9))
        bp_row.pack(fill="x", pady=(2, 4))

        # Строка с адресом для копирования
        self._bc_addr_label = tk.Label(body,
            text=f"ws://ВАШ_IP:{self._broadcast_port_var.get()}",
            fg=ACCENT2, bg=BG2, font=("Courier", 8),
            cursor="hand2", padx=6, pady=3, relief="flat")
        self._bc_addr_label.pack(fill="x", pady=(0, 10))
        self._bc_addr_label.bind("<Button-1>", lambda e: (
            self.root.clipboard_clear(),
            self.root.clipboard_append(self._bc_addr_label.cget("text")),
            self._bc_addr_label.configure(text="✔ Скопировано!"),
            self.root.after(1500, lambda: self._bc_addr_label.configure(
                text=f"ws://ВАШ_IP:{self._broadcast_port_var.get()}"))
        ))
        tk.Label(body, text="Нажми на строку выше чтобы скопировать адрес",
                 fg=TEXT3, bg=BG, font=("Courier", 7)).pack(anchor="w", pady=(0, 4))

        # ── Тип активности ──
        tk.Frame(body, bg=BG3, height=1).pack(fill="x", pady=(0, 10))
        tk.Label(body, text="Тип активности Discord", fg=ACCENT2, bg=BG,
                 font=("Courier", 9, "bold")).pack(anchor="w", pady=(0, 6))

        self._activity_type_var = tk.StringVar(
            value=cfg["settings"].get("activity_type", "playing"))

        activity_options = [
            ("playing",   "🎮  Играет  (поддерживает кнопки)"),
            ("listening", "🎵  Слушает  (без кнопок)"),
        ]

        for val, text in activity_options:
            rb = tk.Radiobutton(body, text=text, variable=self._activity_type_var,
                                value=val, bg=BG, fg=TEXT2, selectcolor=BG3,
                                activebackground=BG, activeforeground=ACCENT2,
                                font=("Courier", 9), cursor="hand2")
            rb.pack(anchor="w", pady=1)

        tk.Label(body, text="При выборе «Слушает» кнопки не отображаются — ограничение Discord",
                 fg=TEXT3, bg=BG, font=("Courier", 7), wraplength=280,
                 justify="left").pack(anchor="w", pady=(4, 10))

        # ── Кнопки активности ──
        tk.Frame(body, bg=BG3, height=1).pack(fill="x", pady=(6, 10))
        tk.Label(body, text="Кнопки в активности Discord", fg=ACCENT2, bg=BG,
                 font=("Courier", 9, "bold")).pack(anchor="w", pady=(0, 8))

        b = cfg["buttons"]

        # ── Кнопка 1 ──
        tk.Label(body, text="КНОПКА 1", fg=TEXT3, bg=BG,
                 font=("Courier", 7), ).pack(anchor="w")

        row1_en = tk.Frame(body, bg=BG)
        row1_en.pack(fill="x", pady=(2, 4))
        self._btn1_enabled = tk.BooleanVar(value=b.get("btn1_enabled", "false") == "true")
        tk.Checkbutton(row1_en, text="Включить", variable=self._btn1_enabled,
                       bg=BG, fg=TEXT2, selectcolor=BG3,
                       activebackground=BG, activeforeground=ACCENT2,
                       font=("Courier", 9), cursor="hand2").pack(side="left")

        # Название кнопки 1
        tk.Label(body, text="Название", fg=TEXT3, bg=BG,
                 font=("Courier", 7)).pack(anchor="w")
        self._btn1_label = tk.StringVar(value=b.get("btn1_label", "Слушать на Suno"))
        tk.Entry(body, textvariable=self._btn1_label,
                 bg=BG3, fg=TEXT, insertbackground=ACCENT, relief="flat",
                 font=("Courier", 9), bd=0, highlightthickness=1,
                 highlightbackground=BG3, highlightcolor=ACCENT
                 ).pack(fill="x", ipady=4, ipadx=6, pady=(2, 6))

        # Авто-ссылка на трек
        self._btn1_auto_url = tk.BooleanVar(value=b.get("btn1_auto_url", "false") == "true")

        def _toggle_btn1_url_entry(*_):
            state = "disabled" if self._btn1_auto_url.get() else "normal"
            fg    = TEXT3      if self._btn1_auto_url.get() else TEXT3
            self._btn1_url_entry.config(state=state)

        chk_auto_url = tk.Checkbutton(body, text="Авто: ссылка на текущий трек",
                                      variable=self._btn1_auto_url,
                                      bg=BG, fg=ACCENT2, selectcolor=BG3,
                                      activebackground=BG, activeforeground=ACCENT2,
                                      font=("Courier", 9), cursor="hand2",
                                      command=_toggle_btn1_url_entry)
        chk_auto_url.pack(anchor="w", pady=(0, 3))

        tk.Label(body, text="URL (если авто выключено)", fg=TEXT3, bg=BG,
                 font=("Courier", 7)).pack(anchor="w")
        self._btn1_url = tk.StringVar(value=b.get("btn1_url", "https://suno.com"))
        btn1_url_row, self._btn1_url_entry = self._make_entry_row(
            body, self._btn1_url, font=("Courier", 8), fg=TEXT3)
        btn1_url_row.pack(fill="x", pady=(2, 10))
        _toggle_btn1_url_entry()  # применить начальное состояние

        # ── Кнопка 2 ──
        tk.Frame(body, bg=BG3, height=1).pack(fill="x", pady=(0, 8))
        tk.Label(body, text="КНОПКА 2", fg=TEXT3, bg=BG,
                 font=("Courier", 7)).pack(anchor="w")

        row2_en = tk.Frame(body, bg=BG)
        row2_en.pack(fill="x", pady=(2, 4))
        self._btn2_enabled = tk.BooleanVar(value=b.get("btn2_enabled", "false") == "true")
        tk.Checkbutton(row2_en, text="Включить", variable=self._btn2_enabled,
                       bg=BG, fg=TEXT2, selectcolor=BG3,
                       activebackground=BG, activeforeground=ACCENT2,
                       font=("Courier", 9), cursor="hand2").pack(side="left")

        tk.Label(body, text="Название", fg=TEXT3, bg=BG,
                 font=("Courier", 7)).pack(anchor="w")
        self._btn2_label = tk.StringVar(value=b.get("btn2_label", "Мой профиль"))
        tk.Entry(body, textvariable=self._btn2_label,
                 bg=BG3, fg=TEXT, insertbackground=ACCENT, relief="flat",
                 font=("Courier", 9), bd=0, highlightthickness=1,
                 highlightbackground=BG3, highlightcolor=ACCENT
                 ).pack(fill="x", ipady=4, ipadx=6, pady=(2, 6))

        tk.Label(body, text="URL", fg=TEXT3, bg=BG,
                 font=("Courier", 7)).pack(anchor="w")
        self._btn2_url = tk.StringVar(value=b.get("btn2_url", ""))
        btn2_url_row, _ = self._make_entry_row(
            body, self._btn2_url, font=("Courier", 8), fg=TEXT3)
        btn2_url_row.pack(fill="x", pady=(2, 6))

        tk.Label(body, text="Пустой URL — кнопка не отображается", fg=TEXT3, bg=BG,
                 font=("Courier", 7)).pack(anchor="w", pady=(0, 6))

    # ── Вспомогательный метод: поле ввода + кнопка вставки ──
    def _make_entry_row(self, parent, var, show="", **entry_kwargs):
        """Создаёт Frame с Entry + кнопкой вставки 📋. Возвращает (frame, entry)."""
        row = tk.Frame(parent, bg=BG)
        kw = dict(bg=BG3, fg=TEXT, insertbackground=ACCENT, relief="flat",
                  bd=0, highlightthickness=1, highlightbackground=BG3, highlightcolor=ACCENT)
        kw.update(entry_kwargs)
        if show:
            kw["show"] = show
        entry = tk.Entry(row, textvariable=var, **kw)
        entry.pack(side="left", fill="x", expand=True, ipady=5, ipadx=6)

        def do_paste(e=None):
            try:
                text = self.root.clipboard_get()
                var.set(text.strip())
                entry.icursor("end")
            except tk.TclError:
                pass

        btn_paste = tk.Label(row, text="📋", fg=TEXT3, bg=BG,
                             font=("Courier", 11), cursor="hand2", padx=4)
        btn_paste.pack(side="left")
        btn_paste.bind("<Button-1>", do_paste)
        btn_paste.bind("<Enter>",    lambda e: btn_paste.configure(fg=ACCENT2))
        btn_paste.bind("<Leave>",    lambda e: btn_paste.configure(fg=TEXT3))
        return row, entry

    # ── Переключение видимости Client ID ──
    def _toggle_id_visibility(self):
        self._id_shown = not self._id_shown
        self.entry_id.config(show="" if self._id_shown else "•")
        self.btn_eye.configure(text="🙈" if self._id_shown else "👁")

    # ── Сохранить настройки ───────────────
    def _save_settings(self):
        global DISCORD_CLIENT_ID, WEBSOCKET_PORT, UPDATE_INTERVAL
        new_id       = self._id_var.get().strip()
        new_port     = self._port_var.get().strip()
        new_interval = self._interval_var.get().strip()

        if new_id:
            DISCORD_CLIENT_ID = new_id
            cfg["settings"]["discord_client_id"] = new_id
        try:
            p = int(new_port)
            if 1024 <= p <= 65535:
                WEBSOCKET_PORT = p
                cfg["settings"]["websocket_port"] = str(p)
        except ValueError:
            pass
        try:
            i = int(new_interval)
            if 1 <= i <= 300:
                UPDATE_INTERVAL = i
                cfg["settings"]["update_interval"] = str(i)
        except ValueError:
            pass

        cfg["settings"]["show_elapsed"]      = str(self._show_elapsed_var.get()).lower()
        cfg["settings"]["autostart_discord"] = str(self._autostart_var.get()).lower()
        cfg["settings"]["activity_type"]     = self._activity_type_var.get()
        cfg["settings"]["broadcast_enabled"] = str(self._broadcast_enabled_var.get()).lower()
        cfg["settings"]["broadcast_port"]    = self._broadcast_port_var.get().strip()

        if "buttons" not in cfg:
            cfg["buttons"] = {}
        cfg["buttons"]["btn1_enabled"]  = str(self._btn1_enabled.get()).lower()
        cfg["buttons"]["btn1_auto_url"] = str(self._btn1_auto_url.get()).lower()
        cfg["buttons"]["btn1_label"]    = self._btn1_label.get().strip()
        cfg["buttons"]["btn1_url"]      = self._btn1_url.get().strip()
        cfg["buttons"]["btn2_enabled"]  = str(self._btn2_enabled.get()).lower()
        cfg["buttons"]["btn2_label"]    = self._btn2_label.get().strip()
        cfg["buttons"]["btn2_url"]      = self._btn2_url.get().strip()
        save_config()
        global last_update
        last_update = 0  # принудительно обновить Discord при следующем треке

        # Немедленно применить кнопки если трек уже играет
        if async_loop and last_data:
            asyncio.run_coroutine_threadsafe(update_presence(last_data), async_loop)

        self.lbl_save_status.configure(text="✔ Сохранено!")
        self.root.after(3000, lambda: self.lbl_save_status.configure(text=""))

    # ── Показать/скрыть настройки ─────────
    def _show_settings(self):
        if self._settings_open:
            return
        self._settings_open = True
        self.frame_main.pack_forget()
        self.frame_settings.pack(fill="both", expand=True)
        self.root.geometry(f"{self._w_settings}x{self._h_settings}")

    def _hide_settings(self):
        if not self._settings_open:
            return
        self._settings_open = False
        self.frame_settings.pack_forget()
        self.frame_main.pack(fill="both", expand=True)
        self.root.geometry(f"{self._w_main}x{self._h_main}")

    # ── Переподключить Discord ────────────
    def _reconnect_discord(self):
        self.lbl_discord.configure(text="подключение...", fg=YELLOW)
        self.dot_discord.configure(fg=YELLOW)
        def do_reconnect():
            if async_loop:
                future = asyncio.run_coroutine_threadsafe(_reconnect_discord_async(), async_loop)
                future.result(timeout=10)
        t = threading.Thread(target=do_reconnect, daemon=True)
        t.start()

    # ── Отключить Discord ─────────────────
    def _disconnect_discord(self):
        def do_disconnect():
            if async_loop:
                asyncio.run_coroutine_threadsafe(_disconnect_discord_async(), async_loop)
        t = threading.Thread(target=do_disconnect, daemon=True)
        t.start()

    # ── Обновление UI каждую секунду ─────
    def update_ui(self):
        s = state

        title  = s["title"][:34]  + "…" if len(s["title"])  > 35 else s["title"]
        artist = s["artist"][:38] + "…" if len(s["artist"]) > 39 else s["artist"]
        self.lbl_title.configure(text=title)
        self.lbl_artist.configure(text=artist)

        dur = max(1, s["duration"])
        pct = min(s["elapsed"] / dur, 1.0)
        bar_w = int(292 * pct)
        self.pb_fill.place(width=bar_w)
        self.lbl_time.configure(
            text=f"{fmt_time(s['elapsed'])} / {fmt_time(s['duration'])}"
        )

        self.lbl_title.configure(fg=TEXT2 if s["is_paused"] else TEXT)

        if s["discord"]:
            self.dot_discord.configure(fg=GREEN)
            self.lbl_discord.configure(text="подключён", fg=GREEN)
            self.btn_disconnect.configure(fg=TEXT3)
        else:
            self.dot_discord.configure(fg=TEXT3)
            self.lbl_discord.configure(text="не подключён", fg=TEXT3)

        if s["extension"]:
            self.dot_ext.configure(fg=ACCENT2)
            self.lbl_ext.configure(text="активно", fg=ACCENT2)
        else:
            self.dot_ext.configure(fg=TEXT3)
            self.lbl_ext.configure(text="не подключено", fg=TEXT3)

        bc_enabled = cfg["settings"].get("broadcast_enabled", "true").lower() == "true"
        bc_count = len(broadcast_clients)
        if not bc_enabled:
            self.dot_bc.configure(fg=TEXT3)
            self.lbl_bc.configure(text="выключен", fg=TEXT3)
        elif bc_count > 0:
            n = bc_count
            sfx = "ов" if n >= 5 else ("а" if n >= 2 else "")
            self.dot_bc.configure(fg=GREEN)
            self.lbl_bc.configure(text=f"{n} клиент{sfx}", fg=GREEN)
        else:
            self.dot_bc.configure(fg=YELLOW)
            self.lbl_bc.configure(text="ожидание...", fg=YELLOW)

        self.root.after(1000, self.update_ui)

    def show_stats_button(self):
        """Показывает кнопку статистики когда данные готовы."""
        try:
            self.btn_stats.pack()
        except Exception:
            pass

    def _open_stats(self):
        """Открывает HTML-отчёт в браузере, предварительно обновив его."""
        if _STATS_AVAILABLE:
            suno_stats.generate_html()
            webbrowser.open(suno_stats.HTML_FILE)
        else:
            import tkinter.messagebox as mb
            mb.showinfo("Статистика", "Модуль suno_stats.py не найден рядом со скриптом.")

    def show(self):
        self.root.deiconify()
        self.root.lift()

    def hide(self):
        self.root.withdraw()

    def _drag_start(self, e):
        # Не начинать перетаскивание если кликнули по полю ввода или скроллбару
        if isinstance(e.widget, (tk.Entry, tk.Scrollbar, tk.Text)):
            self._drag_data = {}
            return
        self._drag_data = {"x": e.x, "y": e.y}

    def _drag_move(self, e):
        if not self._drag_data:
            return
        dx = e.x - self._drag_data["x"]
        dy = e.y - self._drag_data["y"]
        x  = self.root.winfo_x() + dx
        y  = self.root.winfo_y() + dy
        self.root.geometry(f"+{x}+{y}")

    def mainloop(self):
        self.root.mainloop()


# ══════════════════════════════════════════════
#  ТРЕЙ
# ══════════════════════════════════════════════

tray_icon = None

def build_menu():
    return pystray.Menu(
        pystray.MenuItem("Открыть",          lambda: toggle_popup(), default=True),
        pystray.MenuItem("📊 Статистика",    lambda: _tray_open_stats()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Переподключить",   lambda: _tray_reconnect()),
        pystray.MenuItem("Отключить RPC",    lambda: _tray_disconnect()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Выйти",            lambda: quit_app()),
    )

def _tray_open_stats():
    if _STATS_AVAILABLE:
        suno_stats.generate_html()
        webbrowser.open(suno_stats.HTML_FILE)

def _tray_reconnect():
    if async_loop:
        asyncio.run_coroutine_threadsafe(_reconnect_discord_async(), async_loop)

def _tray_disconnect():
    if async_loop:
        asyncio.run_coroutine_threadsafe(_disconnect_discord_async(), async_loop)

def toggle_popup():
    global popup_win
    if popup_win is None:
        return
    if popup_win.root.state() == "withdrawn":
        popup_win.show()
    else:
        popup_win.hide()

def refresh_tray():
    global tray_icon
    if tray_icon:
        playing = not state["is_paused"]
        tray_icon.icon = make_icon(playing, state["discord"])

def quit_app():
    global tray_icon, rpc
    print("\n👋 Остановка...")
    if rpc:
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(rpc.clear())
            loop.close()
        except Exception:
            pass
    if tray_icon:
        tray_icon.stop()
    if popup_win:
        popup_win.root.quit()
    sys.exit(0)


# ══════════════════════════════════════════════
#  DISCORD RPC
# ══════════════════════════════════════════════

async def connect_discord():
    global rpc, discord_ok
    try:
        rpc = AioPresence(DISCORD_CLIENT_ID)
        await rpc.connect()
        discord_ok = True
        state["discord"] = True
        print("✅ Discord подключён!")
        refresh_tray()
        return True
    except Exception as e:
        print(f"⚠️  Discord недоступен: {e}")
        discord_ok = False
        state["discord"] = False
        refresh_tray()
        return False


async def _reconnect_discord_async():
    global rpc, discord_ok
    print("🔄 Переподключение к Discord...")
    discord_ok = False
    state["discord"] = False
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
    print("✗  Отключение от Discord...")
    discord_ok = False
    state["discord"] = False
    refresh_tray()
    if rpc:
        try:
            await rpc.clear()
            rpc.close()
        except Exception:
            pass
        rpc = None
    print("RPC отключён")


async def update_presence(data: dict):
    global last_update, rpc
    if rpc is None:
        return
    now = time.time()
    if now - last_update < UPDATE_INTERVAL:
        return
    try:
        title     = (data.get("title")  or "Неизвестный трек")[:128]
        artist    = (data.get("artist") or "Suno AI")[:128]
        cover     = data.get("coverUrl") or ""
        duration  = int(data.get("duration") or 0)
        elapsed   = int(data.get("elapsed")  or 0)
        is_paused = bool(data.get("isPaused", False))

        ts_now   = int(time.time())
        ts_start = ts_now - elapsed
        ts_end   = ts_start + duration if duration > 0 else None

        show_elapsed = cfg["settings"].get("show_elapsed", "true").lower() == "true"

        activity_type_str = cfg["settings"].get("activity_type", "playing")
        act_type = 2 if activity_type_str == "listening" else 0

        # ── Статистика для поля large_text (третья строка) ──────────────────
        large_text_val = title
        if _STATS_AVAILABLE:
            try:
                track_url_for_stats = data.get("trackUrl") or data.get("url") or ""
                stats = suno_stats.get_track_stats(
                    track_url = track_url_for_stats,
                    title     = title,
                    artist    = artist,
                )
                count = stats["count"]
                secs  = stats["total_listen_seconds"]
                if count > 0:
                    h, rem = divmod(secs, 3600)
                    m_s, s_s = divmod(rem, 60)
                    if h > 0:
                        time_str = f"{h}ч {m_s}м"
                    elif m_s > 0:
                        time_str = f"{m_s}м {s_s}с"
                    else:
                        time_str = f"{s_s}с"
                    large_text_val = f"🎧 {count}× · {time_str} всего"
            except Exception:
                pass

        kwargs = dict(
            activity_type=act_type,
            details=title,
            state=f"by {artist}",
            large_text=large_text_val,
            small_text="Suno AI",
            large_image=cover if cover else "https://suno.com/favicon.ico",
        )
        if show_elapsed:
            if not is_paused and ts_end:
                kwargs["start"] = ts_start
                kwargs["end"]   = ts_end
            else:
                kwargs["start"] = ts_start

        # Кнопки
        buttons = []
        b = cfg["buttons"] if "buttons" in cfg else {}
        track_url = data.get("trackUrl") or ""

        if b.get("btn1_enabled", "false") == "true":
            lbl = (b.get("btn1_label") or "Кнопка 1")[:32]
            if b.get("btn1_auto_url", "false") == "true":
                url = track_url  # авто: ссылка на текущий трек
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
        print(f"{sym}  {title} — {artist}  [{me}:{se:02d} / {md}:{sd:02d}]")

    except Exception as e:
        print(f"⚠️  Ошибка RPC: {e}")
        try:
            rpc = AioPresence(DISCORD_CLIENT_ID)
            await rpc.connect()
        except Exception:
            pass


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
    print(f"📡 Новый подписчик Broadcast API: {addr[0]}:{addr[1]}  (всего: {len(broadcast_clients)})")
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
    print(f"📡 Подписчик отключился  (осталось: {len(broadcast_clients)})")


def _make_broadcast_payload(data: dict) -> dict:
    return {
        "event":     "track_update",
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

async def handle_client(websocket):
    global last_data, ws_clients
    ws_clients += 1
    state["extension"] = True
    print("🔌 Расширение подключилось!")
    refresh_tray()

    broadcast_enabled = cfg["settings"].get("broadcast_enabled", "true").lower() == "true"

    try:
        async for message in websocket:
            try:
                data = json.loads(message)

                # ── Аудио данные (FFT) — быстрый путь, только бродкаст ──
                if data.get("type") == "AUDIO_DATA":
                    if broadcast_enabled and broadcast_clients:
                        await broadcast({
                            "event":  "audio_data",
                            "fft":    data.get("fft", []),
                            "volume": data.get("volume", 0),
                        })
                    continue

                # ── Данные трека ─────────────────────────────────────────
                last_data = data

                state["title"]     = data.get("title")  or "Неизвестный трек"
                state["artist"]    = data.get("artist") or "Suno AI"
                state["elapsed"]   = int(data.get("elapsed")  or 0)
                state["duration"]  = int(data.get("duration") or 0)
                state["is_paused"] = bool(data.get("isPaused", False))

                refresh_tray()
                await update_presence(data)

                # ── трекинг статистики ──────────────────────────────────────
                if _STATS_AVAILABLE and not bool(data.get("isPaused", False)):
                    suno_stats.on_track_update(
                        title     = data.get("title")    or "",
                        artist    = data.get("artist")   or "",
                        cover_url = data.get("coverUrl") or "",
                        track_url = data.get("trackUrl") or "",
                        elapsed   = int(data.get("elapsed")  or 0),
                        duration  = int(data.get("duration") or 0),
                        is_paused = False,
                    )

                if broadcast_enabled and broadcast_clients:
                    await broadcast(_make_broadcast_payload(data))

            except json.JSONDecodeError:
                pass
    except Exception:
        pass

    ws_clients -= 1
    if ws_clients <= 0:
        ws_clients = 0
        state["extension"] = False
        state["title"]     = "Ожидание трека..."
        state["artist"]    = "—"
        state["is_paused"] = True
        refresh_tray()
    print("🔌 Расширение отключилось")


async def ws_main():
    broadcast_enabled = cfg["settings"].get("broadcast_enabled", "true").lower() == "true"
    print(f"🌐 WebSocket (расширение) запущен на порту {WEBSOCKET_PORT}")
    async with websockets.serve(handle_client, "localhost", WEBSOCKET_PORT):
        if broadcast_enabled:
            print(f"📡 Broadcast API запущен на порту {BROADCAST_PORT}  (0.0.0.0 — доступен по сети)")
            async with websockets.serve(handle_subscriber, "0.0.0.0", BROADCAST_PORT):
                await asyncio.Future()
        else:
            await asyncio.Future()


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

    print(f"📱 Мобильная синхронизация: принято {processed} треков")

    # Перегенерируем HTML с мобильными данными
    if _STATS_AVAILABLE and processed > 0:
        try:
            suno_stats.generate_html()
        except Exception as e:
            print(f"⚠️  Ошибка генерации HTML: {e}")

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
    print(f"📱 Мобильный синхронизатор запущен на порту {MOBILE_SYNC_PORT}")
    try:
        import socket as _socket
        _s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        _s.connect(("8.8.8.8", 80))
        _local_ip = _s.getsockname()[0]
        _s.close()
    except Exception:
        _local_ip = "127.0.0.1"
    print(f"   Адрес для телефона: http://{_local_ip}:{MOBILE_SYNC_PORT}")
    await asyncio.Future()


def run_async_loop():
    global async_loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    async_loop = loop

    async def start():
        autostart = cfg["settings"].get("autostart_discord", "true").lower() == "true"
        if autostart:
            await connect_discord()
        await asyncio.gather(
            ws_main(),
            start_mobile_sync_server()
        )

    loop.run_until_complete(start())


def main():
    global tray_icon, popup_win

    t = threading.Thread(target=run_async_loop, daemon=True)
    t.start()

    popup_win = PopupWindow()
    popup_win.root.withdraw()

    icon_img  = make_icon(False, False)
    tray_icon = pystray.Icon(
        "suno_rpc",
        icon_img,
        "Suno RPC",
        menu=build_menu(),
    )

    tray_thread = threading.Thread(target=tray_icon.run, daemon=True)
    tray_thread.start()

    popup_win.mainloop()


if __name__ == "__main__":
    main()
