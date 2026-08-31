# 🎵 Suno → Discord Rich Presence

Приложение для Windows: живёт в трее и показывает в Discord трек, который
играет на suno.com — с названием, исполнителем, обложкой и прогресс-баром.
Когда музыка молчит, показывает видео с YouTube, а когда не играет ничего —
свой шаблон ожидания.

**[⬇ Скачать установщик](https://github.com/e24x5-Fox/suno-discord-rpc/releases/latest)**
— файл `Suno RPC Setup *.exe` в последнем релизе.

---

## Установка

**1. Поставь приложение** установщиком и запусти — иконка появится в трее.
Ничего настраивать не нужно: рабочий Discord Application ID уже вшит.

**2. Поставь расширение для браузера.** Оно читает плеер suno.com — без него
приложению нечего показывать. Все расширения уже лежат внутри приложения, в
`resources\extensions`; кнопка **«Как установить расширения»** в окне
открывает пошаговую инструкцию с путями и кнопками.

Запускай приложение **до** того, как открываешь Suno. Если открыл раньше —
просто обнови вкладку.

### Firefox — проще: один клик из каталога

| Расширение | Ссылка |
|---|---|
| Audio FX — эффекты для suno.com | [addons.mozilla.org](https://addons.mozilla.org/ru/firefox/addon/audio-fx-sound-enhancer/) |
| Suno Stats — своя история прослушиваний | [addons.mozilla.org](https://addons.mozilla.org/ru/firefox/addon/suno-stats/) |
| Suno → Discord RPC (версия 1.4.0, устаревшая) | [addons.mozilla.org](https://addons.mozilla.org/ru/firefox/addon/suno-discord-rpc/) |

Первые два в Firefox работают полностью — им приложение и не нужно. А вот
основному расширению нужны `offscreen` и `tabCapture` (есть только в Chromium),
да и `ws://` Firefox расширениям не выпускает — поэтому **для Suno нужен
Chrome, Edge, Opera GX или Яндекс**. YouTube-расширения в каталоге пока нет.

---

## Расширения

Живут в отдельных репозиториях — там их сборка, установка и issues:

| Репозиторий | Зачем | Браузеры |
|---|---|---|
| **[suno-rpc-extension](https://github.com/e24x5-Fox/suno-rpc-extension)** | **Обязательное.** Читает плеер suno.com | Chromium |
| **[youtube-rpc-extension](https://github.com/e24x5-Fox/youtube-rpc-extension)** | Активность YouTube, когда Suno молчит | Chromium, Firefox |
| **[audio-fx-extension](https://github.com/e24x5-Fox/audio-fx-extension)** | Не про RPC: эквалайзер, ревёрб, дилей, 8D для suno.com | Chromium, Firefox |

Кто попадёт в Discord, если открыто и то и другое:

| Suno | YouTube | В Discord |
|---|---|---|
| играет | играет | Suno |
| пауза / закрыт | играет | YouTube |
| играет | пауза | Suno |
| пауза | пауза | тот, что играл последним |

---

## Что настраивается

В окне приложения: тип активности и название в её шапке, две кнопки под
активностью (отдельно для Suno и YouTube), шаблон ожидания, иконка приложения
(её же видно в Discord — маленьким кружком поверх обложки), автозапуск с
Windows, поведение трея, Broadcast API для оверлеев и своих подписчиков.

Статистика прослушиваний и просмотров ведётся отдельно и открывается кнопкой
в трее или в окне. Логи — вкладка **⚠ Логи**, оттуда их можно скопировать
одной кнопкой.

Свой Discord Application ID можно подставить в настройках, если хочешь другое
имя приложения в активности, — но это необязательно.

---

## Сборка

```bash
build.bat            # бэкенд (PyInstaller) + установщик (electron-builder)
cd desktop && npm run dist    # только Electron-часть, если бэкенд не менялся
```

Нужны Python и Node.js, остальное ставится само. Версия установщика берётся из
`version` в `desktop/package.json`.

Иконки приложения собираются из `desktop/src/icons/source/` командой
`python build-icons.py`.

---

## Устройство

Два процесса: **Electron** — окно, трей и меню, **Python** — вся логика
(WebSocket-серверы, Discord RPC, статистика). Electron запускает бэкенд сам и
общается с ним по локальному HTTP.

```
suno.com → расширение → Python-бэкенд → Discord
                             ↑
                    Electron (окно и трей)
```

Порты: `6969` — расширение, `6970` — Broadcast API, `6971` — мобильная
синхронизация, `6972` — управление из интерфейса.

Подробности архитектуры, форматы данных и разбор известных граблей —
в [AI_GUIDE.md](AI_GUIDE.md).

---

## Авторы

- **[e24x5-Fox](https://github.com/e24x5-Fox)** — разработка
- **Claude (Anthropic)** — AI-ассистент, со-разработка кода

Лицензия — [MIT](LICENSE). Сборки есть только под Windows; сам бэкенд
кроссплатформенный.
