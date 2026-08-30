// content.js — читает данные плеера со страницы suno.com

let sendInterval = null;

function getTrackData() {
  try {
    const audio = document.querySelector('audio');
    if (!audio) return null;

    let title = "Неизвестный трек";
    const titleLink = document.querySelector('a[aria-label^="Playbar: Title for"]');
    if (titleLink) {
      const label = titleLink.getAttribute('aria-label') || "";
      const match = label.match(/Playbar:\s*Title for\s+(.+)/i);
      if (match) title = match[1].trim();
      else title = titleLink.textContent?.trim() || title;
    } else {
      const altTitle =
        document.querySelector('[data-testid="playbar-title"]') ||
        document.querySelector('[class*="playbar"] [class*="title"]') ||
        document.querySelector('[class*="player"] [class*="title"]');
      if (altTitle) title = altTitle.textContent?.trim() || title;
    }

    let artist = "Suno AI";
    const artistLink =
      document.querySelector('a[aria-label^="Playbar: Artist"]') ||
      document.querySelector('[data-testid="playbar-artist"]');
    if (artistLink) {
      artist = artistLink.textContent?.trim() || artist;
    } else {
      const spans = document.querySelectorAll('span.line-clamp-1.w-full, span.line-clamp-1');
      if (spans.length >= 2) artist = spans[1].textContent?.trim() || artist;
      else if (spans.length === 1) artist = spans[0].textContent?.trim() || artist;
    }

    let coverUrl = "";
    const coverImg = document.querySelector('img[aria-label^="Playbar: Cover image"]');
    if (coverImg) {
      coverUrl = coverImg.getAttribute('data-src') || coverImg.getAttribute('src') || coverImg.src || "";
      coverUrl = coverUrl.replace(/\?.*$/, "");
    }

    let trackUrl = "";
    if (titleLink) {
      const href = titleLink.getAttribute('href') || "";
      if (href.startsWith("/song/")) trackUrl = "https://suno.com" + href;
    }
    if (!trackUrl && coverUrl) {
      const m = coverUrl.match(/image(?:_large)?_([a-f0-9-]{36})/i);
      if (m) trackUrl = "https://suno.com/song/" + m[1];
    }

    return {
      // Сервер различает источники активности по этому полю: параллельно к
      // нему подключается youtube-расширение (youtube-extension/), которое
      // шлёт source: "youtube". Без поля сервер считает данные суновскими —
      // старые сборки расширения из-за этого продолжают работать.
      source: "suno",
      title, artist, coverUrl, trackUrl,
      duration: Math.floor(audio.duration) || 0,
      elapsed: Math.floor(audio.currentTime) || 0,
      isPaused: audio.paused,
      timestamp: Date.now()
    };
  } catch (e) { return null; }
}

function sendToBackground(data) {
  try { chrome.runtime.sendMessage({ type: "TRACK_UPDATE", data }); } catch {}
}

function startTracking() {
  if (sendInterval) clearInterval(sendInterval);
  sendInterval = setInterval(() => {
    const data = getTrackData();
    if (data) sendToBackground(data);
  }, 2000);
}

// Ждём появления плеера на странице
const observer = new MutationObserver(() => {
  if (document.querySelector('audio')) {
    startTracking();
    observer.disconnect();
  }
});
observer.observe(document.body, { childList: true, subtree: true });
if (document.querySelector('audio')) startTracking();
