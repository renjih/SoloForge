// Exercise architecture detection, rule evaluation, and workflow copy consistency.
// Run with node scripts/test_arch_gate.js.
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const g = require('./arch_gate.js');

let n = 0;
function t(name, fn) { fn(); n++; console.log('✓ ' + name); }
const F = (filename, status) => ({ filename, status: status || 'modified' });
const io = (tree) => ({
  list: () => Object.keys(tree),
  read: (p) => tree[p],
  exists: (p) => p in tree,
});
const rules = (list) => JSON.stringify({ rules: list });

// Architecture change detection.
t('Small changes do not trigger architecture review', () => {
  assert.deepStrictEqual(g.detectHits([F('src/ui/menu.js'), F('src/ui/menu.css')], {}), []);
});
t('Documentation and test directories do not trigger architecture review', () => {
  const files = [F('docs/new/plan.md', 'added'), F('tests/border/a.test.js', 'added'), F('README.md')];
  assert.deepStrictEqual(g.candidateDirs(files), []);
  assert.deepStrictEqual(g.detectHits(files, {}), []);
});
t('A new source directory is detected', () => {
  const files = [F('src/border/gate.js', 'added'), F('src/ui/menu.js')];
  assert.deepStrictEqual(g.candidateDirs(files), ['src/border']);
  assert.strictEqual(g.detectHits(files, { newDirs: ['src/border'] }).length, 1);
});
t('A new file in an existing directory passes', () => {
  assert.deepStrictEqual(g.detectHits([F('src/ui/new.js', 'added')], { newDirs: [] }), []);
});
t('Save and schema file changes are detected', () => {
  const hits = g.detectHits([F('src/core/save.js')], {});
  assert.ok(hits[0].startsWith('Save or schema changes:'));
  assert.strictEqual(g.detectHits([F('src/core/schema.js')], {}).length, 1);
});
t('Save format tests and documentation pass', () => {
  assert.deepStrictEqual(g.detectHits([F('tests/save.test.js'), F('docs/save-format.md')], {}), []);
});
t('Workflow changes are detected', () => {
  assert.ok(g.detectHits([F('.github/workflows/ci.yml')], {})[0].startsWith('CI or shared policy changes:'));
});
t('Shared policy paths can be configured', () => {
  assert.strictEqual(g.detectHits([F('rules/SHARED.md')], {}).length, 0);
  assert.strictEqual(g.detectHits([F('rules/SHARED.md')], { sharedPattern: '^rules/' }).length, 1);
});
t('package.json detects new production dependencies only', () => {
  const base = JSON.stringify({ dependencies: { phaser: '3.80.0' }, devDependencies: {} });
  const add = JSON.stringify({ dependencies: { phaser: '3.80.0', lodash: '4.0.0' } });
  const bump = JSON.stringify({ dependencies: { phaser: '3.90.0' }, devDependencies: { vitest: '1.0.0' } });
  assert.deepStrictEqual(g.depsAdded('package.json', base, add), ['lodash']);
  assert.deepStrictEqual(g.depsAdded('package.json', base, bump), []);
  assert.strictEqual(g.detectHits([F('package.json')], { newDeps: ['lodash'] }).length, 1);
});
t('requirements.txt detects new dependencies and ignores version changes', () => {
  assert.deepStrictEqual(g.depsAdded('requirements.txt', 'pyyaml==6.0\n', 'pyyaml==6.1\npillow>=10\n'), ['pillow']);
  assert.deepStrictEqual(g.depsAdded('requirements.txt', 'pyyaml==6.0\n', '# Comment\npyyaml==6.1\n'), []);
});
t('Changes in at least three modules are detected', () => {
  const two = [F('src/ui/a.js'), F('src/core/b.js'), F('src/core/c.js')];
  const three = two.concat([F('src/events/d.js')]);
  assert.deepStrictEqual(g.detectHits(two, {}), []);
  assert.ok(g.detectHits(three, {})[0].startsWith('Changes across 3 modules:'));
});
t('All detected architecture changes are reported', () => {
  const files = [F('src/save/v2.js', 'added'), F('package.json'), F('.github/workflows/ci.yml')];
  assert.strictEqual(g.detectHits(files, { newDirs: ['src/save'], newDeps: ['zod'] }).length, 4);
});

// Architecture rule evaluation.
t('Projects without an architecture rules file pass', () => {
  const r = g.checkRules(io({ 'src/a.js': '' }));
  assert.deepStrictEqual([r.violations.length, r.errors.length, r.checked], [0, 0, 0]);
});
t('Forbidden engine imports report a file and line', () => {
  const r = g.checkRules(io({
    'docs/arch-rules.json': rules([{ id: 'A-01', rule: 'The engine must be independent of the game', why: 'Keep the engine reusable',
      check: { type: 'forbid', files: 'src/engine/**', pattern: "from ['\"]\\.\\./game" } }]),
    'src/engine/core.js': "const a = 1;\nimport x from '../game/level';\n",
    'src/game/level.js': "import e from '../engine/core';\n",
  }));
  assert.strictEqual(r.violations.length, 1);
  assert.strictEqual(r.violations[0].file, 'src/engine/core.js');
  assert.ok(r.violations[0].detail.startsWith('Line 2 '));
});
t('Forbidden patterns are ignored in excluded files', () => {
  const rule = { id: 'A-01', rule: 'r', check: { type: 'forbid', files: 'src/**', pattern: 'localStorage', except: 'src/core/save.js' } };
  const r = g.checkRules(io({
    'docs/arch-rules.json': rules([rule]),
    'src/core/save.js': 'localStorage.setItem()', 'src/ui/menu.js': 'render()',
  }));
  assert.deepStrictEqual([r.violations.length, r.checked], [0, 1]);
});
t('Only files over the line limit fail', () => {
  const rule = { id: 'A-02', rule: 'Files must have at most three lines', check: { type: 'max_lines', files: 'src/**/*.js', max: 3 } };
  const r = g.checkRules(io({ 'docs/arch-rules.json': rules([rule]), 'src/a.js': '1\n2\n3\n4\n5', 'src/x/b.js': '1\n2' }));
  assert.deepStrictEqual(r.violations.map((v) => v.file), ['src/a.js']);
});
t('A missing required file fails', () => {
  const rule = { id: 'A-03', rule: 'Document save migrations', check: { type: 'require', path: 'docs/SAVE_MIGRATION.md' } };
  assert.strictEqual(g.checkRules(io({ 'docs/arch-rules.json': rules([rule]) })).violations.length, 1);
});
t('Manual rules are counted without evaluation', () => {
  const r = g.checkRules(io({ 'docs/arch-rules.json': rules([
    { id: 'A-04', rule: 'Document new systems before implementation' },
    { id: 'A-05', rule: 'Review public interfaces', check: 'manual' },
    { id: 'A-06', rule: 'Review storage changes', check: { type: 'manual' } },
  ]) }));
  assert.deepStrictEqual([r.manual, r.checked, r.violations.length, r.errors.length], [3, 0, 0, 0]);
});
t('Malformed rules files report errors', () => {
  assert.strictEqual(g.checkRules(io({ 'docs/arch-rules.json': '{ rules: [' })).errors.length, 1);
  assert.strictEqual(g.checkRules(io({ 'docs/arch-rules.json': '{}' })).errors.length, 1);
});
t('Unknown checks, missing fields, and duplicate IDs report errors', () => {
  const r = g.checkRules(io({ 'docs/arch-rules.json': rules([
    { id: 'A-01', rule: 'r', check: { type: 'forbidd', files: '**', pattern: 'x' } },
    { id: 'A-02', rule: 'r', check: { type: 'forbid', files: '**' } },
    { id: 'A-02', rule: 'r' },
    { rule: 'Missing ID' },
  ]) }));
  assert.strictEqual(r.errors.length, 4);
});
t('Double-star globs match across directories', () => {
  assert.ok(g.matchAny('src/a/b/c.js', 'src/**/*.js'));
  assert.ok(g.matchAny('src/c.js', 'src/**/*.js'));
  assert.ok(!g.matchAny('src/a/c.js', 'src/*.js'));
  assert.ok(!g.matchAny('lib/c.js', ['src/**', 'app/**']));
});

// Reusable workflows embed the same architecture module.
for (const name of ['gate.yml', 'merge-bot.yml']) {
  t(name + ' contains the current embedded copy', () => {
    const yml = fs.readFileSync(path.join(__dirname, '..', '.github', 'workflows', name), 'utf8');
    const src = fs.readFileSync(path.join(__dirname, 'arch_gate.js'), 'utf8').trimEnd().split('\n');
    const blocks = yml.split('// >>> arch_gate.js').slice(1).map((b) => b.split('// <<< arch_gate.js')[0]);
    assert.strictEqual(blocks.length, 1, name + ' should have exactly one embedded copy');
    const lines = blocks[0].split('\n').slice(1, -1).map((l) => l.replace(/^ {14}/, '').trimEnd());
    assert.deepStrictEqual(lines, src.map((l) => l.trimEnd()), 'Run python3 scripts/sync_arch_gate.py to synchronize the embedded copy');
  });
}

console.log('\n' + n + ' architecture checks passed');
