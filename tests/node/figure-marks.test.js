// node --test tests/node/figure-marks.test.js
// How the browser reads a saved number (the same rule as figures.shown_state): answers saved before 0.6.1
// stored a number with no tool call, and a date, as "no"; they show as unverified and are not counted as
// differing. A real difference still is.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const src = fs.readFileSync(path.join(__dirname, "../../src/bench2agent/workspace/static/app.js"), "utf8");
const pick = (from, to) => src.slice(src.indexOf(from), src.indexOf(to));
const ctx = {};
vm.runInNewContext(src.match(/^const UNVERIFIED.*$/m)[0] + "\n" +
  pick("const WHY = {", "function sourceUrl") + "\nthis.shown = shown; this.figNote = figNote; this.UNVERIFIED = UNVERIFIED;", ctx);

const legacy = {t: "n", s: "13", v: "no", why: "this turn did not call the tool with these arguments"};
const date = {t: "n", s: "2026-10-21", v: "no", kind: "date", why: "date"};
const real = {t: "n", s: "63", v: "no", why: "the tool results for this context say 61"};

test("a saved no-call number and a saved date read as unverified; a real difference stays", () => {
  assert.equal(ctx.shown(legacy), "na");
  assert.equal(ctx.shown(date), "date");
  assert.equal(ctx.shown(real), "no");
  assert.match(ctx.figNote(legacy), /^Number not verified: this answer did not call the lookup it names$/);
  assert.match(ctx.figNote(date), /^Date not verified/);
  assert.match(ctx.figNote(real), /^Differs from this answer's tool results/);
});

test("the count of differing numbers uses the same reading", () => {
  const segs = [legacy, date, real, {t: "n", s: "5", v: "ok"}];
  const differs = segs.filter((s) => s.t === "n" && ctx.UNVERIFIED.has(s.v) && ctx.shown(s) === "no").length;
  assert.equal(differs, 1);
  assert.ok(src.includes('s.t === "n" && UNVERIFIED.has(s.v) && shown(s) === "no"'));   // the page counts this way
  assert.ok(src.includes('shown(s) === "no" ? " differs" : ""'));                         // and underlines this way
});
