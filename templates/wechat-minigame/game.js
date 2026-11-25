// Keep game rules in pure functions so Node.js can test them in CI.
import { createState, step } from './js/core.js';

const canvas = wx.createCanvas();
const ctx = canvas.getContext('2d');
let state = createState({ width: canvas.width, height: canvas.height });
let last = Date.now();

function frame() {
  const now = Date.now();
  state = step(state, Math.min(now - last, 50));
  last = now;
  ctx.fillStyle = '#1f4d3a';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#ffffff';
  ctx.font = '24px sans-serif';
  ctx.fillText(`Elapsed ${Math.floor(state.elapsedMs / 1000)} seconds`, 24, 64);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
