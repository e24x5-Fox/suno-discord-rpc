// Audio FX — Universal Sound Enhancer
// Перехватывает все <audio>/<video> на странице через Web Audio API и прогоняет
// звук через цепочку: дисторшн → бас/средние/высокие (эквалайзер) → ревёрб.
// Скорость воспроизведения (замедление/ускорение) регулируется напрямую через
// HTMLMediaElement.playbackRate (естественно тянет за собой изменение тона —
// это и есть эффект "slowed", а не тайм-стрейтчинг без изменения питча).

if (!window.__afxLoaded) {
  window.__afxLoaded = true;

  const DEFAULTS = {
    enabled: true,
    distortion: 0,   // 0..100
    bass: 0,          // -15..15 дБ
    mid: 0,           // -15..15 дБ
    treble: 0,        // -15..15 дБ
    reverb: 0,        // 0..100
    rate: 100,        // 25..200 (%), 100 = обычная скорость
    pos: { x: 0.95, y: 0.92 }, // положение кружка — доля от ширины/высоты страницы
  };

  let settings = { ...DEFAULTS };
  let audioCtx = null;
  const hooked = new WeakMap(); // HTMLMediaElement -> { nodes }

  // ── Хранилище настроек ──────────────────────────────────────────────────
  function loadSettings(cb) {
    try {
      chrome.storage.local.get(["afxSettings"], (res) => {
        if (res && res.afxSettings) settings = { ...DEFAULTS, ...res.afxSettings };
        cb();
      });
    } catch (e) {
      cb();
    }
  }

  function saveSettings() {
    try { chrome.storage.local.set({ afxSettings: settings }); } catch (e) {}
  }

  // ── DSP-хелперы ──────────────────────────────────────────────────────────
  // Важно: рост громкости от дисторшна — это в основном буст ТИХИХ участков
  // (кривая у нуля становится очень крутой), а не рост пика. Попытка глобально
  // нормализовать саму кривую по RMS (была раньше) только всё портила: она
  // домножала уже перегруженную кривую на коэффициент >1, отчего даже небольшой
  // drive начинал "фигачить". Правильный фикс — не трогать кривую, а гасить
  // именно этот буст в реальном сигнале компрессором ниже (см. applySettingsToNode).
  function makeDistortionCurve(amount) {
    const k = amount * 6;
    const n = 44100;
    const curve = new Float32Array(n);
    const deg = Math.PI / 180;
    for (let i = 0; i < n; i++) {
      const x = (i * 2) / n - 1;
      curve[i] = k === 0 ? x : ((3 + k) * x * 20 * deg) / (Math.PI + k * Math.abs(x));
    }
    return curve;
  }

  function makeImpulseResponse(ctx, duration = 2.2, decay = 3) {
    const rate = ctx.sampleRate;
    const length = Math.floor(rate * duration);
    const impulse = ctx.createBuffer(2, length, rate);
    for (let ch = 0; ch < 2; ch++) {
      const data = impulse.getChannelData(ch);
      for (let i = 0; i < length; i++) {
        data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / length, decay);
      }
    }
    return impulse;
  }

  function getAudioCtx() {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioCtx.state === "suspended") {
      audioCtx.resume().catch(() => {});
    }
    return audioCtx;
  }

  // ── Подключение одного media-элемента к графу эффектов ─────────────────
  function hookElement(el) {
    if (hooked.has(el)) return;
    if (el.__afxFailed) return;

    let ctx, source;
    try {
      ctx = getAudioCtx();
      source = ctx.createMediaElementSource(el);
    } catch (e) {
      // Элемент уже подключён к другому AudioContext/ноде — пропускаем без шума.
      el.__afxFailed = true;
      return;
    }

    const preGain = ctx.createGain(); // небольшой запас на вход перед дисторшном

    const distortion = ctx.createWaveShaper();
    distortion.oversample = "4x";

    // Компрессор после дисторшна — гасит буст тихих участков от самой формулы
    // искажения, чтобы громкость не росла вместе с drive. Параметры зависят от
    // drive и выставляются в applySettingsToNode() сразу после создания нод.
    const compressor = ctx.createDynamicsCompressor();

    const bass   = ctx.createBiquadFilter();
    bass.type = "lowshelf";
    bass.frequency.value = 200;

    const mid = ctx.createBiquadFilter();
    mid.type = "peaking";
    mid.frequency.value = 1000;
    mid.Q.value = 1;

    const treble = ctx.createBiquadFilter();
    treble.type = "highshelf";
    treble.frequency.value = 3000;

    const dryGain = ctx.createGain();
    const wetGain = ctx.createGain();
    const convolver = ctx.createConvolver();
    convolver.buffer = makeImpulseResponse(ctx);

    source.connect(preGain);
    preGain.connect(distortion);
    distortion.connect(compressor);
    compressor.connect(bass);
    bass.connect(mid);
    mid.connect(treble);

    treble.connect(dryGain);
    treble.connect(convolver);
    convolver.connect(wetGain);

    dryGain.connect(ctx.destination);
    wetGain.connect(ctx.destination);

    const nodes = { source, preGain, distortion, compressor, bass, mid, treble, dryGain, wetGain };
    hooked.set(el, nodes);

    applySettingsToNode(nodes);
    applyRateToElement(el);

    el.addEventListener("play", () => {
      getAudioCtx();
      applyRateToElement(el);
    });
  }

  // ── Применение текущих настроек ─────────────────────────────────────────
  function applySettingsToNode(nodes) {
    const on = settings.enabled;
    const drive = on ? settings.distortion : 0;
    const t = drive / 100; // 0..1

    nodes.preGain.gain.value = 1 - t * 0.1; // небольшой запас на вход, основная работа — у компрессора ниже

    // Компрессор ловит именно тот буст тихих участков, который даёт формула дисторшна.
    // При drive=0 он полностью прозрачен (threshold=0, ratio=1 — реально не сжимает
    // ничего), и становится заметнее ровно по мере роста drive — не раньше.
    nodes.compressor.threshold.value = -t * 8;
    nodes.compressor.ratio.value     = 1 + t * 4;
    nodes.compressor.knee.value      = 6;
    nodes.compressor.attack.value    = 0.003;
    nodes.compressor.release.value   = 0.2;

    nodes.distortion.curve = makeDistortionCurve(drive);
    nodes.bass.gain.value    = on ? settings.bass   : 0;
    nodes.mid.gain.value     = on ? settings.mid    : 0;
    nodes.treble.gain.value  = on ? settings.treble : 0;
    nodes.dryGain.gain.value = 1;
    nodes.wetGain.gain.value = on ? settings.reverb / 100 : 0;
  }

  function applyRateToElement(el) {
    try {
      el.playbackRate = settings.enabled ? settings.rate / 100 : 1;
      // По умолчанию браузер сам "выравнивает" тон при смене playbackRate —
      // именно это и даёт металлический/роботический призвук на низкой скорости.
      // Отключаем эту коррекцию: получаем честное винтажное "slowed" (тон падает
      // вместе со скоростью), звучит естественно, без артефактов алгоритма.
      el.preservesPitch = false;
      el.mozPreservesPitch = false;
      el.webkitPreservesPitch = false;
    } catch (e) {}
  }

  function applyAll() {
    for (const el of document.querySelectorAll("audio, video")) {
      hookElement(el);
      const nodes = hooked.get(el);
      if (nodes) applySettingsToNode(nodes);
      applyRateToElement(el);
    }
  }

  // ── Наблюдение за DOM: сайты подгружают/пересоздают плееры динамически ──
  function scanNode(node) {
    if (node.nodeType !== 1) return;
    if (node.tagName === "AUDIO" || node.tagName === "VIDEO") hookElement(node);
    node.querySelectorAll && node.querySelectorAll("audio, video").forEach(hookElement);
  }

  const observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      m.addedNodes.forEach(scanNode);
    }
  });

  function startObserving() {
    observer.observe(document.documentElement, { childList: true, subtree: true });
    document.querySelectorAll("audio, video").forEach(hookElement);
  }

  // Первый пользовательский жест на странице — шанс снять suspended-статус контекста.
  ["click", "keydown", "touchstart"].forEach((evt) => {
    document.addEventListener(evt, () => { if (audioCtx) getAudioCtx(); }, { once: true, capture: true });
  });

  // ── Плавающая панель управления ──────────────────────────────────────────
  function buildPanel() {
    const root = document.createElement("div");
    root.id = "afx-root";
    root.innerHTML = `
      <div id="afx-toggle" title="Audio FX"><img src="${chrome.runtime.getURL("icons/toggle-icon.png")}" alt=""></div>
      <div id="afx-panel">
        <div id="afx-header">
          <span class="afx-title">Audio FX</span>
          <span id="afx-close">✕</span>
        </div>

        <div class="afx-row">
          <div class="afx-row-label"><span>Дисторшн</span><span class="afx-val" data-out="distortion"></span></div>
          <input type="range" min="0" max="100" step="1" data-key="distortion">
        </div>
        <div class="afx-row">
          <div class="afx-row-label"><span>Бас</span><span class="afx-val" data-out="bass"></span></div>
          <input type="range" min="-15" max="15" step="1" data-key="bass">
        </div>
        <div class="afx-row">
          <div class="afx-row-label"><span>Средние</span><span class="afx-val" data-out="mid"></span></div>
          <input type="range" min="-15" max="15" step="1" data-key="mid">
        </div>
        <div class="afx-row">
          <div class="afx-row-label"><span>Высокие</span><span class="afx-val" data-out="treble"></span></div>
          <input type="range" min="-15" max="15" step="1" data-key="treble">
        </div>
        <div class="afx-row">
          <div class="afx-row-label"><span>Ревёрб</span><span class="afx-val" data-out="reverb"></span></div>
          <input type="range" min="0" max="100" step="1" data-key="reverb">
        </div>
        <div class="afx-row">
          <div class="afx-row-label"><span>Скорость</span><span class="afx-val" data-out="rate"></span></div>
          <input type="range" min="25" max="200" step="1" data-key="rate">
        </div>

        <div class="afx-actions">
          <div class="afx-btn" id="afx-reset">Сброс</div>
          <div class="afx-btn afx-primary" id="afx-power">Вкл</div>
        </div>
        <div class="afx-status" id="afx-status">Эффекты применяются к audio/video на странице</div>
      </div>
    `;
    document.documentElement.appendChild(root);
    return root;
  }

  function fmtVal(key, v) {
    if (key === "bass" || key === "mid" || key === "treble") return (v > 0 ? "+" : "") + v + " дБ";
    if (key === "rate") return (v / 100).toFixed(2) + "×";
    return String(v);
  }

  function syncPanelUI(root) {
    root.querySelectorAll("input[type=range]").forEach((inp) => {
      const key = inp.dataset.key;
      inp.value = settings[key];
      const out = root.querySelector(`[data-out="${key}"]`);
      if (out) out.textContent = fmtVal(key, settings[key]);
    });
    const powerBtn = root.querySelector("#afx-power");
    powerBtn.textContent = settings.enabled ? "Вкл" : "Выкл";
    powerBtn.classList.toggle("afx-primary", settings.enabled);
    root.querySelector("#afx-toggle").classList.toggle("active", settings.enabled);
  }

  function applyAndPersist(root) {
    for (const el of document.querySelectorAll("audio, video")) {
      const nodes = hooked.get(el);
      if (nodes) applySettingsToNode(nodes);
      applyRateToElement(el);
    }
    syncPanelUI(root);
    saveSettings();
  }

  // ── Позиционирование кружка: доля от разрешения страницы ─────────────────
  function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }

  function positionRoot(root) {
    const size = 44;
    const left = clamp(settings.pos.x * window.innerWidth  - size / 2, 4, window.innerWidth  - size - 4);
    const top  = clamp(settings.pos.y * window.innerHeight - size / 2, 4, window.innerHeight - size - 4);
    root.style.left = left + "px";
    root.style.top  = top  + "px";
  }

  // Панель открывается в сторону, где есть место, чтобы не улетать за экран
  // независимо от того, в каком углу страницы сейчас стоит кружок.
  function positionPanel(root) {
    const panel = root.querySelector("#afx-panel");
    const rect  = root.getBoundingClientRect();
    const openUp   = rect.top  > window.innerHeight / 2;
    const openLeft = rect.left > window.innerWidth  / 2;
    panel.style.top    = openUp   ? "auto" : "56px";
    panel.style.bottom = openUp   ? "56px" : "auto";
    panel.style.left   = openLeft ? "auto" : "0";
    panel.style.right  = openLeft ? "0"    : "auto";
  }

  // Общий механизм drag для кружка и заголовка панели: если мышь/палец
  // сдвинулись меньше порога — считаем это кликом (onClick), иначе — перетаскиванием.
  function makeDraggable(root, handleEl, { onClick } = {}) {
    let dragging = false, moved = false, startX = 0, startY = 0, startLeft = 0, startTop = 0;

    function down(e) {
      const p = e.touches ? e.touches[0] : e;
      dragging = true; moved = false;
      startX = p.clientX; startY = p.clientY;
      const rect = root.getBoundingClientRect();
      startLeft = rect.left; startTop = rect.top;
      e.preventDefault();
    }
    function move(e) {
      if (!dragging) return;
      const p = e.touches ? e.touches[0] : e;
      const dx = p.clientX - startX;
      const dy = p.clientY - startY;
      if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
      root.style.left = clamp(startLeft + dx, 4, window.innerWidth  - 44 - 4) + "px";
      root.style.top  = clamp(startTop  + dy, 4, window.innerHeight - 44 - 4) + "px";
    }
    function up() {
      if (!dragging) return;
      dragging = false;
      if (moved) {
        const rect = root.getBoundingClientRect();
        settings.pos = { x: (rect.left + 22) / window.innerWidth, y: (rect.top + 22) / window.innerHeight };
        saveSettings();
        positionPanel(root);
      } else if (onClick) {
        onClick();
      }
    }

    handleEl.addEventListener("mousedown", down);
    handleEl.addEventListener("touchstart", down, { passive: false });
    window.addEventListener("mousemove", move);
    window.addEventListener("touchmove", move, { passive: false });
    window.addEventListener("mouseup", up);
    window.addEventListener("touchend", up);
  }

  function wirePanel(root) {
    const toggleBtn = root.querySelector("#afx-toggle");
    const panel     = root.querySelector("#afx-panel");
    const closeBtn  = root.querySelector("#afx-close");
    const header    = root.querySelector("#afx-header");

    makeDraggable(root, toggleBtn, {
      onClick: () => {
        panel.classList.toggle("open");
        applyAll(); // на случай, если плеер появился, пока панель была закрыта
      },
    });
    makeDraggable(root, header); // перетаскивание панели за заголовок, без onClick

    closeBtn.addEventListener("click", () => panel.classList.remove("open"));

    root.querySelectorAll("input[type=range]").forEach((inp) => {
      inp.addEventListener("input", () => {
        const key = inp.dataset.key;
        settings[key] = Number(inp.value);
        const out = root.querySelector(`[data-out="${key}"]`);
        if (out) out.textContent = fmtVal(key, settings[key]);
        applyAndPersist(root);
      });
    });

    root.querySelector("#afx-reset").addEventListener("click", () => {
      settings = { ...DEFAULTS, pos: settings.pos, enabled: settings.enabled };
      applyAndPersist(root);
    });

    root.querySelector("#afx-power").addEventListener("click", () => {
      settings.enabled = !settings.enabled;
      applyAndPersist(root);
    });

    window.addEventListener("resize", () => {
      positionRoot(root);
      positionPanel(root);
    });
  }

  // ── Инициализация ────────────────────────────────────────────────────────
  function initPanel() {
    try {
      const root = buildPanel();
      positionRoot(root);
      positionPanel(root);
      syncPanelUI(root);
      wirePanel(root);
    } catch (e) {
      console.error("[Audio FX] не удалось создать панель:", e);
    }
  }

  loadSettings(() => {
    initPanel();
    startObserving();

    // Некоторые SPA-сайты после нашей инициализации полностью пересобирают
    // <body> (гидратация/роутинг) и вместе с ним стирают наш узел — сторожок
    // раз в 2 сек проверяет, на месте ли панель, и пересоздаёт при необходимости.
    setInterval(() => {
      if (!document.getElementById("afx-root")) initPanel();
    }, 2000);
  });
}
