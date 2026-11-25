import test from 'node:test';
import assert from 'node:assert/strict';
import { createState, step } from '../js/core.js';

test('step accumulates time without changing the original state', () => {
  const s0 = createState({ width: 375, height: 667 });
  const s1 = step(s0, 16);
  assert.equal(s1.elapsedMs, 16);
  assert.equal(s0.elapsedMs, 0);
});

test('step rejects negative and nonnumeric durations', () => {
  const s0 = createState({ width: 1, height: 1 });
  assert.throws(() => step(s0, -1), RangeError);
  assert.throws(() => step(s0, NaN), RangeError);
});
