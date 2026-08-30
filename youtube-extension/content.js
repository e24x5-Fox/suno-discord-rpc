// content.js — читает данные плеера со страницы YouTube.
//
// Формат отправляемых данных совпадает с suno-расширением (title/artist/
// coverUrl/trackUrl/duration/elapsed/isPaused) и отличается только полем
// source: "youtube" — по нему сервер отличает источники и решает, чью
// активность показать в Discord. Не переименовывай поля под YouTube-термины:
// сервер и оверлей разбирают оба источника одним и тем же кодом.

const SEND_INTERVAL_MS = 2000;

let sendTimer = null;

function videoId() {
  const url = new URL(location.href);
  // Обычное видео: /watch?v=ID. Shorts: /shorts/ID — там параметра нет.
  const v = url.searchParams.get("v");
  if (v) return v;
  const m = url.pathname.match(/^\/shorts\/([\w-]{6,})/);
  return m ? m[1] : "";
}

function getTitle() {
  // Порядок важен: разметка watch-страницы менялась не раз, а document.title
  // существует всегда — но он же меняется последним при переходе между видео
  // в SPA, поэтому берётся только как запасной вариант.
  const el =
    document.querySelector("#title h1 yt-formatted-string") ||
    document.querySelector("h1.ytd-watch-metadata yt-formatted-string") ||
    document.querySelector("ytd-reel-player-header-renderer h2 yt-formatted-string") ||
    document.querySelector("h1.title yt-formatted-string");
  const fromDom = el?.textContent?.trim();
  if (fromDom) return fromDom;

  const t = document.title.replace(/\s*[-–]\s*YouTube\s*$/, "").trim();
  // На вкладке с непрочитанными уведомлениями заголовок выглядит как "(3) Название".
  return t.replace(/^\(\d+\)\s*/, "") || "Видео без названия";
}

function getChannel() {
  const el =
    document.querySelector("#owner #channel-name a") ||
    document.querySelector("ytd-channel-name#channel-name a") ||
    document.querySelector("#upload-info ytd-channel-name a") ||
    document.querySelector("ytd-reel-player-header-renderer #channel-name a");
  return el?.textContent?.trim() || "YouTube";
}

function isAdPlaying() {
  // Во время рекламы <video> проигрывает ролик рекламодателя: длительность и
  // позиция относятся к ней, а название на странице остаётся от настоящего
  // видео. Отправлять такую смесь нельзя — в Discord поехал бы таймкод.
  return !!document.querySelector(".html5-video-player.ad-showing, .ad-interrupting");
}

function getVideoData() {
  try {
    const id = videoId();
    if (!id) return null;

    // Берём именно основной плеер: на странице бывают и превью-видео в
    // рекомендациях, которые тоже являются <video>.
    const video =
      document.querySelector(".html5-main-video") ||
      document.querySelector("#movie_player video") ||
      document.querySelector("video");
    if (!video || !isFinite(video.duration) || video.duration <= 0) return null;
    if (isAdPlaying()) return null;

    return {
      source:   "youtube",
      title:    getTitle(),
      artist:   getChannel(),
      // hqdefault, а не maxresdefault: последнего у части видео просто нет,
      // и Discord показал бы пустую обложку вместо превью.
      coverUrl: `https://i.ytimg.com/vi/${id}/hqdefault.jpg`,
      trackUrl: `https://www.youtube.com/watch?v=${id}`,
      duration: Math.floor(video.duration) || 0,
      elapsed:  Math.floor(video.currentTime) || 0,
      isPaused: video.paused,
      timestamp: Date.now(),
    };
  } catch (e) {
    return null;
  }
}

function tick() {
  const data = getVideoData();
  if (!data) return;
  try {
    chrome.runtime.sendMessage({ type: "YT_UPDATE", data });
  } catch (e) {
    // Расширение перезагрузили — контекст умер, следующий тик подхватит заново.
  }
}

function start() {
  if (sendTimer) clearInterval(sendTimer);
  sendTimer = setInterval(tick, SEND_INTERVAL_MS);
  tick();
}

start();

// YouTube — SPA: переход к другому видео не перезагружает страницу и не
// перезапускает content-скрипт. Отдельно ловить навигацию не нужно, потому что
// tick() каждый раз перечитывает и URL, и DOM, — но при уходе со страницы
// видео нужно сообщить об этом, иначе сервер ещё 10 секунд считал бы источник
// живым и держал бы в Discord уже закрытое видео.
let lastPath = location.pathname + location.search;
setInterval(() => {
  const now = location.pathname + location.search;
  if (now === lastPath) return;
  lastPath = now;
  if (!videoId()) {
    try { chrome.runtime.sendMessage({ type: "YT_GONE" }); } catch (e) {}
  }
}, 1000);
