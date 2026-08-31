// Главный процесс Electron: трей, окна и жизненный цикл приложения.
//
// Вся логика Suno → Discord живёт в отдельном Python-процессе (python/suno_rpc.py),
// запущенном отсюда как дочерний. Общение — по HTTP на 127.0.0.1:6972, методы
// повторяют бывший js_api-мост pywebview один в один (см. start_control_server
// в suno_rpc.py). Здесь нет ни одной строки, знающей про Suno или Discord.

const { app, BrowserWindow, Tray, Menu, ipcMain, nativeImage, shell, screen } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');

const CONTROL_PORT = 6972;
const CONTROL_HOST = '127.0.0.1';
const IS_DEV = !app.isPackaged;

// Полоса заголовка рисуется нами, а не Windows: системная красится в акцентный
// цвет пользователя и с фиолетовой иконкой приложения сочетается как придётся.
// Цвет намеренно константа, а не настройка, — окно должно всегда совпадать с
// иконкой. Тот же цвет лежит в index.html как --tb; правишь здесь — правь там.
const TITLEBAR_COLOR = '#8b5cf6';
const TITLEBAR_SYMBOL_COLOR = '#ffffff';   // цвет значков свернуть/развернуть/✕
const TITLEBAR_HEIGHT = 36;                // должен совпадать с --tb-h в index.html

// Прокси Hiddify в системных настройках Chromium применяет даже к localhost, и
// запросы к бэкенду уходят в никуда. Ровно та же строка стоит в оверлее
// (python/визуал/suno-overlay/src/main.js) — без неё приложение выглядит как
// «бэкенд не запустился», хотя он работает. Обязательно до app.whenReady().
app.commandLine.appendSwitch('no-proxy-server');

// ══════════════════════════════════════════════
//  ИКОНКИ
// ══════════════════════════════════════════════

// Наборы иконок готовит build-icons.py из desktop/src/icons/source/.
// Порядок здесь = порядок в настройках, первый вариант основной.
const ICON_VARIANTS = [
  { id: 'play',       label: 'Плеер' },
  { id: 'play-solid', label: 'Плеер (сплошной угол)' },
  { id: 'fox',        label: 'Бойкиссер' },
  { id: 'fox-solid',  label: 'Бойкиссер (сплошной угол)' },
  { id: 'stand', label: 'Во весь рост' },
  { id: 'stand-solid', label: 'Во весь рост (сплошной угол)' },
  { id: 'glee', label: 'Интересное радостное чувство' },
  { id: 'glee-solid', label: 'Интересное радостное чувство (сплошной угол)' },
  { id: 'cute-wow', label: 'Милое удивление' },
  { id: 'cute-wow-solid', label: 'Милое удивление (сплошной угол)' },
  { id: 'huh', label: 'Не понял' },
  { id: 'huh-solid', label: 'Не понял (сплошной угол)' },
  { id: 'sulk', label: 'Обида' },
  { id: 'sulk-solid', label: 'Обида (сплошной угол)' },
  { id: 'sad', label: 'Огорчение' },
  { id: 'sad-solid', label: 'Огорчение (сплошной угол)' },
  { id: 'sprawl', label: 'Ожидание (развалился)' },
  { id: 'sprawl-solid', label: 'Ожидание (развалился) (сплошной угол)' },
  { id: 'wait', label: 'Ожидание' },
  { id: 'wait-solid', label: 'Ожидание (сплошной угол)' },
  { id: 'chonk', label: 'Просто толстая кость' },
  { id: 'chonk-solid', label: 'Просто толстая кость (сплошной угол)' },
  { id: 'shy', label: 'Смущение' },
  { id: 'shy-solid', label: 'Смущение (сплошной угол)' },
  { id: 'shame', label: 'Стыдно' },
  { id: 'shame-solid', label: 'Стыдно (сплошной угол)' },
  { id: 'starfish', label: 'Счастье (звёздочкой)' },
  { id: 'starfish-solid', label: 'Счастье (звёздочкой) (сплошной угол)' },
  { id: 'joy', label: 'Счастье' },
  { id: 'joy-solid', label: 'Счастье (сплошной угол)' },
  { id: 'wow', label: 'Удивление' },
  { id: 'wow-solid', label: 'Удивление (сплошной угол)' },
  { id: 'aww', label: 'Умиление' },
  { id: 'aww-solid', label: 'Умиление (сплошной угол)' },
  { id: 'aww-2', label: 'Умиление 2' },
  { id: 'aww-2-solid', label: 'Умиление 2 (сплошной угол)' },
  { id: 'aww-3', label: 'Умиление 3' },
  { id: 'aww-3-solid', label: 'Умиление 3 (сплошной угол)' },
  { id: 'aww-4', label: 'Умиление 4' },
  { id: 'aww-4-solid', label: 'Умиление 4 (сплошной угол)' },
  { id: 'calm', label: 'Умиротворение' },
  { id: 'calm-solid', label: 'Умиротворение (сплошной угол)' },
  { id: 'tired', label: 'Усталость' },
  { id: 'tired-solid', label: 'Усталость (сплошной угол)' },
  { id: 'smirk', label: 'Прищур' },
  { id: 'smirk-solid', label: 'Прищур (сплошной угол)' },
];
const DEFAULT_VARIANT = ICON_VARIANTS[0].id;

// Выбор иконки хранится у самого Electron, а не в конфиге бэкенда: трей нужно
// нарисовать сразу при запуске, до того как бэкенд поднимется и ответит, иначе
// пользователь каждый раз видел бы, как иконка меняется через пару секунд.
const UI_SETTINGS_FILE = () => path.join(app.getPath('userData'), 'ui-settings.json');

// Здесь же (а не в конфиге бэкенда) живут настройки окна и автозапуска: они
// нужны главному процессу до того, как бэкенд поднимется, — окно создаётся и
// показывается в первые же миллисекунды, а бэкенд отвечает через пару секунд.
const DEFAULT_UI_SETTINGS = {
  iconVariant: DEFAULT_VARIANT,
  autoLaunch: false,        // запускать вместе с Windows
  startMinimized: true,     // стартовать сразу свёрнутым в трей
  closeToTray: true,        // ✕ прячет окно, а не завершает приложение
  minimizeToTray: false,    // «свернуть» убирает окно в трей, а не на панель задач
  alwaysOnTop: false,
  traySingleClick: false,   // открывать окно одним кликом по значку, а не двойным
  rememberBounds: true,     // запоминать размер и положение окна
  bounds: null,             // {x, y, width, height} последнего положения
};

// Ключи-переключатели, которые интерфейс имеет право менять по одному.
const WINDOW_SETTING_KEYS = [
  'autoLaunch', 'startMinimized', 'closeToTray', 'minimizeToTray',
  'alwaysOnTop', 'traySingleClick', 'rememberBounds',
];

function validBounds(b) {
  return !!b && ['x', 'y', 'width', 'height'].every((k) => Number.isFinite(b[k]))
    && b.width >= 200 && b.height >= 150;
}

function loadUiSettings() {
  let saved = {};
  try {
    saved = JSON.parse(fs.readFileSync(UI_SETTINGS_FILE(), 'utf8')) || {};
  } catch (e) { /* файла ещё нет или он битый — берём значения по умолчанию */ }
  // Мержим с дефолтами, а не возвращаем прочитанное целиком: файл мог быть
  // записан старой версией, где половины ключей ещё не существовало.
  const s = { ...DEFAULT_UI_SETTINGS, ...saved };
  if (!ICON_VARIANTS.some((v) => v.id === s.iconVariant)) s.iconVariant = DEFAULT_VARIANT;
  for (const key of WINDOW_SETTING_KEYS) s[key] = !!s[key];
  if (!validBounds(s.bounds)) s.bounds = null;
  return s;
}

function saveUiSettings(s) {
  try {
    fs.writeFileSync(UI_SETTINGS_FILE(), JSON.stringify(s, null, 2), 'utf8');
  } catch (e) {
    console.error('[ui-settings] не удалось сохранить:', e.message);
  }
}

let uiSettings = { ...DEFAULT_UI_SETTINGS };

const iconPath = (file) => path.join(__dirname, 'icons', 'variants', file);
const windowIconPath = () => iconPath(`${uiSettings.iconVariant}.ico`);

let mainWindow = null;
let statsWindow = null;
let guideWindow = null;
let tray = null;
let backend = null;          // ChildProcess Python-бэкенда
let quitting = false;        // отличает «закрыть в трей» от настоящего выхода
let lastState = null;        // последний ответ /api/get_state, он же источник иконки трея
let stateTimer = null;
const backendLog = [];       // stdout/stderr бэкенда — на случай, если он умрёт до старта API

// ══════════════════════════════════════════════
//  CONTROL API
// ══════════════════════════════════════════════

function callBackend(method, endpoint, payload) {
  return new Promise((resolve, reject) => {
    const body = payload === undefined ? null : Buffer.from(JSON.stringify(payload), 'utf8');
    const req = http.request({
      host: CONTROL_HOST,
      port: CONTROL_PORT,
      path: '/api/' + endpoint,
      method,
      headers: body
        ? { 'Content-Type': 'application/json', 'Content-Length': body.length }
        : {},
      timeout: 5000,
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        try {
          resolve(JSON.parse(text));
        } catch (e) {
          reject(new Error(`Бэкенд вернул не JSON (${res.statusCode}): ${text.slice(0, 200)}`));
        }
      });
    });
    req.on('timeout', () => req.destroy(new Error('Таймаут запроса к бэкенду')));
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

const get = (endpoint) => callBackend('GET', endpoint);
const post = (endpoint, payload) => callBackend('POST', endpoint, payload || {});

// ══════════════════════════════════════════════
//  БЭКЕНД
// ══════════════════════════════════════════════

function backendCommand() {
  if (app.isPackaged) {
    // --watch-stdin: бэкенд выйдет сам, если Electron умрёт, не дав команду /api/quit.
    const exe = path.join(process.resourcesPath, 'backend', 'suno-rpc-backend.exe');
    return { cmd: exe, args: ['--watch-stdin'], cwd: path.dirname(exe) };
  }
  // В разработке запускаем скрипт интерпретатором из PATH. -u обязателен:
  // без него Python буферизует stdout блоками по 4 КБ, и логи бэкенда доходят
  // до окна пачками с большой задержкой либо теряются при падении процесса.
  const pythonDir = path.join(__dirname, '..', '..', 'python');
  return {
    cmd: process.platform === 'win32' ? 'python' : 'python3',
    args: ['-u', path.join(pythonDir, 'suno_rpc.py'), '--watch-stdin'],
    cwd: pythonDir,
  };
}

function startBackend() {
  const { cmd, args, cwd } = backendCommand();
  backend = spawn(cmd, args, {
    // cwd задаёт backendCommand(): в собранном приложении папки python/ рядом
    // нет, а __dirname указывает внутрь app.asar — spawn с несуществующим cwd
    // падает с ENOENT ещё до запуска бэкенда.
    cwd,
    windowsHide: true,
    // stdin остаётся открытым пайпом намеренно: бэкенд следит за ним и выходит
    // по EOF (watch_parent_process в suno_rpc.py). Если Electron упадёт, пайп
    // закроется сам — иначе Python остался бы висеть без интерфейса, держа
    // порты и продолжая слать presence в Discord.
    stdio: ['pipe', 'pipe', 'pipe'],
  });

  const record = (prefix) => (chunk) => {
    for (const line of chunk.toString('utf8').split(/\r?\n/)) {
      if (!line.trim()) continue;
      backendLog.push(prefix + line);
      if (backendLog.length > 500) backendLog.shift();
      console.log('[backend]', line);
    }
  };
  backend.stdout.on('data', record(''));
  backend.stderr.on('data', record('stderr: '));

  backend.on('error', (err) => {
    backendLog.push(`Не удалось запустить бэкенд: ${err.message}`);
    console.error('[backend] spawn error', err);
  });

  backend.on('exit', (code) => {
    backend = null;
    if (quitting) return;
    // Код 3 — мьютекс занят другим экземпляром бэкенда (см. suno_rpc.py).
    const hint = code === 3
      ? 'Бэкенд уже запущен другим процессом. Закройте старый SunoRPC в диспетчере задач.'
      : `Бэкенд неожиданно завершился (код ${code}).`;
    backendLog.push(hint);
    if (mainWindow) mainWindow.webContents.send('backend-down', { code, hint, log: backendLog.slice(-30) });
  });
}

function waitForBackend(timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      get('ping').then(resolve).catch(() => {
        if (Date.now() > deadline) {
          reject(new Error('Бэкенд не ответил за отведённое время.\n' + backendLog.slice(-10).join('\n')));
        } else {
          setTimeout(attempt, 300);
        }
      });
    };
    attempt();
  });
}

// ══════════════════════════════════════════════
//  ТРЕЙ
// ══════════════════════════════════════════════

const trayIcons = {};

// Состояние сворачивается в цвет точки на иконке (её дорисовывает build-icons.py):
// зелёная — Discord подключён и идёт воспроизведение, жёлтая — подключён, но
// пауза, серая — Discord недоступен. Раньше трей рисовался кодом и показывал это
// формой значка, но на готовой картинке пользователя глиф уже не нарисуешь.
function trayIcon(playing, discord) {
  const state = !discord ? 'gray' : (playing ? 'green' : 'amber');
  const key = `${uiSettings.iconVariant}-${state}`;
  if (!trayIcons[key]) {
    trayIcons[key] = nativeImage.createFromPath(
      iconPath(`${uiSettings.iconVariant}-tray-${state}.ico`));
  }
  return trayIcons[key];
}

function buildTrayMenu() {
  return Menu.buildFromTemplate([
    { label: 'Открыть', click: () => toggleMainWindow() },
    { label: '📊 Статистика', click: () => openStatsWindow('suno') },
    { type: 'separator' },
    { label: 'Переподключить', click: () => post('reconnect_discord').catch(() => {}) },
    { label: 'Отключить RPC', click: () => post('disconnect_discord').catch(() => {}) },
    { type: 'separator' },
    { label: 'Выйти', click: () => quitApp() },
  ]);
}

function refreshTray() {
  if (!tray) return;
  const playing = lastState ? !lastState.is_paused : false;
  const discord = lastState ? !!lastState.discord : false;
  tray.setImage(trayIcon(playing, discord));
  const title = lastState && lastState.title ? lastState.title : 'Ожидание трека...';
  // Windows обрезает подсказку трея на 127 символах, длинные названия треков
  // Suno в этот лимит упираются регулярно.
  tray.setToolTip(`Suno RPC — ${title}`.slice(0, 127));
}

function applyIconVariant(id) {
  if (!ICON_VARIANTS.some((v) => v.id === id)) return false;
  uiSettings.iconVariant = id;
  saveUiSettings(uiSettings);

  // Бэкенду вариант нужен не для показа, а для ссылки: Discord тянет картинку
  // сам и до файлов приложения не достаёт, поэтому в presence уходит адрес
  // иконки в репозитории проекта. Ошибку глушим — выбор иконки не должен
  // отваливаться из-за того, что бэкенд ещё не поднялся.
  post('set_icon_variant', { variant: id }).catch(() => {});

  refreshTray();
  // Иконку окна меняем на лету — она же показывается на панели задач и в
  // Alt+Tab. А вот иконку самого .exe (в проводнике и у закреплённого ярлыка)
  // подменить нельзя: она зашита в файл при сборке электрон-билдером.
  const ico = windowIconPath();
  for (const win of [mainWindow, statsWindow]) {
    if (win && !win.isDestroyed()) win.setIcon(nativeImage.createFromPath(ico));
  }
  return true;
}

// ══════════════════════════════════════════════
//  ОПРОС СОСТОЯНИЯ
// ══════════════════════════════════════════════

// Опрос один на всё приложение: и трей, и окно берут состояние отсюда.
// Раньше состояние тянул рендерер (setInterval + api.get_state в pywebview);
// теперь окно может быть скрыто в трее, а иконка обязана обновляться всё равно.
function startStatePolling() {
  const tick = () => {
    get('get_state').then((s) => {
      lastState = s;
      refreshTray();
      if (mainWindow && mainWindow.isVisible()) mainWindow.webContents.send('state', s);
    }).catch(() => { /* бэкенд перезапускается или уже умер — переживём молча */ });
  };
  tick();
  stateTimer = setInterval(tick, 1000);
}

// ══════════════════════════════════════════════
//  АВТОЗАПУСК И ГЕОМЕТРИЯ ОКНА
// ══════════════════════════════════════════════

// Запуск вместе с системой — штатный механизм Electron: на Windows это запись
// в HKCU\...\Run с путём к exe. Аргумент --autostart нужен, чтобы отличить
// системный запуск от ручного и в этом случае всегда стартовать в трей.
function applyAutoLaunch() {
  // В разработке регистрировать нечего: openAtLogin запомнил бы путь к
  // electron.exe без папки проекта, и Windows поднимала бы пустой Electron.
  // Флаг при этом сохраняется — в собранном приложении он применится.
  if (!app.isPackaged) return;
  try {
    app.setLoginItemSettings({
      openAtLogin: uiSettings.autoLaunch,
      path: process.execPath,
      args: ['--autostart'],
    });
  } catch (e) {
    console.error('[autolaunch] не удалось применить:', e.message);
  }
}

function autoLaunchActive() {
  if (!app.isPackaged) return uiSettings.autoLaunch;
  try {
    return app.getLoginItemSettings({ path: process.execPath, args: ['--autostart'] }).openAtLogin;
  } catch (e) {
    return uiSettings.autoLaunch;
  }
}

// Сохранённое положение окна применимо не всегда: монитор, на котором окно
// стояло, могли отключить — тогда окно открылось бы за пределами видимой
// области и выглядело бы как «приложение не запустилось». В этом случае
// оставляем только размер, а позицию отдаём системе.
function savedWindowBounds() {
  const b = uiSettings.rememberBounds ? uiSettings.bounds : null;
  if (!validBounds(b)) return {};
  const anchorX = b.x + 60;   // точка внутри полосы заголовка, за которую тянут окно
  const anchorY = b.y + 20;
  const onScreen = screen.getAllDisplays().some((d) => {
    const a = d.workArea;
    return anchorX >= a.x && anchorX <= a.x + a.width
        && anchorY >= a.y && anchorY <= a.y + a.height;
  });
  return onScreen ? { ...b } : { width: b.width, height: b.height };
}

let boundsTimer = null;

// Запись на диск троттлится: resize/move летят десятками событий за перетаскивание.
function rememberBounds() {
  if (!uiSettings.rememberBounds) return;
  if (!mainWindow || mainWindow.isDestroyed()) return;
  clearTimeout(boundsTimer);
  boundsTimer = setTimeout(captureBounds, 500);
}

function captureBounds() {
  clearTimeout(boundsTimer);
  if (!uiSettings.rememberBounds) return;
  if (!mainWindow || mainWindow.isDestroyed()) return;
  // Скрытое или свёрнутое окно отдаёт бессмысленные координаты, а у
  // развёрнутого getNormalBounds() и так возвращает размер до разворота.
  if (!mainWindow.isVisible() || mainWindow.isMinimized()) return;
  uiSettings.bounds = mainWindow.getNormalBounds();
  saveUiSettings(uiSettings);
}

function setWindowSetting(key, value) {
  if (!WINDOW_SETTING_KEYS.includes(key)) return { ok: false };
  uiSettings[key] = !!value;
  if (key === 'rememberBounds' && !uiSettings[key]) uiSettings.bounds = null;
  saveUiSettings(uiSettings);

  if (key === 'autoLaunch') applyAutoLaunch();
  if (key === 'alwaysOnTop' && mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.setAlwaysOnTop(uiSettings.alwaysOnTop);
  }
  if (key === 'rememberBounds' && uiSettings[key]) captureBounds();
  return { ok: true, autoLaunchActive: autoLaunchActive() };
}

// ══════════════════════════════════════════════
//  ОКНА
// ══════════════════════════════════════════════

function createMainWindow() {
  mainWindow = new BrowserWindow({
    title: 'Suno RPC',
    width: 980,
    height: 640,
    ...savedWindowBounds(),
    minWidth: 760,
    minHeight: 480,
    backgroundColor: '#0e0e10',
    show: false,
    icon: windowIconPath(),
    autoHideMenuBar: true,
    alwaysOnTop: uiSettings.alwaysOnTop,
    // Системная полоса заголовка спрятана, а её место занимает наша (.titlebar
    // в index.html). Кнопки свернуть/развернуть/закрыть остаются настоящими —
    // titleBarOverlay рисует их поверх клиентской области в заданных цветах,
    // так что работают и Snap Layouts, и подсветка при наведении, а цвет уже
    // не зависит от темы и акцента Windows.
    titleBarStyle: 'hidden',
    titleBarOverlay: {
      color: TITLEBAR_COLOR,
      symbolColor: TITLEBAR_SYMBOL_COLOR,
      height: TITLEBAR_HEIGHT,
    },
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.loadFile(path.join(__dirname, 'index.html'));

  // По умолчанию ✕ сворачивает в трей, а не закрывает приложение — так же вело
  // себя окно pywebview. Настоящий выход тогда только через меню трея / кнопку
  // «Выйти». Настройкой closeToTray поведение можно вернуть к обычному.
  mainWindow.on('close', (e) => {
    if (quitting) return;
    e.preventDefault();
    captureBounds();
    if (uiSettings.closeToTray) mainWindow.hide();
    else quitApp();
  });

  // «Свернуть» по настройке убирает окно в трей вместо панели задач. preventDefault
  // здесь обязателен: без него окно сначала свернётся, и после hide() в трее
  // останется невидимая свёрнутая копия, которую show() поднимает уже развёрнутой.
  mainWindow.on('minimize', (e) => {
    if (!uiSettings.minimizeToTray) return;
    e.preventDefault();
    mainWindow.hide();
  });

  mainWindow.on('resize', rememberBounds);
  mainWindow.on('move', rememberBounds);

  // Внешние ссылки (например на discord.com/developers) — в системный браузер,
  // иначе они открываются прямо в окне приложения и из него некуда вернуться.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  if (IS_DEV && process.argv.includes('--dev')) mainWindow.webContents.openDevTools({ mode: 'detach' });
}

function toggleMainWindow() {
  if (!mainWindow) return;
  if (mainWindow.isVisible()) {
    mainWindow.hide();
  } else {
    mainWindow.show();
    mainWindow.focus();
  }
}

// page: "suno" | "youtube" — какую из двух страниц отчёта открыть. Страницы
// лежат в одной папке и ссылаются друг на друга кнопкой в шапке, поэтому
// переключаться между ними можно и внутри уже открытого окна.
async function openStatsWindow(page) {
  let res;
  try {
    res = await post('generate_stats_html', { page: page === 'youtube' ? 'youtube' : 'suno' });
  } catch (e) {
    return;
  }
  if (!res.ok || !res.path || !fs.existsSync(res.path)) return;

  if (statsWindow && !statsWindow.isDestroyed()) {
    statsWindow.show();
    statsWindow.focus();
    // Именно loadFile, а не reload(): отчёт только что пересобран, и открыть
    // надо запрошенную страницу — reload() показал бы ту, что осталась с
    // прошлого раза, даже если нажата кнопка другой.
    statsWindow.loadFile(res.path);
    return;
  }
  statsWindow = new BrowserWindow({
    title: 'Suno RPC — Статистика',
    width: 1000,
    height: 760,
    backgroundColor: '#0e0e10',
    icon: windowIconPath(),
    autoHideMenuBar: true,
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  statsWindow.loadFile(res.path);
  statsWindow.on('closed', () => { statsWindow = null; });
}

// ══════════════════════════════════════════════
//  РАСШИРЕНИЯ БРАУЗЕРА
// ══════════════════════════════════════════════

// Расширения кладутся в установщик готовыми папками (fetch-extensions.js +
// extraResources), чтобы пользователю не пришлось ничего скачивать. Установить
// их ЗА пользователя приложение не может и не пытается: Chromium с 2018 года
// блокирует установку расширений мимо магазина (реестровый ключ с путём к
// локальному .crx больше не срабатывает — расширение молча отключается), а
// Firefox не грузит неподписанные XPI вовсе. Поэтому здесь только показ пути и
// открытие нужной страницы браузера — два клика вместо скачивания и поиска.
const EXTENSIONS = [
  {
    slug: 'suno-rpc-extension',
    title: 'Suno → Discord RPC',
    required: true,
    note: 'Читает плеер suno.com. Без него приложению нечего показывать в Discord.',
    firefox: false,
  },
  {
    slug: 'youtube-rpc-extension',
    title: 'YouTube → Discord RPC',
    required: false,
    note: 'Показывает видео с YouTube, когда музыка в Suno не играет.',
    firefox: true,
  },
  {
    slug: 'audio-fx-extension',
    title: 'Audio FX',
    required: false,
    note: 'С Discord не связано: панель эквалайзера, ревёрба и эффектов на suno.com.',
    firefox: true,
  },
];

// В собранном приложении папка лежит в resources рядом с бэкендом. В
// разработке extensions-bundle может быть не создана (её делает
// fetch-extensions.js) — тогда вкладка честно скажет, что папки нет, вместо
// того чтобы показывать несуществующий путь.
function extensionsDir() {
  return app.isPackaged
    ? path.join(process.resourcesPath, 'extensions')
    : path.join(__dirname, '..', 'extensions-bundle');
}

function listExtensions() {
  const root = extensionsDir();
  return EXTENSIONS.map((ext) => {
    const dir = path.join(root, ext.slug);
    let version = null;
    try {
      version = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8')).version;
    } catch (e) { /* папки нет — сборка без расширений или запуск из исходников */ }
    return { ...ext, dir, version, present: version !== null };
  });
}

// Страницу расширений нельзя открыть через shell.openExternal: схемы chrome://,
// edge:// и прочие в системе не зарегистрированы, и Windows на них ругается.
// Зато браузер принимает такой адрес аргументом командной строки — так и делаем.
const BROWSERS = [
  // Пути пишутся ЧЕРЕЗ ПРЯМЫЕ СЛЭШИ намеренно. Windows и Node их принимают
  // наравне с обратными, зато в строковом литерале не нужно ничего
  // экранировать: пара «\\» легко теряется при любой правке файла, и тогда
  // «\firefox» превращается в перевод формата плюс «irefox», путь молча
  // перестаёт находиться, а браузер просто пропадает из списка без ошибки.
  { id: 'opera-gx', title: 'Opera GX',        page: 'opera://extensions',
    paths: ['%LOCALAPPDATA%/Programs/Opera GX/opera.exe'] },
  { id: 'opera',    title: 'Opera',           page: 'opera://extensions',
    paths: ['%LOCALAPPDATA%/Programs/Opera/opera.exe'] },
  { id: 'chrome',   title: 'Chrome',          page: 'chrome://extensions',
    paths: ['%PROGRAMFILES%/Google/Chrome/Application/chrome.exe',
            '%PROGRAMFILES(X86)%/Google/Chrome/Application/chrome.exe',
            '%LOCALAPPDATA%/Google/Chrome/Application/chrome.exe'] },
  { id: 'edge',     title: 'Edge',            page: 'edge://extensions',
    paths: ['%PROGRAMFILES(X86)%/Microsoft/Edge/Application/msedge.exe',
            '%PROGRAMFILES%/Microsoft/Edge/Application/msedge.exe'] },
  { id: 'yandex',   title: 'Яндекс.Браузер',  page: 'browser://extensions',
    paths: ['%LOCALAPPDATA%/Yandex/YandexBrowser/Application/browser.exe'] },
  { id: 'brave',    title: 'Brave',           page: 'brave://extensions',
    paths: ['%PROGRAMFILES%/BraveSoftware/Brave-Browser/Application/brave.exe'] },
  { id: 'vivaldi',  title: 'Vivaldi',         page: 'vivaldi://extensions',
    paths: ['%LOCALAPPDATA%/Vivaldi/Application/vivaldi.exe'] },
  // Firefox распакованные папки не принимает совсем: в about:debugging
  // загружается либо manifest.json, либо .xpi, и то лишь до перезапуска.
  { id: 'firefox',  title: 'Firefox',         page: 'about:debugging#/runtime/this-firefox',
    firefoxOnly: true,
    paths: ['%PROGRAMFILES%/Mozilla Firefox/firefox.exe',
            '%PROGRAMFILES(X86)%/Mozilla Firefox/firefox.exe'] },
];

function expandEnv(p) {
  return p.replace(/%([^%]+)%/g, (_m, name) => process.env[name] || process.env[name.toUpperCase()] || '');
}

function findBrowsers() {
  const found = [];
  for (const b of BROWSERS) {
    for (const raw of b.paths) {
      const full = expandEnv(raw);
      if (full && fs.existsSync(full)) {
        found.push({ id: b.id, title: b.title, page: b.page, exe: full, firefoxOnly: !!b.firefoxOnly });
        break;
      }
    }
  }
  return found;
}

function openBrowserPage(id) {
  const b = findBrowsers().find((x) => x.id === id);
  if (!b) return false;
  // detached + unref: браузер должен пережить закрытие приложения, а не
  // висеть его дочерним процессом и не умирать вместе с ним.
  const child = spawn(b.exe, [b.page], { detached: true, stdio: 'ignore' });
  child.unref();
  return true;
}

// Инструкция по установке — обычная страница приложения (src/guide.html), а не
// ссылка наружу: её открывают ровно в тот момент, когда что-то не получилось, и
// интернета у пользователя может не быть. Поэтому в самой странице нет ни
// внешних шрифтов, ни картинок по ссылке.
function openGuideWindow() {
  if (guideWindow && !guideWindow.isDestroyed()) {
    guideWindow.show();
    guideWindow.focus();
    return;
  }
  guideWindow = new BrowserWindow({
    title: 'Suno RPC — Как установить расширения',
    width: 840,
    height: 780,
    minWidth: 480,
    minHeight: 420,
    backgroundColor: '#0e0e10',
    icon: windowIconPath(),
    autoHideMenuBar: true,
    // Полоса заголовка своя, как у главного окна: страницу пишем мы, значит
    // можем нарисовать её сами и не отдавать цвет системной теме.
    titleBarStyle: 'hidden',
    titleBarOverlay: {
      color: TITLEBAR_COLOR,
      symbolColor: TITLEBAR_SYMBOL_COLOR,
      height: TITLEBAR_HEIGHT,
    },
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  guideWindow.loadFile(path.join(__dirname, 'guide.html'));

  // Ссылки на suno.com и GitHub уводят в системный браузер. Без этого они
  // открылись бы прямо в окне инструкции, из которого некуда вернуться:
  // навигации назад тут нет, окно пришлось бы закрывать и открывать заново.
  guideWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
  guideWindow.webContents.on('will-navigate', (e, url) => {
    if (url !== guideWindow.webContents.getURL()) {
      e.preventDefault();
      shell.openExternal(url);
    }
  });

  guideWindow.on('closed', () => { guideWindow = null; });
}

// ══════════════════════════════════════════════
//  ВЫХОД
// ══════════════════════════════════════════════

async function quitApp() {
  if (quitting) return;
  quitting = true;
  if (stateTimer) clearInterval(stateTimer);

  // Даём бэкенду снять статус в Discord: без rpc.clear() последний трек висит
  // в профиле, пока Discord сам не заметит обрыв сокета. Ждём недолго — если
  // бэкенд завис, приложение всё равно должно закрыться.
  try {
    await Promise.race([
      post('quit'),
      new Promise((r) => setTimeout(r, 2000)),
    ]);
  } catch (e) { /* бэкенд уже мёртв — нечего чистить */ }

  if (backend) {
    backend.kill();
    backend = null;
  }
  app.quit();
}

// ══════════════════════════════════════════════
//  IPC (имена методов те же, что были у js_api pywebview)
// ══════════════════════════════════════════════

// Отдаём только кэш опроса, без обращения к бэкенду: окно запрашивает состояние
// сразу при загрузке, а бэкенд к этому моменту ещё поднимается, и запрос падал
// бы с ECONNREFUSED. Рендерер переживает null (renderState на нём выходит), а
// через секунду всё равно придёт первый push из startStatePolling.
ipcMain.handle('get_state', () => lastState);
ipcMain.handle('get_config', () => get('get_config'));
ipcMain.handle('save_config', (_e, payload) => post('save_config', payload));
ipcMain.handle('reconnect_discord', () => post('reconnect_discord'));
ipcMain.handle('disconnect_discord', () => post('disconnect_discord'));
ipcMain.handle('get_logs', () => get('get_logs'));
ipcMain.handle('get_stats_summary', () => get('get_stats_summary'));
ipcMain.handle('open_stats_window', (_e, page) => openStatsWindow(page));
ipcMain.handle('quit_app', () => quitApp());
ipcMain.handle('get_backend_log', () => backendLog.slice(-100));

// Настройки окна применяются мгновенно, по одному переключателю: кнопки
// «Сохранить» у них нет — она в интерфейсе относится к конфигу бэкенда.
ipcMain.handle('get_window_settings', () => {
  const out = { autoLaunchSupported: app.isPackaged, autoLaunchActive: autoLaunchActive() };
  for (const key of WINDOW_SETTING_KEYS) out[key] = uiSettings[key];
  return out;
});
ipcMain.handle('set_window_setting', (_e, { key, value }) => setWindowSetting(key, value));

ipcMain.handle('get_extensions', () => ({
  dir: extensionsDir(),
  items: listExtensions(),
  browsers: findBrowsers().map(({ id, title, firefoxOnly }) => ({ id, title, firefoxOnly })),
}));
ipcMain.handle('open_extensions_folder', (_e, dir) => {
  // Открываем именно папку расширения, а не выделяем её в родителе: пользователь
  // сюда пришёл, чтобы скормить этот путь браузеру, а не рассматривать соседей.
  const root = extensionsDir();
  const target = dir && path.resolve(dir).startsWith(path.resolve(root)) ? dir : root;
  return shell.openPath(target);
});
ipcMain.handle('open_browser_extensions', (_e, id) => openBrowserPage(id));
ipcMain.handle('open_guide', () => openGuideWindow());

ipcMain.handle('get_icon_settings', () => ({
  current: uiSettings.iconVariant,
  variants: ICON_VARIANTS,
}));
ipcMain.handle('set_icon_variant', (_e, id) => applyIconVariant(id));

// ══════════════════════════════════════════════
//  СТАРТ
// ══════════════════════════════════════════════

// Второй запуск не поднимает второе приложение (и второй бэкенд, который
// проиграл бы борьбу за порты), а показывает окно уже работающего.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      mainWindow.show();
      mainWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    // Читаем до создания окна и трея: иначе они успеют показаться со старой
    // иконкой и сменить её на глазах у пользователя.
    uiSettings = loadUiSettings();
    createMainWindow();

    tray = new Tray(trayIcon(false, false));
    tray.setToolTip('Suno RPC');
    tray.setContextMenu(buildTrayMenu());
    tray.on('double-click', () => toggleMainWindow());
    // Одиночный клик — по настройке. Слушатель вешаем всегда: включить и
    // выключить его на лету нельзя, а Tray.removeAllListeners убрал бы и меню.
    tray.on('click', () => { if (uiSettings.traySingleClick) toggleMainWindow(); });

    applyAutoLaunch();   // перерегистрируем: путь к exe мог измениться после переустановки

    // Запуск вместе с Windows (--autostart) всегда уходит в трей: показывать
    // окно поверх рабочего стола при входе в систему никто не просил. Обычный
    // запуск слушается настройки startMinimized. В режиме разработки окно
    // показывается всегда: иначе каждый перезапуск требует лезть в трей, а
    // падение рендерера остаётся невидимым.
    const startedBySystem = process.argv.includes('--autostart');
    if (process.argv.includes('--dev') || (!startedBySystem && !uiSettings.startMinimized)) {
      mainWindow.show();
    }

    startBackend();
    try {
      await waitForBackend();
    } catch (e) {
      // Окно всё равно показываем: там есть вкладка «Логи», и без неё
      // пользователь видел бы просто ничего не делающую иконку в трее.
      mainWindow.show();
      mainWindow.webContents.send('backend-down', {
        code: null,
        hint: e.message,
        log: backendLog.slice(-30),
      });
      return;
    }
    // Тем же путём, но при старте: бэкенд мог перезапуститься сам и не знать,
    // какую иконку выбрал пользователь.
    post('set_icon_variant', { variant: uiSettings.iconVariant }).catch(() => {});
    startStatePolling();
  });

  // Окно закрыто в трей — приложение продолжает работать, это его нормальный режим.
  app.on('window-all-closed', () => { /* намеренно пусто */ });

  app.on('before-quit', () => { quitting = true; });
}
