// popup.js

function formatTime(sec) {
  if (!sec || isNaN(sec)) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function updateUI(data, isConnected) {
  const serverEl = document.getElementById('server-status');
  const playerEl = document.getElementById('player-status');
  const container = document.getElementById('track-container');

  if (isConnected) {
    serverEl.textContent = 'Подключён ✓';
    serverEl.className = 'badge connected';
  } else {
    serverEl.textContent = 'Не подключён';
    serverEl.className = 'badge disconnected';
  }

  if (!data) {
    playerEl.textContent = 'Ожидание';
    playerEl.className = 'badge idle';
    container.innerHTML = `
      <div class="no-track">
        <div class="no-track-icon">🎵</div>
        <p>Открой suno.com<br>и начни воспроизведение</p>
      </div>`;
    return;
  }

  playerEl.textContent = data.isPaused ? '⏸ Пауза' : '▶ Играет';
  playerEl.className = 'badge ' + (data.isPaused ? 'paused' : 'playing');

  const progress = data.duration > 0 ? (data.elapsed / data.duration * 100) : 0;
  const coverHtml = data.coverUrl
    ? `<img class="cover" src="${data.coverUrl}" alt="cover">`
    : `<div class="cover-placeholder">🎵</div>`;

  container.innerHTML = `
    <div class="track-section">
      <div class="cover-row">
        ${coverHtml}
        <div class="track-info">
          <div class="track-title">${escHtml(data.title)}</div>
          <div class="track-artist">${escHtml(data.artist)}</div>
        </div>
      </div>
      <div class="progress-bar">
        <div class="progress-fill" style="width: ${progress.toFixed(1)}%"></div>
      </div>
      <div class="time-row">
        <span>${formatTime(data.elapsed)}</span>
        <span>${formatTime(data.duration)}</span>
      </div>
    </div>`;
}

chrome.runtime.sendMessage({ type: "GET_STATE" }, (response) => {
  if (chrome.runtime.lastError) { updateUI(null, false); return; }
  if (response) {
    updateUI(response.lastData, response.isConnected);
  }
});
