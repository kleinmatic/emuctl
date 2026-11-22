// SPDX-License-Identifier: GPL-2.0-only
// A small line graph for the last hour, drawn as SVG.
import { h, svg } from "./dom.js";
import { clock, maxOf, niceMax, segments } from "./format.js";

// series: [{label, values}] (one or two); t: epoch seconds; fmt: value -> text
export function chart({ title, t, series, fmt, big, note, floor = 1, label }) {
  const max = niceMax(maxOf(...series.map((s) => s.values)), floor);
  const box = svg("svg", { viewBox: "0 0 300 80", preserveAspectRatio: "none", role: "img",
    "aria-label": label || `${title} over the last hour, up to ${fmt(max)}` });
  series.forEach((s, i) => {
    for (const seg of segments(s.values, max)) {
      const pts = seg.join(" ");
      if (i === 0 && series.length === 1) {
        const x0 = seg[0].split(",")[0];
        const x1 = seg[seg.length - 1].split(",")[0];
        box.append(svg("polygon", { class: "area", points: `${x0},80 ${pts} ${x1},80` }));
      }
      box.append(svg("polyline", { class: i ? "line second" : "line", points: pts }));
    }
  });
  const legend = series.length > 1
    ? h("div", { class: "legend" }, series.map((s, i) => h("span", { class: i ? "second" : "first", text: s.label })))
    : null;
  return h("section", { class: "card chart" },
    h("div", { class: "head" }, h("h2", { text: title }),
      h("span", { class: "big", text: big }), note ? h("span", { class: "muted", text: note }) : null),
    box,
    h("div", { class: "axis" }, h("span", { text: t.length ? clock(t[0]) : "" }),
      h("span", { text: `top ${fmt(max)}` }), h("span", { text: t.length ? clock(t[t.length - 1]) : "" })),
    legend);
}
