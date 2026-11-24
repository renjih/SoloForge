// Architecture checks and change detection without network calls.
// Run python3 scripts/sync_arch_gate.py after editing this file to update both workflows.
'use strict';

function globToRe(glob) {
  let re = '';
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === '*' && glob[i + 1] === '*') {
      i++;
      if (glob[i + 1] === '/') { i++; re += '(?:.*/)?'; } else re += '.*';
    } else if (c === '*') re += '[^/]*';
    else if ('\\^$+?.()|{}[]'.includes(c)) re += '\\' + c;
    else re += c;
  }
  return new RegExp('^' + re + '$');
}

function matchAny(file, globs) {
  const list = Array.isArray(globs) ? globs : [globs];
  return list.some((g) => globToRe(String(g)).test(file));
}

// Exclude documentation, tests, assets, and workflow files from source checks.
function isSource(file) {
  if (/\.(md|txt|png|jpe?g|gif|webp|svg|glb|gltf|ogg|mp3|wav)$/i.test(file)) return false;
  if (/(^|\/)(docs?|tests?|__tests__|art|assets|\.github)\//i.test(file)) return false;
  if (/\.(test|spec)\.[a-z]+$/i.test(file)) return false;
  return true;
}

function moduleOf(file) {
  const seg = file.split('/');
  if (seg.length === 1) return null;
  if (seg[0] === 'src') return seg.length > 2 ? 'src/' + seg[1] : null;
  return seg[0];
}

// The caller checks whether these directories already exist at the PR base.
function candidateDirs(files) {
  const dirs = new Set();
  for (const f of files) {
    if (f.status !== 'added' || !isSource(f.filename)) continue;
    const i = f.filename.lastIndexOf('/');
    if (i > 0) dirs.add(f.filename.slice(0, i));
  }
  return [...dirs].sort();
}

function depNames(filename, text) {
  const names = new Set();
  if (!text) return names;
  if (/(^|\/)package\.json$/.test(filename)) {
    let j;
    try { j = JSON.parse(text); } catch (e) { return names; }
    for (const k of Object.keys((j && j.dependencies) || {})) names.add(k);
  } else {
    for (const line of text.split('\n')) {
      const m = line.trim().match(/^([A-Za-z0-9][A-Za-z0-9._-]*)/);
      if (m) names.add(m[1].toLowerCase());
    }
  }
  return names;
}

// Compare dependency names to ignore version-only changes.
function depsAdded(filename, baseText, headText) {
  const before = depNames(filename, baseText);
  return [...depNames(filename, headText)].filter((n) => !before.has(n)).sort();
}

function isDepFile(file) {
  return /(^|\/)(package\.json|requirements[^/]*\.txt)$/.test(file);
}

// Summarize architecture changes in the PR file list.
function detectHits(files, opts) {
  const o = opts || {};
  const hits = [];
  const src = files.filter((f) => isSource(f.filename));
  if ((o.newDirs || []).length) hits.push('New source directories: ' + o.newDirs.join(', '));
  const saveRe = new RegExp(o.savePattern || '(save|schema|migration|\u5b58\u6863)', 'i');
  const saves = src.filter((f) => saveRe.test(f.filename)).map((f) => f.filename);
  if (saves.length) hits.push('Save or schema changes: ' + saves.slice(0, 5).join(', '));
  const shared = files.filter((f) => /^\.github\/workflows\//.test(f.filename) ||
    (o.sharedPattern && new RegExp(o.sharedPattern).test(f.filename))).map((f) => f.filename);
  if (shared.length) hits.push('CI or shared policy changes: ' + shared.slice(0, 5).join(', '));
  if ((o.newDeps || []).length) hits.push('New external dependencies: ' + o.newDeps.join(', '));
  const mods = [...new Set(src.map((f) => moduleOf(f.filename)).filter(Boolean))].sort();
  if (mods.length >= 3) hits.push('Changes across ' + mods.length + ' modules: ' + mods.join(', '));
  return hits;
}

// Apply the automated rules in docs/arch-rules.json.
// io: {list(): string[], read(path): string, exists(path): boolean}
function checkRules(io, rulesPath) {
  const p = rulesPath || 'docs/arch-rules.json';
  const out = { violations: [], errors: [], checked: 0, manual: 0 };
  if (!io.exists(p)) return out;
  let doc;
  try { doc = JSON.parse(io.read(p)); } catch (e) {
    out.errors.push(p + ' contains invalid JSON: ' + e.message);
    return out;
  }
  const rules = doc && doc.rules;
  if (!Array.isArray(rules)) { out.errors.push(p + ' requires a rules array'); return out; }
  const files = io.list();
  const seen = new Set();
  // Accept the previous marker in existing rule files.
  const manual = new Set(['manual', '\u4eba\u5de5']);
  for (const r of rules) {
    const id = r && r.id;
    if (!id || !r.rule) { out.errors.push('Rule is missing id or rule'); continue; }
    if (seen.has(id)) { out.errors.push('Duplicate rule ID: ' + id); continue; }
    seen.add(id);
    const c = r.check;
    if (!c || manual.has(c) || manual.has(c.type)) { out.manual++; continue; }
    const bad = (file, detail) => out.violations.push({ id, rule: r.rule, why: r.why || '', file, detail });
    const scope = () => files.filter((f) => matchAny(f, c.files) && !(c.except && matchAny(f, c.except)));
    try {
      if (c.type === 'forbid') {
        if (!c.files || !c.pattern) throw new Error('forbid requires files and pattern');
        const re = new RegExp(c.pattern, 'm');
        for (const f of scope()) {
          const text = io.read(f);
          const m = re.exec(text);
          if (m) bad(f, 'Line ' + text.slice(0, m.index).split('\n').length + ' matches the forbidden pattern');
        }
      } else if (c.type === 'max_lines') {
        if (!c.files || !(c.max > 0)) throw new Error('max_lines requires files and a positive max');
        for (const f of scope()) {
          const n = io.read(f).split('\n').length;
          if (n > c.max) bad(f, n + ' lines, limit ' + c.max);
        }
      } else if (c.type === 'require') {
        if (!c.path) throw new Error('require needs a path');
        if (!io.exists(c.path)) bad(c.path, 'File is missing');
      } else {
        throw new Error('Unknown check type: ' + c.type);
      }
      out.checked++;
    } catch (e) {
      out.errors.push(id + ': ' + e.message);
    }
  }
  return out;
}

module.exports = { globToRe, matchAny, isSource, moduleOf, candidateDirs, depsAdded, isDepFile, detectHits, checkRules };
