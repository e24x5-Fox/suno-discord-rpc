// Складывает расширения браузера в extensions-bundle/, откуда electron-builder
// кладёт их в установщик (extraResources в package.json).
//
// Расширения живут в отдельных репозиториях, и берём мы их ОТТУДА, а не из
// соседних папок на диске. Это не паранойя: копия audio-fx-extension, лежавшая
// когда-то внутри этого репозитория, отстала от рабочей на восемь минорных
// версий, и никто этого не замечал, пока не полез сверять файлы. Опубликованный
// репозиторий — единственный источник правды, и версию каждого расширения
// скрипт печатает вслух, чтобы расхождение было видно сразу.
//
//     node fetch-extensions.js          — обновить (клонирует заново)
//     node fetch-extensions.js --keep   — не перекачивать, если папка уже есть
//
// Требует git в PATH и доступ в сеть. Без сети собрать установщик с
// расширениями нельзя — это осознанный компромисс в пользу единого источника.

const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const OWNER = 'e24x5-Fox';
const SLUGS = ['suno-rpc-extension', 'youtube-rpc-extension', 'audio-fx-extension'];
const OUT = path.join(__dirname, 'extensions-bundle');

// Файлы репозитория, которым в браузере делать нечего. README оставляем: он
// открывается рядом с папкой и объясняет, что это вообще такое.
const DROP = ['.git', '.gitignore', 'build.py', 'dist'];

const keep = process.argv.includes('--keep');

function rm(target) {
  fs.rmSync(target, { recursive: true, force: true });
}

function fetchOne(slug) {
  const dest = path.join(OUT, slug);
  if (keep && fs.existsSync(path.join(dest, 'manifest.json'))) {
    return report(slug, dest, 'оставлено как есть');
  }
  rm(dest);
  execFileSync('git', [
    'clone', '--depth', '1', '--quiet',
    `https://github.com/${OWNER}/${slug}.git`, dest,
  ], { stdio: ['ignore', 'ignore', 'inherit'] });

  for (const name of DROP) rm(path.join(dest, name));
  report(slug, dest, 'склонировано');
}

function report(slug, dest, what) {
  const manifestPath = path.join(dest, 'manifest.json');
  if (!fs.existsSync(manifestPath)) {
    throw new Error(`${slug}: manifest.json не найден — расширение не собрано`);
  }
  const m = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  console.log(`  ${slug.padEnd(24)} v${(m.version || '?').padEnd(8)} ${what}`);
}

fs.mkdirSync(OUT, { recursive: true });
console.log('[extensions] складываю расширения в extensions-bundle/');
for (const slug of SLUGS) fetchOne(slug);
console.log('[extensions] готово');
