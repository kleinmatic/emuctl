// SPDX-License-Identifier: GPL-2.0-only
// node --test tests/js
import assert from "node:assert/strict";
import { test } from "node:test";

import * as f from "../../src/emuctl/web/static/js/format.js";

test("bytes", () => {
  assert.equal(f.bytes(null), f.DASH);
  assert.equal(f.bytes(512), "512 B");
  assert.equal(f.bytes(1536), "1.5 KB");
  assert.equal(f.bytes(402 * 1024 * 1024), "402 MB");
  assert.equal(f.bytes(1.1 * 1024 ** 3), "1.1 GB");
  assert.equal(f.rate(2048), "2.0 KB/s");
  assert.equal(f.rate(undefined), f.DASH);
});

test("uptime matches emuctl list", () => {
  assert.equal(f.uptime(null), f.DASH);
  assert.equal(f.uptime(258), "4:18");
  assert.equal(f.uptime(4360), "1:12:40");
  assert.equal(f.uptime(187200), "2d04h");
  assert.equal(f.longUptime(33120), "9 h 12 min");
  assert.equal(f.longUptime(45), "45 s");
  assert.equal(f.longUptime(3 * 86400 + 7200), "3 d 2 h");
});

test("percent and states", () => {
  assert.equal(f.pct(7.25), "7.3%");
  assert.equal(f.pct(null), f.DASH);
  assert.equal(f.stateClass("ready"), "st-ready");
  assert.equal(f.stateClass("x\"><script>"), "st-error");
  assert.ok(f.isUp("stuck") && !f.isUp("down") && !f.isUp("crashed"));
});

test("graph maths", () => {
  assert.equal(f.last([1, 2, null]), 2);
  assert.equal(f.last([null]), null);
  assert.equal(f.maxOf([1, null, 5], [3]), 5);
  assert.equal(f.niceMax(0), 1);
  assert.equal(f.niceMax(7.4), 10);
  assert.equal(f.niceMax(23), 25);
  assert.equal(f.niceMax(0.3, 10), 10);
  const segs = f.segments([0, 10, null, 5, 5], 10, 300, 80);
  assert.equal(segs.length, 2);
  assert.deepEqual(segs[0], ["0.0,80.0", "75.0,4.0"]);
  assert.deepEqual(f.segments([20], 10)[0], ["300.0,4.0"]);   // clipped to the top
});

import { slowSend } from "../../src/emuctl/web/static/js/keys.js";

test("slow paste sends one character at a time and can stop", async () => {
  const sent = [];
  const waits = [];
  const r = slowSend("ab\rc", 20, (c) => sent.push(c), async (ms) => { waits.push(ms); });
  assert.equal(await r.done, 4);
  assert.deepEqual(sent, ["a", "b", "\r", "c"]);
  assert.deepEqual(waits, [20, 20, 20]);
  const sent2 = [];
  const box = {};
  box.r = slowSend("abcdef", 5, (c) => sent2.push(c), async () => { await null; box.r.cancel(); });
  assert.equal(await box.r.done, 1);
  assert.equal((await slowSend("é😀", 0, () => {}).done), 2);   // by character, not byte
});

test("slow paste stops when the keys can no longer go", async () => {
  let n = 0;
  const r = slowSend("abcdef", 0, () => ++n < 3);
  assert.equal(await r.done, 2);
});
