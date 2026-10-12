// node --test tests/node/page.test.js
// The page controller against a fake server: a question stays with the
// conversation it was asked in when the user switches away, its events do not
// reach the other conversation's screen, cancel targets the asking
// conversation, and the finished answer is there when that conversation is
// reopened.
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const App = require(path.join(__dirname, "../../src/bench2agent/web/static/app.js"));

function fakeServer() {
  const convs = {A: {cid: "A", title: "A", revision: "v1", provider: "openai", model: "m", turns: []},
                 B: {cid: "B", title: "B", revision: "v1", provider: "openai", model: "m", turns: []}};
  const pending = {};          // cid -> resolve function that lets the answer finish
  const cancels = [];
  const json = (status, body) => ({ok: status < 400, status, json: async () => body, statusText: String(status)});
  async function* events(cid) {
    yield {t: "tool", name: "benchmark_usage", arguments: {}};
    const outcome = await new Promise(resolve => { pending[cid] = resolve; });
    if (outcome === "turn") {
      const turn = {q: "q-" + cid, segs: [{t: "p", s: "answer for " + cid}], verified: {}, trail: []};
      convs[cid].turns.push(turn);
      yield {t: "turn", turn, conversation: {cid}};
    } else {
      yield {t: "error", cancelled: true, message: "cancelled"};
    }
  }
  const fetch = async (url, opts) => {
    const m = url.match(/^\/api\/(me|conversations)(?:\/([A-Z]))?(?:\/(ask|cancel))?$/);
    if (!m) return json(404, {detail: "no"});
    if (m[1] === "me") return json(200, {name: "u", credentials: "service", attempts_today: 0, answers_today: 0, turns_per_day: 5});
    if (!m[2]) return json(200, {conversations: Object.values(convs).map(c => ({cid: c.cid, title: c.title, revision: c.revision}))});
    if (m[3] === "ask") return {ok: true, status: 200, body: events(m[2])};
    if (m[3] === "cancel") { cancels.push(m[2]); if (pending[m[2]]) pending[m[2]]("cancelled"); return json(200, {cancelled: true, message: "Cancelled; nothing will be saved."}); }
    return json(200, convs[m[2]]);
  };
  const ndjson = async function* (response) { for await (const ev of response.body) yield ev; };
  return {fetch, ndjson, finish: (cid, outcome) => pending[cid](outcome), cancels, convs};
}

function recordingView() {
  const log = [];
  const rec = name => (...args) => log.push([name, ...args.map(a => (a && a.cid) || (typeof a === "object" && a !== null ? JSON.stringify(a).slice(0, 40) : a))]);
  return {log, showUser: rec("showUser"), showSignedOut: rec("showSignedOut"), showList: rec("showList"),
          showConversation: rec("showConversation"), setBusy: rec("setBusy"), showEvent: rec("showEvent"),
          appendTurn: rec("appendTurn"), showError: rec("showError"), showStatus: rec("showStatus"), notifyFinished: rec("notifyFinished")};
}

test("a question asked in A does not reach B's screen; A shows it when reopened", async () => {
  const server = fakeServer();
  const view = recordingView();
  const app = App.create({fetch: server.fetch, ndjson: server.ndjson, view});
  await app.connect();
  await app.open("A");
  const asking = app.ask("question for A");
  await new Promise(r => setTimeout(r, 10));          // the tool event arrives while A is open
  assert.ok(view.log.some(e => e[0] === "showEvent" && e[1] === "A"));
  await app.open("B");                                  // the user switches away before A finishes
  view.log.length = 0;
  server.finish("A", "turn");
  const outcome = await asking;
  assert.equal(outcome.t, "turn");
  assert.ok(!view.log.some(e => e[0] === "appendTurn"), "A's answer must not be appended to B's screen");
  assert.ok(!view.log.some(e => e[0] === "showEvent"), "no A events on B's screen");
  assert.ok(view.log.some(e => e[0] === "notifyFinished" && e[1] === "A"));
  assert.ok(!view.log.some(e => e[0] === "setBusy" && e[2] === false), "B's ask button is not touched by A's job");
  assert.equal(app.state.jobs.A, undefined);
  const reopened = await app.open("A");                 // the server kept the turn under A
  assert.deepEqual(reopened.turns.map(t => t.q), ["q-A"]);
  assert.ok(view.log.some(e => e[0] === "showConversation" && e[1] === "A"));
  const b = await app.open("B");
  assert.deepEqual(b.turns, []);
});

test("cancel goes to the conversation that asked, even when another one is open", async () => {
  const server = fakeServer();
  const view = recordingView();
  const app = App.create({fetch: server.fetch, ndjson: server.ndjson, view});
  await app.connect();
  await app.open("A");
  const asking = app.ask("question for A");
  await new Promise(r => setTimeout(r, 10));
  await app.open("B");
  const r = await app.cancel("A");                      // the cancel button of the asking conversation's job
  assert.equal(r.cancelled, true);
  assert.deepEqual(server.cancels, ["A"]);
  const outcome = await asking;
  assert.equal(outcome.t, "error");
  assert.ok(!view.log.some(e => e[0] === "showError"), "A's cancellation is not shown on B's screen");
  const none = await app.cancel("B");                   // nothing runs in B
  assert.equal(none.cancelled, false);
  assert.deepEqual(server.cancels, ["A"]);
  assert.deepEqual((await app.open("A")).turns, []);
});

test("one question at a time per conversation, and the open conversation gets its own events", async () => {
  const server = fakeServer();
  const view = recordingView();
  const app = App.create({fetch: server.fetch, ndjson: server.ndjson, view});
  await app.connect();
  await app.open("A");
  const first = app.ask("first");
  await new Promise(r => setTimeout(r, 10));
  await assert.rejects(app.ask("second"), /already running/);
  server.finish("A", "turn");
  await first;
  assert.ok(view.log.some(e => e[0] === "appendTurn" && e[1] === "A"));
});
