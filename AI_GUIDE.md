# 🤖 Гайд для AI-ассистентов: Suno → Discord RPC

Этот документ описывает архитектуру проекта, типичные проблемы и как их решать.
Предназначен для будущих AI-сессий, чтобы не начинать с нуля.

---

## Архитектура проекта

```
suno.com (браузер)
  └── content.js        ← читает DOM страницы каждые 2 сек
        └── chrome.runtime.sendMessage(TRACK_UPDATE)
              └── background.js   ← WebSocket клиент
                    └── ws://localhost:6969
                          └── suno_rpc.py  ← WebSocket сервер + Discord RPC
                                └── Discord (pypresence / AioPresence)
```

**Поток данных:** DOM → content.js → background.js → Python → Discord

---

## Структура передаваемых данных

```js
{
  title:     string,   // название трека
  artist:    string,   // исполнитель
  coverUrl:  string,   // URL обложки (cdn2.suno.ai)
  duration:  number,   // длина трека в секундах
  elapsed:   number,   // текущая позиция в секундах
  isPaused:  boolean,  // на паузе или нет
  timestamp: number    // Date.now()
}
```

---

## Селекторы DOM на suno.com (актуальные)

### Название трека
```js
// aria-label содержит "Playbar: Title for <название>"
const titleLink = document.querySelector('a[aria-label^="Playbar: Title for"]');
const label = titleLink.getAttribute('aria-label');
const title = label.match(/Playbar:\s*Title for\s+(.+)/i)?.[1]?.trim();
```
⚠️ `.textContent` ссылки не равен названию — название только в `aria-label`.

### Обложка трека
```js
// img с aria-label "Playbar: Cover image for <название>"
// src = https://cdn2.suno.ai/image_b54c84cc-....jpeg?width=100
// data-src = https://cdn2.suno.ai/image_large_b54c84cc-....jpeg  ← лучшее качество
const coverImg = document.querySelector('img[aria-label^="Playbar: Cover image"]');
const dataSrc = coverImg.getAttribute('data-src');   // большое изображение
const src     = coverImg.getAttribute('src');         // маленькое (width=100)
const coverUrl = dataSrc || src.replace(/\?.*$/, "");
```
⚠️ Не использовать `.src` (свойство) — может вернуть пустую строку в контексте расширения. Использовать `getAttribute('src')`.

⚠️ На странице много `img[src*="cdn2.suno.ai"]` — это список треков, **не** обложка плеера. Нужен именно `aria-label^="Playbar: Cover image"`.

### Исполнитель
```js
// Вариант 1: ссылка с aria-label
const artistLink = document.querySelector('a[aria-label^="Playbar: Artist"]');

// Вариант 2: второй span.line-clamp-1 в зоне плеера
const spans = document.querySelectorAll('span.line-clamp-1.w-full');
const artist = spans[1]?.textContent?.trim();
```

### Аудио-элемент (прогресс-бар)
```js
const audio = document.querySelector('audio');
audio.currentTime  // elapsed
audio.duration     // total
audio.paused       // isPaused
```

---

## Ограничения Discord Rich Presence

- Обновление не чаще **1 раза в 15 секунд** (лимит API) — настраивается в `UPDATE_INTERVAL`
- `large_image` принимает: загруженные арт-ассеты в Developer Portal **или** прямые URL
- CDN Suno (`cdn2.suno.ai`) Discord принимает — проверено
- Если обложка не отображается: подождать 1-2 минуты (Discord кеширует), либо загрузить ассет вручную
- Прогресс-бар работает только при воспроизведении (не на паузе) — это ограничение Discord

---

## Типичные баги и решения

| Симптом | Причина | Решение |
|---|---|---|
| Название "Неизвестный трек" | Селектор берёт `.textContent` вместо `aria-label` | Парсить `aria-label` через regex |
| Обложка не та (чужой трек) | `querySelectorAll('img[src*="cdn2.suno.ai"]')` берёт первый попавшийся | Использовать `aria-label^="Playbar: Cover image"` |
| Обложка — аватарка профиля | Селектор попал на `img.rounded-full.h-9.w-9` (аватар юзера) | Использовать `aria-label^="Playbar: Cover image"` |
| `coverUrl` пустой | `coverImg.src` возвращает `""` в content script | Использовать `getAttribute('src')` |
| Статус не обновляется | `UPDATE_INTERVAL = 15` сек | Нормально, ждать |
| Расширение не видит аудио | Страница открыта до установки расширения | Обновить вкладку suno.com |

---

## Файлы проекта

```
extension/
├── manifest.json    — MV3, permissions: activeTab, scripting, storage
├── content.js       — читает DOM, отправляет TRACK_UPDATE каждые 2 сек
├── background.js    — service worker, WebSocket клиент на localhost:6969
├── popup.html       — UI расширения (статус + трек)
└── popup.js         — запрашивает GET_STATE у background, рендерит UI

python/
├── suno_rpc.py      — asyncio: WebSocket сервер + AioPresence + tkinter tray
└── requirements.txt — pypresence>=4.3.0, websockets>=12.0, pystray, pillow
```

---

## Как отлаживать селекторы

Открыть DevTools на suno.com (F12 → Console) и проверить:

```js
// Название
document.querySelector('a[aria-label^="Playbar: Title for"]')?.getAttribute('aria-label')

// Обложка
const img = document.querySelector('img[aria-label^="Playbar: Cover image"]');
console.log(img?.getAttribute('src'), img?.getAttribute('data-src'));

// Все img в плеере
document.querySelectorAll('img[src*="cdn2.suno.ai"]').forEach(i =>
  console.log(i.src.slice(0,80), i.getAttribute('aria-label'))
)
```

После правки `content.js`: `chrome://extensions` → кнопка обновления (🔄) → обновить вкладку suno.com.

---

## Конфиг (suno_rpc.cfg, создаётся автоматически)

```ini
[settings]
discord_client_id = 1426921356527927399
websocket_port = 6969
update_interval = 15
show_elapsed = true
autostart_discord = true
```

---

> Последнее обновление селекторов: июнь 2025.
> Suno может менять DOM — при поломке начинать отладку с проверки `aria-label` атрибутов в DevTools.
