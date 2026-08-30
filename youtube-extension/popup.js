// popup.js — показывает, что расширение сейчас отправляет серверу.

function fmt(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(sec / 60), s = sec % 60;
  return m + ":" + String(s).padStart(2, "0");
}

function set(id, text, cls) {
  const el = document.getElementById(id);
  el.textContent = text;
  el.className = "val" + (cls ? " " + cls : "");
}

function refresh() {
  chrome.runtime.sendMessage({ type: "GET_STATE" }, (res) => {
    // lastError бывает, когда service worker ещё просыпается — тогда просто
    // ждём следующего тика, а не показываем ошибку.
    if (chrome.runtime.lastError || !res) return;

    set("conn", res.isConnected ? "подключён" : "не подключён", res.isConnected ? "ok" : "");
    // Причина видна сразу: чаще всего приложение просто не запущено, и без
    // подсказки это неотличимо от поломки самого расширения.
    const conn = document.getElementById("conn");
    conn.title = res.isConnected ? "" : (res.lastError || "");
    set("tabs", String(res.tabCount || 0), "");

    const d = res.lastData;
    if (!d) {
      document.getElementById("title").textContent = "Видео не найдено";
      document.getElementById("channel").textContent = "—";
      document.getElementById("fill").style.width = "0%";
      document.getElementById("time").textContent = "0:00 / 0:00";
      set("play", "—", "");
      return;
    }

    document.getElementById("title").textContent = d.title || "—";
    document.getElementById("channel").textContent = d.artist || "—";
    const pct = d.duration > 0 ? Math.min(d.elapsed / d.duration, 1) : 0;
    document.getElementById("fill").style.width = (pct * 100) + "%";
    document.getElementById("time").textContent = fmt(d.elapsed) + " / " + fmt(d.duration);
    set("play", d.isPaused ? "на паузе" : "играет", d.isPaused ? "" : "on");
  });
}

refresh();
setInterval(refresh, 1000);
