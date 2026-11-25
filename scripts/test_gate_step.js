// Run the actual workflow scripts against temporary files and mocked GitHub APIs.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const OWNER = 'example-maintainer';
const REPO = 'example-game';
const FULL_NAME = OWNER + '/' + REPO;
const HEAD_SHA = 'bead' + '0'.repeat(36);
const OLD_SHA = 'abcd' + '1'.repeat(36);
const PR_NUMBER = 7;

function workflowScript(name, pattern) {
  const yaml = fs.readFileSync(path.join(__dirname, '..', '.github', 'workflows', name), 'utf8').replace(/\r\n/g, '\n');
  const match = yaml.match(pattern);
  assert.ok(match, 'Expected a script block in ' + name);
  const source = match[1].split('\n').map((line) => line.replace(/^ {12}/, '')).join('\n');
  assert.ok(!source.includes('$' + '{{'), 'Workflow expressions must stay outside scripts');
  return new AsyncFunction('github', 'context', 'core', 'require', 'process', 'console', source);
}

const gateScript = workflowScript('gate.yml', /- name: Architecture rules[\s\S]*?script: \|\n([\s\S]*?)\n      - name:/);
const botScript = workflowScript('merge-bot.yml', /script: \|\n([\s\S]*)$/);
const quiet = { log: () => {} };

async function runGate({ comment = true } = {}) {
  const calls = [];
  const warnings = [];
  let failed = null;
  const github = { rest: { issues: { createComment: async (params) => calls.push(params) } } };
  const context = { repo: { owner: OWNER, repo: REPO } };
  const core = {
    warning: (warning) => warnings.push(warning),
    setFailed: (reason) => { failed = reason; },
    setOutput: () => {},
  };
  const env = { PR_NUMBER: comment ? String(PR_NUMBER) : '' };
  await gateScript(github, context, core, require, { env }, quiet);
  return { calls, warnings, failed };
}

const review = (sha = HEAD_SHA, login = OWNER) => ({
  user: { login },
  body: '## Review record\nReviewed commit: ' + sha,
});

async function runBot({
  branch = 'codex/task',
  files = [],
  labels = [],
  headRepo = FULL_NAME,
  event = 'schedule',
  tree = {},
  head = {},
  env = {},
  apiDown = false,
  runPages = [['success']],
  notes = [review()],
  fullHead = HEAD_SHA,
  mergeHead = HEAD_SHA,
  mergeable = 'clean',
  draft = false,
  fullDraft = draft,
  title = 'Update the example game',
} = {}) {
  const pr = {
    number: PR_NUMBER,
    state: 'open',
    draft,
    title,
    created_at: '2026-01-01T00:00:00Z',
    labels: labels.map((name) => ({ name })),
    head: { ref: branch, sha: HEAD_SHA, repo: headRepo && { full_name: headRepo } },
    base: { ref: 'main', sha: 'base', repo: { full_name: FULL_NAME } },
  };
  const calls = [];
  const mergeRequests = [];
  const jobs = new Map();
  let gateReads = 0;
  let summary = [];

  const listPulls = () => {};
  const listFiles = () => {};
  const listComments = ({ per_page, page }) => notes.slice((page - 1) * per_page, page * per_page);
  const github = {
    paginate: async (method, args) => {
      if (method === listPulls) return [pr];
      if (method === listFiles) return files;
      assert.equal(method, listComments);
      const all = [];
      for (let page = 1; ; page++) {
        const comments = method({ ...args, page });
        all.push(...comments);
        if (comments.length < args.per_page) return all;
      }
    },
    rest: {
      pulls: {
        list: listPulls,
        listFiles,
        get: async () => ({ data: { ...pr, draft: fullDraft, head: { ...pr.head, sha: fullHead }, mergeable_state: mergeable } }),
        merge: async (request) => {
          mergeRequests.push(request);
          if (request.sha !== mergeHead) throw new Error('Head SHA mismatch');
          calls.push('MERGE');
          return { data: { merged: true } };
        },
      },
      issues: {
        addLabels: async ({ labels }) => calls.push('label:' + labels.join(',')),
        createComment: async ({ body }) => calls.push(body.includes('arch-detect') ? 'comment:detect' : 'comment:merge'),
        listComments,
      },
      actions: {
        listWorkflowRunsForRepo: async ({ head_sha, event, page, per_page }) => {
          gateReads++;
          assert.equal(head_sha, HEAD_SHA);
          assert.equal(event, 'pull_request');
          assert.equal(per_page, 30);
          const runs = (runPages[page - 1] || []).map((conclusion, index) => {
            const id = page * 1000 + index;
            const date = new Date(Date.UTC(2026, 0, 1) - id * 1000).toISOString();
            jobs.set(id, {
              name: 'gate / gate', status: conclusion === 'running' ? 'in_progress' : 'completed',
              conclusion, started_at: date,
            });
            return { id, created_at: date, head_repository: pr.head.repo, pull_requests: [{ number: PR_NUMBER }] };
          });
          return { data: { workflow_runs: runs } };
        },
        listJobsForWorkflowRun: async ({ run_id }) => ({ data: { jobs: [jobs.get(run_id)] } }),
      },
      repos: {
        getContent: async ({ path: name, ref }) => {
          if (apiDown) throw Object.assign(new Error('GitHub unavailable'), { status: 500 });
          const contents = ref === HEAD_SHA ? head : tree;
          if (!Object.hasOwn(contents, name)) throw Object.assign(new Error('Not found'), { status: 404 });
          return { data: contents[name] === null ? [] : { content: Buffer.from(contents[name]).toString('base64') } };
        },
      },
    },
  };
  const context = { repo: { owner: OWNER, repo: REPO }, eventName: event };
  const sum = { addHeading: () => sum, addList: (items) => { summary = items; return sum; }, write: async () => {} };
  const workflowEnv = { REPO_OWNER: OWNER, ARCH_DETECT: 'auto', ARCH_BRANCHES: '^codex/', ARCH_PATHS: '', ARCH_SHARED: '', ...env };
  await botScript(github, context, { summary: sum }, require, { env: workflowEnv }, quiet);
  return { calls, mergeRequests, gateReads, note: summary.join('\n') };
}

let count = 0;
function pass(message) {
  count++;
  console.log('PASS ' + message);
}

async function testArchitectureStep() {
  const write = (name, content) => {
    fs.mkdirSync(path.dirname(name), { recursive: true });
    fs.writeFileSync(name, content);
    execFileSync('git', ['add', '--', name]);
  };
  const green = { calls: [], warnings: [], failed: null };
  write('src/engine/core.js', 'ok');
  assert.deepEqual(await runGate(), green);
  pass('Gate passes without a rules file');

  write('docs/arch-rules.json', JSON.stringify({ rules: [{
    id: 'A-01',
    rule: 'The engine must be independent of the game',
    check: { type: 'forbid', files: 'src/engine/**', pattern: '\\.\\./game/' },
  }] }));
  assert.deepEqual(await runGate(), green);
  pass('Gate passes when the rules are satisfied');

  write('src/engine/core.js', "import '../game/level'");
  const failed = await runGate();
  assert.equal(failed.failed, 'Architecture rules failed');
  assert.deepEqual(failed.warnings, []);
  assert.equal(failed.calls.length, 1);
  assert.match(failed.calls[0].body, /A-01.*\n?.*Line 1/);
  pass('Gate fails a rule violation and comments on a same-repository pull request');

  const serverOnly = { calls: [], warnings: [], failed: 'Architecture rules failed' };
  assert.deepEqual(await runGate({ comment: false }), serverOnly);
  pass('Gate reports violations on pushes and fork pull requests through the job result');

  write('docs/arch-rules.json', '{invalid');
  assert.equal((await runGate()).failed, 'Architecture rules failed');
  pass('Gate fails a malformed rules file');
}

async function testBot() {
  const tree = { 'docs/AUTOPILOT.md': 'enabled', 'src/engine': null, 'package.json': JSON.stringify({ dependencies: { phaser: '1' } }) };
  const head = { 'package.json': JSON.stringify({ dependencies: { phaser: '1', zod: '1' } }) };
  const small = [{ filename: 'src/engine/core.js', status: 'modified' }];
  const arch = [{ filename: 'src/border/gate.js', status: 'added' }, { filename: 'package.json', status: 'modified' }];
  const tagged = ['label:needs-arch', 'comment:detect'];
  const p3 = { files: small, labels: ['P3'] };

  async function bot(name, options, calls, notePattern) {
    const result = await runBot({ tree, head, ...options });
    assert.deepEqual(result.calls, calls, name);
    if (notePattern) assert.match(result.note, notePattern, name);
    pass(name);
    return result;
  }

  await bot('Small changes do not add an architecture label', { files: small }, []);
  await bot('New directories and dependencies add needs-arch', { files: arch }, tagged, /architecture changes/);
  await bot('Reviewed P3 pull requests with a green gate merge', p3, ['MERGE']);
  await bot('Reviewed P2 pull requests announce the merge', { ...p3, labels: ['P2'] }, ['MERGE', 'comment:merge']);
  await bot('P0 blocks a pull request that also has P3', { ...p3, labels: ['P0', 'P3'] }, [], /Blocked by P0/);
  await bot('P1 blocks a pull request that also has P2', { ...p3, labels: ['P1', 'P2'] }, [], /Blocked by P1/);
  await bot('Draft pull requests cannot merge', { ...p3, draft: true }, [], /Draft pull request/);
  await bot('A pull request converted to draft during the run cannot merge', { ...p3, fullDraft: true }, [], /Draft pull request/);
  await bot('New architecture changes block a P3 pull request', { ...p3, files: arch }, tagged, /Blocked by needs-arch/);
  await bot('An existing needs-arch label blocks merge without a duplicate comment', { ...p3, files: arch, labels: ['P3', 'needs-arch'] }, [], /Blocked by needs-arch/);
  await bot('An architecture veto blocks merge', { ...p3, files: arch, labels: ['P3', 'arch-veto'] }, [], /Blocked by arch-veto/);
  await bot('Architecture approval prevents relabeling', { ...p3, files: arch, labels: ['P3', 'arch-ok'] }, ['MERGE']);
  await bot('Branches outside the configured pattern skip automatic labeling', { branch: 'contributor/notes', files: arch }, []);
  await bot('Architecture detection can be disabled', { files: arch, env: { ARCH_DETECT: 'off' } }, []);
  await bot('Auto mode waits for the base policy file', { files: arch, tree: {} }, []);
  await bot('On mode enables detection without a base policy file', { files: arch, tree: {}, env: { ARCH_DETECT: 'on' } }, tagged);
  await bot('Architecture API errors are logged', { files: arch, apiDown: true, env: { ARCH_DETECT: 'on' } }, [], /Could not detect/);
  await bot('Architecture API failure blocks an otherwise eligible pull request', {
    ...p3, files: arch, apiDown: true, env: { ARCH_DETECT: 'on' },
  }, [], /Could not detect/);
  await bot('Branch and shared policy patterns are configurable', {
    branch: 'task/policy', files: [{ filename: 'rules/SHARED.md', status: 'modified' }],
    env: { ARCH_DETECT: 'on', ARCH_BRANCHES: '^task/', ARCH_SHARED: '^rules/' },
  }, tagged);

  const skipped = Array(30).fill('skipped');
  await bot('Skipped gates on the first page leave a green gate on the next page eligible', { ...p3, runPages: [skipped, ['success']] }, ['MERGE']);
  await bot('A failed gate on the next page blocks merge', { ...p3, runPages: [skipped, ['failure']] }, [], /Gate result: failure/);
  await bot('A newer running gate blocks merge', { ...p3, runPages: [['running', 'success']] }, [], /Gate is still running/);
  await bot('Only skipped gates leave the pull request waiting', { ...p3, runPages: [['skipped']] }, [], /Gate has not run/);
  await bot('The gate search stops after 300 runs', { ...p3, runPages: Array(11).fill(skipped) }, [], /No gate found in 300 runs/);
  await bot('A branch behind its base gets needs-rebase', { ...p3, mergeable: 'behind' }, ['label:needs-rebase']);

  const fork = { ...p3, headRepo: 'example-contributor/' + REPO };
  const scheduled = await bot('A reviewed fork pull request never merges and receives needs-owner on schedule', fork, ['label:needs-owner'], /Fork pull request/);
  assert.equal(scheduled.gateReads, 0);
  assert.deepEqual(scheduled.mergeRequests, []);
  await bot('A different head repository is a fork even when its owner matches', { ...p3, headRepo: OWNER + '/demo-app' }, ['label:needs-owner']);
  await bot('An already labeled fork is left for the owner', { ...fork, labels: ['P3', 'needs-owner'] }, []);
  await bot('Only scheduled runs label forks', { ...fork, event: 'pull_request' }, []);
  await bot('A missing head repository cannot merge', { ...p3, headRepo: null }, ['label:needs-owner']);

  const waiting = /Waiting for an owner review record/;
  await bot('A stale review SHA blocks merge', { ...p3, notes: [review(OLD_SHA)] }, [], waiting);
  await bot('A shortened head SHA blocks merge', { ...p3, notes: [review(HEAD_SHA.slice(0, 7))] }, [], waiting);
  await bot('An invalid SHA line blocks merge', { ...p3, notes: [review(HEAD_SHA + 'a')] }, [], waiting);
  await bot('An old pull request still needs an owner review record', { ...p3, notes: [] }, [], waiting);
  await bot('A valid-looking record from a contributor blocks merge', { ...p3, notes: [review(HEAD_SHA, 'example-contributor')] }, [], waiting);
  await bot('A Codex-named account cannot approve a merge', { ...p3, notes: [review(HEAD_SHA, 'example-codex-account')] }, [], waiting);
  await bot('An owner comment without a SHA blocks merge', { ...p3, notes: [{ ...review(), body: '## Review record\nApproved' }] }, [], waiting);
  await bot('A review heading embedded in a comment blocks merge', { ...p3, notes: [{ ...review(), body: 'Quoted record\n' + review().body }] }, [], waiting);
  await bot('An ambiguous review record blocks merge', { ...p3, notes: [{ ...review(), body: review().body + '\nReviewed commit: ' + OLD_SHA }] }, [], waiting);
  const chatter = Array.from({ length: 100 }, () => ({ user: { login: OWNER }, body: 'Discussion' }));
  await bot('The matching review record can be on a later comments page', { ...p3, notes: [review(OLD_SHA), ...chatter, review()] }, ['MERGE']);

  const command = '$(printf executed > comment-marker)';
  const result = await bot('Shell syntax in a valid review comment remains literal', {
    ...p3, notes: [{ ...review(), body: review().body + '\n' + command }],
  }, ['MERGE']);
  assert.equal(fs.existsSync('comment-marker'), false);
  assert.equal(result.mergeRequests[0].sha, HEAD_SHA);
  await bot('A head change during the run blocks merge', { ...p3, fullHead: OLD_SHA }, [], /Pull request changed/);
  const raced = await bot('The merge API receives the reviewed SHA to reject a racing push', { ...p3, mergeHead: OLD_SHA }, [], /Head SHA mismatch/);
  assert.equal(raced.mergeRequests[0].sha, HEAD_SHA);
}

async function main() {
  const cwd = process.cwd();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gate-step-'));
  try {
    process.chdir(dir);
    execFileSync('git', ['init', '--quiet']);
    await testArchitectureStep();
    await testBot();
    console.log('\n' + count + ' workflow script checks passed');
  } finally {
    process.chdir(cwd);
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
