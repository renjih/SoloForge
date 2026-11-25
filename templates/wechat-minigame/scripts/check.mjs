// Validate config, JavaScript syntax, pure logic, and package-size budgets.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { execFileSync } from 'node:child_process';

const ROOT = process.cwd();
const MAIN_PACKAGE_LIMIT = 4 * 1024 * 1024;
const TOTAL_LIMIT = 30 * 1024 * 1024;
const problems = [];
const ok = (msg) => console.log(`✓ ${msg}`);

function readJson(file) {
  try { return JSON.parse(readFileSync(join(ROOT, file), 'utf8')); }
  catch (e) { problems.push(`${file} is not valid JSON: ${e.message}`); return null; }
}

// 1. Configuration.
const game = readJson('game.json');
const project = readJson('project.config.json');
if (game && !['portrait', 'landscape'].includes(game.deviceOrientation ?? 'portrait')) {
  problems.push('game.json deviceOrientation must be portrait or landscape');
}
if (project) {
  if (project.compileType !== 'game') problems.push('project.config.json compileType must be game');
  if (!project.appid) problems.push('project.config.json requires appid; use touristappid for local development');
}
if (game && project) ok('game.json / project.config.json');

// 2. Collect files, skipping hidden directories and node_modules.
// Reject unsupported ignore types rather than silently miscounting package sizes.
const norm = (p) => String(p ?? '').replace(/^\.\//, '').replace(/\/+$/, '');
const ignoreRules = [];
for (const i of project?.packOptions?.ignore ?? []) {
  if (i.type === 'folder' || i.type === 'file') ignoreRules.push({ type: i.type, value: norm(i.value) });
  else problems.push(`project.config.json packOptions.ignore has unsupported type "${i.type}" (${i.value}); only folder / file are supported`);
}
const isIgnored = (rel) => ignoreRules.some((r) => (r.type === 'file' ? rel === r.value : rel === r.value || rel.startsWith(r.value + '/')));
function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    if (name.startsWith('.') || name === 'node_modules') continue;
    const full = join(dir, name);
    const rel = relative(ROOT, full).split(sep).join('/');
    const st = statSync(full);
    if (st.isDirectory()) out.push(...walk(full));
    else out.push({ full, rel, size: st.size });
  }
  return out;
}
const all = walk(ROOT);
const shipped = all.filter((f) => !isIgnored(f.rel));

// Declared subpackage roots are excluded from the main package.
const subRoots = (game?.subpackages ?? game?.subPackages ?? []).map((p) => norm(p.root)).filter(Boolean);
const inSub = (rel) => subRoots.some((r) => rel === r || rel.startsWith(r + '/'));

// 3. Syntax.
const jsFiles = all.filter((f) => /\.(m?js)$/.test(f.rel));
let before = problems.length;
for (const f of jsFiles) {
  try { execFileSync(process.execPath, ['--check', f.full], { stdio: 'pipe' }); }
  catch (e) { problems.push(`Syntax error in ${f.rel}: ${String(e.stderr).split('\n').find(Boolean)}`); }
}
if (problems.length === before) ok(`Syntax checked ${jsFiles.length} JavaScript files`);
before = problems.length;

// 4. Pure logic must not call platform or canvas APIs.
for (const f of jsFiles.filter((f) => /^js\/(core|logic\/)/.test(f.rel))) {
  const src = readFileSync(f.full, 'utf8');
  if (/\bwx\./.test(src) || /getContext\(/.test(src)) problems.push(`${f.rel} is a logic module and must not call wx / canvas directly`);
}
if (problems.length === before) ok('Logic modules have no platform dependency');

// 5. Package sizes. Preserve the template's main and combined package budgets.
const mb = (n) => (n / 1024 / 1024).toFixed(2);
const biggest = (files) => [...files].sort((a, b) => b.size - a.size).slice(0, 5).map((f) => `${f.rel} ${mb(f.size)}MB`).join('; ');
const mainFiles = shipped.filter((f) => !inSub(f.rel));
const subFiles = shipped.filter((f) => inSub(f.rel));
const total = mainFiles.reduce((n, f) => n + f.size, 0);
if (total > MAIN_PACKAGE_LIMIT) {
  problems.push(`Main package ${mb(total)}MB exceeds the 4MB budget. Largest files: ${biggest(mainFiles)} (use subpackages or a CDN for large media)`);
} else {
  ok(`Main package ${mb(total)}MB / 4MB`);
}
if (subRoots.length) {
  const grand = total + subFiles.reduce((n, f) => n + f.size, 0);
  if (grand > TOTAL_LIMIT) problems.push(`Main package + subpackages ${mb(grand)}MB exceeds the 30MB budget. Largest files: ${biggest(shipped)} (use a CDN)`);
  else ok(`Main package + subpackages ${mb(grand)}MB / 30MB (${subRoots.length} subpackages)`);
}

if (problems.length) {
  console.error('\n✗ Gate failed:');
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}
