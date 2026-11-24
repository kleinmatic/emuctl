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
  // by time: the missing minutes between 120 and 600 break the line
  const timed = f.segments([1, 1, 1, 1], 10, 300, 80, [0, 60, 120, 600], 60);
  assert.equal(timed.length, 2);
  assert.equal(timed[1][0].split(",")[0], "300.0");
  assert.equal(timed[0][2].split(",")[0], "60.0");
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

import * as ph from "../../src/emuctl/web/static/js/phosphor.js";

test("the console's font size keeps 80 x 24 whole, and its pixels whole", () => {
  assert.equal(ph.fontFor("amber", 1000, 700).size, 25);       // 800 x 600 fits
  assert.equal(ph.fontFor("amber", 700, 700).size, 20);        // 640 wide
  assert.equal(ph.fontFor("amber", 300, 300).size, 15);        // the least; it scrolls
  assert.equal(ph.fontFor("amber", 1600, 500).size, 20);       // the height decides
  assert.equal(ph.fontFor("modern").size, 13);
});

test("a phosphor turns every colour into its own", () => {
  const t = ph.xtermTheme("green");
  assert.equal(t.red, ph.PHOSPHORS.green.fg);
  assert.equal(t.brightBlue, ph.PHOSPHORS.green.bright);
  assert.equal(t.black, ph.PHOSPHORS.green.bg);
  assert.equal(ph.xtermTheme("modern").red, undefined);        // modern keeps the colours
});

test("a way into a guest, as a command", () => {
  assert.equal(f.reachCommand({ host: "192.0.2.10", port: 23, service: "telnet" }, "h"), "telnet 192.0.2.10");
  assert.equal(f.reachCommand({ host: null, port: 2324, service: "telnet" }, "retrohost"), "telnet retrohost 2324");
  assert.equal(f.reachCommand({ host: null, port: 2223, service: "ssh" }, "retrohost"), "ssh -p 2223 retrohost");
  assert.equal(f.reachCommand({ host: "192.0.2.10", port: 80, service: null }, "h"), "192.0.2.10:80");
});

test("sorting guest rows, with missing values last", () => {
  const rows = [{ n: "a", c: 5 }, { n: "b", c: null }, { n: "c", c: 50 }, { n: "d", c: 5 }];
  const names = (rs) => rs.map((r) => r.n).join("");
  assert.equal(names(f.sortRows(rows, (r) => r.c, "desc")), "cadb");
  assert.equal(names(f.sortRows(rows, (r) => r.c, "asc")), "adcb");
});
