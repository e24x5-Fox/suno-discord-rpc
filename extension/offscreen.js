// offscreen.js — запускается в Offscreen Document (MV3)
// Имеет доступ к DOM и Web Audio API, но НЕ к chrome.tabCapture

let audioCtx     = null;
let analyser     = null;
let delayNode    = null;
let audioStream  = null;
let audioInterval = null;

// FFT_SIZE=128 (64 бина на ВЕСЬ диапазон 0-24кГц при 48кГц) слишком грубо для
// реального баса: при таком разрешении бин — это ~375Гц, и "бас"-четверть
// массива на деле накрывала 0-6кГц, а не суббас+бас (20-250Гц) — из-за этого
// "вес баса" в оверлее реагировал на что угодно в нижней половине спектра, а
// не именно на бас. 1024 даёт бины по ~47Гц при 48кГц — уже можно вычленить
// реальную полосу баса, а не приблизительно.
const FFT_SIZE = 1024;

// ── Границы полос в РЕАЛЬНЫХ Гц, не по индексу массива ──────────────────────
const BASS_MAX_HZ = 250;   // суббас + бас
const MID_MAX_HZ  = 4000;  // выше этого — "высокие"

function hzToBin(hz, sampleRate, binCount) {
  const nyquist = sampleRate / 2;
  return Math.min(binCount, Math.max(0, Math.round((hz / nyquist) * binCount)));
}

function avgRange(data, a, b) {
  if (b <= a) return 0;
  let s = 0;
  for (let i = a; i < b; i++) s += data[i];
  return (s / (b - a)) / 255;
}

// ── Спектр для настоящего эквалайзера («Полоски» в независимых волнах) ──────
// Лог-шкала 40Гц..Nyquist — как в реальных эквалайзерах: бас не сжимается в
// 1-2 крайних бина, высокие не растягиваются на пол-графика. Границы каждой
// полосы считаются один раз в startCapture() от РЕАЛЬНОЙ sampleRate.
const SPECTRUM_BARS = 24
const SPECTRUM_MIN_HZ = 40

function buildSpectrumBins(sampleRate, binCount) {
  const nyquist = sampleRate / 2
  const ranges = []
  for (let i = 0; i < SPECTRUM_BARS; i++) {
    const loHz = SPECTRUM_MIN_HZ * Math.pow(nyquist / SPECTRUM_MIN_HZ, i / SPECTRUM_BARS)
    const hiHz = SPECTRUM_MIN_HZ * Math.pow(nyquist / SPECTRUM_MIN_HZ, (i + 1) / SPECTRUM_BARS)
    const loBin = Math.max(0, Math.min(binCount - 1, Math.round(loHz / nyquist * binCount)))
    const hiBin = Math.max(loBin + 1, Math.min(binCount, Math.round(hiHz / nyquist * binCount)))
    ranges.push([loBin, hiBin])
  }
  return ranges
}

// ── Задержка звука для синхронизации с визуализацией ────────────────────────
// Анализ (analyser) идёт от исходного, НЕзадержанного звука — FFT всегда
// «видит» звук чуть раньше, чем он реально долетит до колонок. За это время
// весь конвейер (offscreen → background → Python → Electron → рендер) успевает
// обработать и отрисовать волну — к моменту, когда звук реально прозвучит,
// визуал уже готов. Величина задержки приходит из background.js (см. SET_DELAY,
// автоматический замер через PING/PONG), меняется плавно через setTargetAtTime,
// чтобы не было щелчков/подскока высоты звука при подстройке на лету.
const DEFAULT_DELAY_SEC  = 0.1;
const DELAY_RAMP_SECONDS = 0.6;

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
    // Низкое значение — почти сырые данные. Своё, настраиваемое сглаживание
    // (Атака/Релиз в оверлее) уже есть ниже по конвейеру; если сглаживать ещё
    // и здесь сильно (было 0.75, дефолт Web Audio API — 0.8), удары баса
    // размываются ДО того, как долетят до тех настроек — оверлей получает уже
    // мёртвый, обездвиженный сигнал, и никакие ползунки это не лечат.
    analyser.smoothingTimeConstant = 0.2;

    delayNode = audioCtx.createDelay(1.0); // максимум 1с про запас
    delayNode.delayTime.value = DEFAULT_DELAY_SEC;

    source.connect(analyser);           // анализ — без задержки, сразу
    source.connect(delayNode);          // звук — с задержкой, для синхронизации
    delayNode.connect(audioCtx.destination); // возвращаем звук в динамики

    await audioCtx.resume(); // resume после сборки графа, offscreen не имеет user gesture

    const binCount = analyser.frequencyBinCount;
    const data     = new Uint8Array(binCount);

    // Границы бинов считаем от РЕАЛЬНОЙ sampleRate этого аудио-контекста —
    // она не всегда 48кГц (зависит от устройства/вкладки), поэтому нельзя
    // просто захардкодить индексы.
    const bassBinEnd = Math.max(1, hzToBin(BASS_MAX_HZ, audioCtx.sampleRate, binCount));
    const midBinEnd  = Math.max(bassBinEnd + 1, hzToBin(MID_MAX_HZ, audioCtx.sampleRate, binCount));
    const spectrumBins = buildSpectrumBins(audioCtx.sampleRate, binCount);

    audioInterval = setInterval(() => {
      if (!analyser) return;
      analyser.getByteFrequencyData(data);
      const bass     = avgRange(data, 0, bassBinEnd);
      const mid      = avgRange(data, bassBinEnd, midBinEnd);
      const high     = avgRange(data, midBinEnd, binCount);
      const volume   = Math.round(avgRange(data, 0, binCount) * 255);
      const spectrum = spectrumBins.map(([a, b]) => avgRange(data, a, b));
      chrome.runtime.sendMessage({ type: "AUDIO_DATA", bass, mid, high, volume, spectrum });
    }, 50);

    console.log("[Suno Offscreen] Захват аудио запущен, streamId:", streamId);
  } catch (err) {
    console.error("[Suno Offscreen] Ошибка захвата:", err);
  }
}

function stopCapture() {
  if (audioInterval) { clearInterval(audioInterval); audioInterval = null; }
  if (analyser)      { analyser.disconnect(); analyser = null; }
  if (delayNode)     { delayNode.disconnect(); delayNode = null; }
  if (audioCtx)      { audioCtx.close().catch(() => {}); audioCtx = null; }
  if (audioStream)   { audioStream.getTracks().forEach(t => t.stop()); audioStream = null; }
  console.log("[Suno Offscreen] Захват остановлен");
}

function setDelay(delayMs) {
  if (!audioCtx || !delayNode || typeof delayMs !== "number") return;
  const sec = Math.max(0, Math.min(1, delayMs / 1000));
  delayNode.delayTime.setTargetAtTime(sec, audioCtx.currentTime, DELAY_RAMP_SECONDS);
  console.log(`[Suno Offscreen] Авто-задержка звука подстроена: ${delayMs}мс`);
}

chrome.runtime.onMessage.addListener((message) => {
  if (message.type === "START_CAPTURE") {
    startCapture(message.streamId);
  } else if (message.type === "STOP_CAPTURE") {
    stopCapture();
  } else if (message.type === "SET_DELAY") {
    setDelay(message.delayMs);
  }
});
