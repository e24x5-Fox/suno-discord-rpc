// Мост между рендерером (index.html) и главным процессом.
//
// Имена методов намеренно повторяют бывший js_api pywebview (snake_case), чтобы
// при переезде интерфейса на Electron код index.html остался прежним — менялась
// только инициализация. Если переименовывать их, придётся синхронно править и
// index.html, и маршруты control API в suno_rpc.py.

const { contextBridge, ipcRenderer } = require('electron');

// Имя намеренно НЕ 'api': exposeInMainWorld создаёт неконфигурируемое свойство
// globalThis, и объявление `let api` в скрипте страницы падало на нём с
// SyntaxError «Identifier 'api' has already been declared» — не в рантайме, а на
// парсинге, из-за чего весь инлайн-скрипт index.html молча не выполнялся:
// разметка рисовалась, мост работал, а ни одна функция страницы не существовала.
contextBridge.exposeInMainWorld('sunoApi', {
  get_state:          ()        => ipcRenderer.invoke('get_state'),
  get_config:         ()        => ipcRenderer.invoke('get_config'),
  save_config:        (payload) => ipcRenderer.invoke('save_config', payload),
  reconnect_discord:  ()        => ipcRenderer.invoke('reconnect_discord'),
  disconnect_discord: ()        => ipcRenderer.invoke('disconnect_discord'),
  get_logs:           ()        => ipcRenderer.invoke('get_logs'),
  get_stats_summary:  ()        => ipcRenderer.invoke('get_stats_summary'),
  open_stats_window:  (page)    => ipcRenderer.invoke('open_stats_window', page),
  quit_app:           ()        => ipcRenderer.invoke('quit_app'),
  get_backend_log:    ()        => ipcRenderer.invoke('get_backend_log'),

  // Расширения браузера лежат готовыми папками внутри установленного
  // приложения. Ставить их за пользователя нельзя (см. комментарий в main.js),
  // поэтому мост отдаёт только путь и умеет открыть нужную страницу браузера.
  get_extensions:          ()    => ipcRenderer.invoke('get_extensions'),
  open_extensions_folder:  (dir) => ipcRenderer.invoke('open_extensions_folder', dir),
  open_browser_extensions: (id)  => ipcRenderer.invoke('open_browser_extensions', id),
  open_guide:              ()    => ipcRenderer.invoke('open_guide'),

  // Настройки окна и автозапуска — тоже на стороне Electron, и применяются
  // сразу при переключении, без общей кнопки «Сохранить».
  get_window_settings: ()          => ipcRenderer.invoke('get_window_settings'),
  set_window_setting:  (key, value) => ipcRenderer.invoke('set_window_setting', { key, value }),

  // Выбор иконки живёт на стороне Electron, а не в конфиге бэкенда: трей и окно
  // должны получить её сразу при запуске, не дожидаясь бэкенда.
  get_icon_settings:  ()        => ipcRenderer.invoke('get_icon_settings'),
  set_icon_variant:   (id)      => ipcRenderer.invoke('set_icon_variant', id),

  // Редактор иконки: страница собирает картинку на canvas и отдаёт её сюда
  // готовым PNG (data:image/png;base64). Файлы кладёт главный процесс — в
  // userData, куда рендереру доступа нет.
  open_icon_editor:   ()        => ipcRenderer.invoke('open_icon_editor'),
  save_custom_icon:   (payload) => ipcRenderer.invoke('save_custom_icon', payload),
  delete_custom_icon: (id)      => ipcRenderer.invoke('delete_custom_icon', id),

  // Набор иконок изменился (добавили свою или удалили) — настройкам надо
  // перерисовать сетку, даже если сама страница ничего не нажимала.
  onIconsChanged: (cb) => ipcRenderer.on('icons-changed', () => cb()),

  // Состояние приходит пушем: опрос бэкенда идёт в главном процессе (он нужен
  // там для иконки трея, которая обновляется и при скрытом окне), поэтому
  // рендереру незачем опрашивать второй раз.
  onState: (cb) => ipcRenderer.on('state', (_e, s) => cb(s)),

  // Бэкенд не поднялся или упал — интерфейс показывает это вместо тишины.
  onBackendDown: (cb) => ipcRenderer.on('backend-down', (_e, info) => cb(info)),
});
