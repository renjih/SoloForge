// Run the real package checker against isolated minimal game directories.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const CHECK = join(dirname(fileURLToPath(import.meta.url)), '..', 'scripts', 'check.mjs');
const MB = 1024 * 1024;

function game({ ignore = [], subpackages, oldKey = false, files = {} }) {
  const dir = mkdtempSync(join(tmpdir(), 'wxgame-'));
  const put = (rel, content) => { mkdirSync(dirname(join(dir, rel)), { recursive: true }); writeFileSync(join(dir, rel), content); };
  put('game.json', JSON.stringify({ deviceOrientation: 'portrait', ...(subpackages ? { [oldKey ? 'subPackages' : 'subpackages']: subpackages } : {}) }));
  put('project.config.json', JSON.stringify({ compileType: 'game', appid: 'touristappid', packOptions: { ignore } }));
  put('game.js', 'console.log(1)\n');
  for (const [rel, size] of Object.entries(files)) put(rel, Buffer.alloc(size));
  const r = spawnSync(process.execPath, [CHECK], { cwd: dir, encoding: 'utf8' });
  rmSync(dir, { recursive: true, force: true });
  return { code: r.status, out: r.stdout + r.stderr };
}

test('a main package over 4MB fails and identifies its largest file', () => {
  const r = game({ files: { 'images/big.bin': 5 * MB } });
  assert.equal(r.code, 1);
  assert.match(r.out, /Main package 5\.00MB exceeds the 4MB/);
  assert.match(r.out, /images\/big\.bin/);
});

test('an ignored nested folder is excluded recursively', () => {
  const r = game({ ignore: [{ type: 'folder', value: 'assets/source' }], files: { 'assets/source/raw/a.bin': 5 * MB, 'assets/ok.bin': 1024 } });
  assert.equal(r.code, 0, r.out);
});

test('a folder ignore does not exclude a sibling with the same prefix', () => {
  const r = game({ ignore: [{ type: 'folder', value: 'assets/source' }], files: { 'assets/sources/a.bin': 5 * MB } });
  assert.equal(r.code, 1);
});

test('a file ignore excludes only the specified file', () => {
  const r = game({ ignore: [{ type: 'file', value: 'docs/big.bin' }], files: { 'docs/big.bin': 5 * MB } });
  assert.equal(r.code, 0, r.out);
});

test('an unsupported ignore type fails instead of being silently ignored', () => {
  const r = game({ ignore: [{ type: 'glob', value: '**/*.psd' }] });
  assert.equal(r.code, 1);
  assert.match(r.out, /glob/);
});

test('declared subpackages do not count toward the main package', () => {
  const subpackages = [{ name: 'stage1', root: 'stage1/' }, { name: 'stage2', root: 'stage2' }];
  const r = game({ subpackages, files: { 'stage1/a.bin': 2.5 * MB, 'stage2/b.bin': 2.5 * MB } });
  assert.equal(r.code, 0, r.out);
  assert.match(r.out, /Main package 0\.00MB \/ 4MB/);
  assert.match(r.out, /Main package \+ subpackages 5\.00MB \/ 30MB/);
});

test('the legacy subPackages spelling is accepted', () => {
  const r = game({ subpackages: [{ name: 's', root: 'stage1/' }], oldKey: true, files: { 'stage1/a.bin': 5 * MB } });
  assert.equal(r.code, 0, r.out);
});

test('an undeclared directory still counts toward the main package', () => {
  const r = game({ files: { 'stage1/a.bin': 5 * MB } });
  assert.equal(r.code, 1);
});

test('combined subpackages over 30MB fail', () => {
  const subpackages = [{ name: 'a', root: 'a/' }, { name: 'b', root: 'b/' }];
  const r = game({ subpackages, files: { 'a/x.bin': 16 * MB, 'b/y.bin': 16 * MB } });
  assert.equal(r.code, 1);
  assert.match(r.out, /Main package \+ subpackages 32\.00MB exceeds the 30MB/);
});

test('the total budget includes both main package and subpackages', () => {
  const r = game({ subpackages: [{ name: 'a', root: 'a/' }], files: { 'main.bin': 3 * MB, 'a/x.bin': 29 * MB } });
  assert.equal(r.code, 1);
  assert.match(r.out, /Main package 3\.00MB \/ 4MB/);
  assert.match(r.out, /Main package \+ subpackages 32\.00MB exceeds the 30MB/);
});
