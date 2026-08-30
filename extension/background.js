// background.js

let ws = null;
let lastData = null;
let reconnectTimer = null;

// ── Авто-задержка звука для синхронизации с визуализацией ──────────────────
// Меряем round-trip до Python (WS уже открыт на 6969) и берём половину как
// оценку задержки конвейера offscreen → background → Python → Electron.
// RENDER_BUFFER_MS — грубая поправка на сглаживание/рендер в самом оверлее,
// которое этим пингом не измеряется (нет обратного канала от Electron) и
// зависит от личных настроек Атака/Релиз пользователя в оверлее — поэтому
// это ТОЛЬКО стартовая оценка, а не точный расчёт. delayOffsetMs — ручная
// подстройка поверх неё из попапа расширения, персистится в chrome.storage.
const PING_INTERVAL_MS          = 2000;
const RENDER_BUFFER_MS          = 150;
const MIN_DELAY_MS              = 0;
const MAX_DELAY_MS              = 800;
const DELAY_CHANGE_THRESHOLD_MS = 15;

let emaLatencyMs    = null;
let lastSentDelayMs = null;
let delayOffsetMs   = 0;
let pingTimer = null;

chrome.storage.local.get(["audioDelayOffsetMs"], (res) => {
  if (typeof res.audioDelayOffsetMs === "number") delayOffsetMs = res.audioDelayOffsetMs;
});

function sendPing() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "PING", ts: Date.now() }));
  }
}

function currentTargetDelay() {
  const base = emaLatencyMs === null ? 0 : emaLatencyMs + RENDER_BUFFER_MS;
  return Math.max(MIN_DELAY_MS, Math.min(MAX_DELAY_MS, Math.round(base + delayOffsetMs)));
}

function applyDelay(force) {
  const targetDelay = currentTargetDelay();
  if (force || lastSentDelayMs === null || Math.abs(targetDelay - lastSentDelayMs) > DELAY_CHANGE_THRESHOLD_MS) {
    lastSentDelayMs = targetDelay;
    chrome.runtime.sendMessage({ type: "SET_DELAY", delayMs: targetDelay }).catch(() => {});
  }
}

function handlePong(ts) {
  const rtt = Date.now() - ts;
  if (!(rtt >= 0) || rtt > 5000) return; // защита от битых/устаревших замеров

  const oneWayEstimate = rtt / 2;
  emaLatencyMs = emaLatencyMs === null ? oneWayEstimate : emaLatencyMs * 0.8 + oneWayEstimate * 0.2;
  applyDelay(false);
}

// ── Offscreen / Audio capture ─────────────────────────────────────────────────
let capturedTabId = null;
const OFFSCREEN_URL = chrome.runtime.getURL("offscreen.html");

async function ensureOffscreenDocument() {
  if (await chrome.offscreen.hasDocument()) return;
  await chrome.offscreen.createDocument({
    url: OFFSCREEN_URL,
    reasons: [chrome.offscreen.Reason.USER_MEDIA],
    justification: "Захват аудио вкладки для FFT визуализации"
  });
  console.log("[Suno Audio] Offscreen document создан");
}

async function startAudioCapture(tabId) {
  if (capturedTabId === tabId) return;
  stopAudioCapture();

  chrome.tabCapture.getMediaStreamId({ targetTabId: tabId }, async (streamId) => {
    if (chrome.runtime.lastError || !streamId) {
      console.warn("[Suno Audio] getMediaStreamId failed:", chrome.runtime.lastError?.message);
      return;
    }
    console.log("[Suno Audio] streamId получен:", streamId);
    try {
      await ensureOffscreenDocument();
      capturedTabId = tabId;
      chrome.runtime.sendMessage({ type: "START_CAPTURE", streamId });
      console.log("[Suno Audio] Захват аудио запущен");
    } catch (err) {
      console.error("[Suno Audio] Ошибка создания offscreen:", err);
    }
  });
}

function stopAudioCapture() {
  if (capturedTabId === null) return;
  capturedTabId = null;
  chrome.offscreen.hasDocument().then(has => {
    if (has) chrome.runtime.sendMessage({ type: "STOP_CAPTURE" });
  }).catch(() => {});
  console.log("[Suno Audio] Захват остановлен");
}

// ── WebSocket ─────────────────────────────────────────────────────────────────
function connect() {
  try {
    ws = new WebSocket("ws://localhost:6969");

    ws.onopen = () => {
      console.log("[Suno RPC] Подключено к Python серверу");
      if (lastData) ws.send(JSON.stringify(lastData));
      if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }

      if (pingTimer) clearInterval(pingTimer);
      pingTimer = setInterval(sendPing, PING_INTERVAL_MS);
      sendPing(); // первый замер сразу, не ждать целый интервал
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "PONG") handlePong(msg.ts);
      } catch {}
    };

    ws.onclose = () => {
      if (pingTimer) { clearInterval(pingTimer); pingTimer = null; }
      scheduleReconnect();
    };
    ws.onerror = () => scheduleReconnect();
  } catch { scheduleReconnect(); }
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); }, 5000);
}

// ── Messages ──────────────────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "TRACK_UPDATE") {
    const tabId = sender.tab?.id;
    // Если уже захвачена конкретная вкладка (для аудио) — принимаем данные
    // ТОЛЬКО от неё. Иначе content.js на каждой открытой вкладке suno.com
    // (даже фоновой, на паузе) шлёт TRACK_UPDATE раз в 2с независимо, и они
    // перебивают друг друга — то один трек мелькнёт в Discord, то другой,
    // без всякой связи с тем, что реально играет. Освобождается автоматически
    // при закрытии/обновлении вкладки (см. chrome.tabs.onRemoved ниже).
    if (capturedTabId !== null && tabId !== capturedTabId) return;
    lastData = message.data;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message.data));
    if (tabId) startAudioCapture(tabId);
    return;
  }

  if (message.type === "AUDIO_DATA") {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type:     "AUDIO_DATA",
        bass:     message.bass,
        mid:      message.mid,
        high:     message.high,
        volume:   message.volume,
        spectrum: message.spectrum,
      }));
    }
    return;
  }

  if (message.type === "GET_STATE") {
    sendResponse({ lastData, isConnected: ws && ws.readyState === WebSocket.OPEN });
    return true;
  }

  if (message.type === "STOP_AUDIO") {
    stopAudioCapture();
    return;
  }

  if (message.type === "SET_DELAY_OFFSET") {
    delayOffsetMs = Number(message.offsetMs) || 0;
    chrome.storage.local.set({ audioDelayOffsetMs: delayOffsetMs });
    applyDelay(true); // сразу, без порога — пользователь двигает ползунок вручную
    return;
  }

  if (message.type === "GET_DELAY_INFO") {
    sendResponse({
      emaLatencyMs,
      renderBufferMs: RENDER_BUFFER_MS,
      delayOffsetMs,
      targetDelayMs: currentTargetDelay(),
    });
    return true;
  }
});

// Если вкладка Suno закрылась/обновилась — останавливаем захват
chrome.tabs.onRemoved.addListener((tabId) => {
  if (tabId === capturedTabId) stopAudioCapture();
});
chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (tabId === capturedTabId && info.status === "loading") stopAudioCapture();
});

// ── Живучесть service worker'а ───────────────────────────────────────────────
// Chromium выгружает service worker расширения примерно через 30 секунд
// простоя, унося с собой WebSocket. Обычно его будит content.js, который шлёт
// данные каждые 2 секунды, — но у ФОНОВОЙ вкладки браузер душит таймеры, и
// сообщения приходить перестают. В логах сервера это выглядело как «расширение
// подключилось / отключилось» каждые полминуты: источник пропадал, сервер
// подставлял шаблон ожидания, потом трек возвращался — и так по кругу, хотя
// музыка всё это время играла.
//
// Будильник — единственный механизм, который будит worker независимо от того,
// активна ли вкладка. На пробуждении проверяем сокет и при необходимости
// переподключаемся: после выгрузки worker'а все переменные модуля обнулены.
const KEEPALIVE_ALARM = "suno-rpc-keepalive";

chrome.alarms.create(KEEPALIVE_ALARM, { periodInMinutes: 0.5 });

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== KEEPALIVE_ALARM) return;
  if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
    connect();
  } else if (ws.readyState === WebSocket.OPEN) {
    sendPing();   // заодно подтверждает, что соединение живое
  }
});

connect();
