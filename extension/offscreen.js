// offscreen.js — запускается в Offscreen Document (MV3)
// Имеет доступ к DOM и Web Audio API, но НЕ к chrome.tabCapture

let audioCtx     = null;
let analyser     = null;
let audioStream  = null;
let audioInterval = null;
const FFT_SIZE   = 128; // 64 бара на выходе

async function startCapture(streamId) {
  stopCapture();

  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        mandatory: {
          chromeMediaSource: "tab",
          chromeMediaSourceId: streamId
        }
      },
      video: false
    });

    audioStream = stream;
    audioCtx    = new AudioContext();
    const source = audioCtx.createMediaStreamSource(stream);

    analyser = audioCtx.createAnalyser();
    analyser.fftSize = FFT_SIZE;
    analyser.smoothingTimeConstant = 0.75;
    source.connect(analyser);
    source.connect(audioCtx.destination); // возвращаем звук в динамики

    await audioCtx.resume(); // resume после сборки графа, offscreen не имеет user gesture

    const data = new Uint8Array(analyser.frequencyBinCount); // 64 значения

    audioInterval = setInterval(() => {
      if (!analyser) return;
      analyser.getByteFrequencyData(data);
      const fft    = Array.from(data);
      const volume = Math.round(fft.reduce((a, b) => a + b, 0) / fft.length);
      chrome.runtime.sendMessage({ type: "AUDIO_DATA", fft, volume });
    }, 50);

    console.log("[Suno Offscreen] Захват аудио запущен, streamId:", streamId);
  } catch (err) {
    console.error("[Suno Offscreen] Ошибка захвата:", err);
  }
}

function stopCapture() {
  if (audioInterval) { clearInterval(audioInterval); audioInterval = null; }
  if (analyser)      { analyser.disconnect(); analyser = null; }
  if (audioCtx)      { audioCtx.close().catch(() => {}); audioCtx = null; }
  if (audioStream)   { audioStream.getTracks().forEach(t => t.stop()); audioStream = null; }
  console.log("[Suno Offscreen] Захват остановлен");
}

chrome.runtime.onMessage.addListener((message) => {
  if (message.type === "START_CAPTURE") {
    startCapture(message.streamId);
  } else if (message.type === "STOP_CAPTURE") {
    stopCapture();
  }
});
