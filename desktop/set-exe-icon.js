// set-exe-icon.js — прописывает иконку и описание в собранный Suno RPC.exe.
//
// Обычно это делает сам electron-builder, но здесь у него отключён
// signAndEditExecutable: чтобы редактировать exe, он тянет пакет winCodeSign, а
// внутри того лежат macOS-симлинки, распаковка которых на Windows требует прав
// администратора или включённого режима разработчика (см. AI_GUIDE.md). Без
// этого шага у .exe остаётся стандартная иконка Electron — атом, — и она видна
// в проводнике и у закреплённого на панели задач ярлыка. Иконка ОКНА при этом
// ставится в рантайме и от exe не зависит.
//
// Запускается между сборкой распакованной папки и упаковкой установщика,
// см. скрипт "dist" в package.json.

const path = require('path');
const fs = require('fs');
// rcedit 5 — ESM-пакет и экспортирует именованную функцию, а не сам модуль:
// require('rcedit') отдаёт объект, и прямой вызов падал с «rcedit is not a function».
const { rcedit } = require('rcedit');

const pkg = require('./package.json');
const EXE = path.join(__dirname, 'dist', 'win-unpacked', `${pkg.build.productName}.exe`);
const ICON = path.join(__dirname, 'src', 'icons', 'app.ico');

async function main() {
  for (const [label, file] of [['exe', EXE], ['иконка', ICON]]) {
    if (!fs.existsSync(file)) {
      console.error(`[set-exe-icon] не найден ${label}: ${file}`);
      process.exit(1);
    }
  }

  await rcedit(EXE, {
    icon: ICON,
    'version-string': {
      ProductName: pkg.build.productName,
      FileDescription: pkg.description,
      CompanyName: pkg.author,
      LegalCopyright: `MIT © ${pkg.author}`,
    },
    'file-version': pkg.version,
    'product-version': pkg.version,
  });

  console.log(`[set-exe-icon] иконка прописана в ${path.basename(EXE)}`);
}

main().catch((e) => {
  console.error('[set-exe-icon] ошибка:', e.message);
  process.exit(1);
});
