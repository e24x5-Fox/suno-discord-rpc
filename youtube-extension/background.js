// background.js — отправка данных о видео в приложение Suno RPC.
//
// Транспорт здесь HTTP, а не WebSocket, как в suno-расширении. Причина в
// Firefox: он не выпускает ws:// из контекста расширения (moz-extension:// —
// защищённый контекст). В зависимости от настроек он либо блокирует запрос
// молча, либо повышает его до wss:// и упирается в TLS-рукопожатие на обычном
// сервере — соединение закрывается с кодом 1006, и расширение выглядит
// «подключённым, но неработающим». Обычный HTTP на localhost Firefox
// пропускает, поэтому данные уходят POST'ом на control API приложения.
//
// Для одного обновления раз в 2 секунды этого более чем достаточно: аудио для
// визуализации отсюда не передаётся, это задача suno-расширения.

const ENDPOINT = "http://127.0.0.1:6972/api/source_update";
const TAB_STALE_MS = 8000;   // вкладка молчит дольше — считаем её неактуальной

let connected = false;
let lastError = "";

// tabId -> { data, updated, playingAt }
const tabs = new Map();

async function send(data) {
  try {
    const res = await fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    connected = res.ok;
    lastError = res.ok ? "" : "HTTP " + res.status;
  } catch (e) {
    // Приложение не запущено — это нормальное состояние, а не ошибка:
    // пользователь мог просто ещё не открыть Suno RPC.
    connected = false;
    lastError = e.message || "нет соединения";
  }
}

// Вкладок YouTube у пользователя обычно несколько, и content.js работает в
// каждой — включая фоновые с поставленным на паузу видео. Без выбора они бы
// перебивали друг друга, и в Discord мелькало бы то одно видео, то другое.
// Побеждает играющая вкладка (самая свежая из играющих), иначе — та, что
// играла последней.
function pickActiveTab() {
  const now = Date.now();
  let best = null;
  let bestKey = -1;

  for (const [, rec] of tabs) {
    if (now - rec.updated > TAB_STALE_MS) continue;
    const playing = !rec.data.isPaused;
    // Играющие всегда важнее приостановленных, поэтому им добавляется
    // заведомо больший разряд, а внутри группы сравнивается свежесть.
    const key = (playing ? 1e15 : 0) + (playing ? rec.updated : rec.playingAt);
    if (key > bestKey) { bestKey = key; best = rec; }
  }
  return best;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "YT_UPDATE") {
    const tabId = sender.tab?.id;
    if (tabId === undefined) return;

    const rec = tabs.get(tabId) || { data: null, updated: 0, playingAt: 0 };
    rec.data = message.data;
    rec.updated = Date.now();
    if (!message.data.isPaused) rec.playingAt = rec.updated;
    tabs.set(tabId, rec);

    const active = pickActiveTab();
    if (active) send(active.data);
    return;
  }

  if (message.type === "YT_GONE") {
    const tabId = sender.tab?.id;
    if (tabId !== undefined) tabs.delete(tabId);
    return;
  }

  if (message.type === "GET_STATE") {
    const active = pickActiveTab();
    sendResponse({
      lastData: active ? active.data : null,
      tabCount: tabs.size,
      isConnected: connected,
      lastError,
    });
    return true;
  }
});

chrome.tabs.onRemoved.addListener((tabId) => { tabs.delete(tabId); });
