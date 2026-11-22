// SPDX-License-Identifier: GPL-2.0-only
// Slow paste: one character at a time, for consoles that drop fast input.
// Tested with `node --test tests/js`.

// Calls send(ch) for each character of `text`, `delayMs` apart. Returns
// {cancel, done}: done is a promise that resolves to the number of
// characters sent.
export function slowSend(text, delayMs, send, sleep = (ms) => new Promise((r) => setTimeout(r, ms))) {
  let stopped = false;
  const chars = Array.from(text);
  const done = (async () => {
    let n = 0;
    for (const ch of chars) {
      if (stopped) break;
      send(ch);
      n++;
      if (delayMs > 0 && n < chars.length) await sleep(delayMs);
    }
    return n;
  })();
  return { cancel() { stopped = true; }, done };
}
