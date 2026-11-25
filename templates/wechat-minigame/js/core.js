// Pure logic has no platform or canvas dependency and runs directly in Node.js.
export function createState({ width, height }) {
  return { width, height, elapsedMs: 0 };
}

export function step(state, dtMs) {
  if (!Number.isFinite(dtMs) || dtMs < 0) throw new RangeError('dtMs must be a finite nonnegative number');
  return { ...state, elapsedMs: state.elapsedMs + dtMs };
}
