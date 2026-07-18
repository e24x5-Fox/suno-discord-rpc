// background.js

let ws = null;
let lastData = null;
let reconnectTimer = null;

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
    };

    ws.onclose = () => scheduleReconnect();
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
    lastData = message.data;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message.data));
    if (sender.tab?.id) startAudioCapture(sender.tab.id);
    return;
  }

  if (message.type === "AUDIO_DATA") {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "AUDIO_DATA", fft: message.fft, volume: message.volume }));
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
});

// Если вкладка Suno закрылась/обновилась — останавливаем захват
chrome.tabs.onRemoved.addListener((tabId) => {
  if (tabId === capturedTabId) stopAudioCapture();
});
chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (tabId === capturedTabId && info.status === "loading") stopAudioCapture();
});

connect();
