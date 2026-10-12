// The page's controller, kept apart from the DOM so its rules can be tested
// under node: a question belongs to the conversation it was asked in, its
// events reach the screen only while that conversation is open, cancel goes
// to that conversation, and a switch mid-answer loses nothing (the server
// saves the turn; reopening shows it).
//
// `env` supplies fetch, a view and storage; the page passes the real ones.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.B2AApp = factory();
})(typeof self !== "undefined" ? self : this, function () {
  function create(env) {
    const view = env.view;
    const state = {me: null, cid: null, conversations: [], jobs: {}};   // jobs: cid -> {controller, started}

    async function api(path, opts) {
      const headers = Object.assign({"Content-Type": "application/json", "X-Requested-With": "Bench2Agent"}, (opts && opts.headers) || {});
      const r = await env.fetch(path, Object.assign({}, opts || {}, {headers}));
      if (!r.ok) { let m = r.statusText || String(r.status); try { m = (await r.json()).detail || m; } catch (e) {} throw new Error(m); }
      return r.status === 204 ? null : r.json();
    }

    async function connect() {
      state.me = await api("/api/me");
      view.showUser(state.me);
      await refreshConnectors();
      await refreshList();
      return state.me;
    }

    // Open access: a first visit gets a session without any sign-up.
    async function start() {
      try { return await connect(); }
      catch (e) {
        try { await api("/api/session", {method: "POST"}); return await connect(); }
        catch (e2) { view.showSignedOut(e2.message); return null; }
      }
    }

    async function refreshConnectors() {
      const d = await api("/api/connectors");
      state.connectors = d.connectors;
      view.showConnectors(state.connectors);
      return state.connectors;
    }

    // Connecting the visitor's own Claude account: the official CLI's sign-in,
    // relayed. Output goes to this visitor's screen only; input goes back.
    let connectCursor = 0, connectTimer = null;
    async function connectStart() {
      connectCursor = 0;
      view.hideConnect();
      const r = await api("/api/connect/claude/start", {method: "POST"});
      view.showConnect("", r.state, false);
      poll();
      return r;
    }
    async function poll() {
      if (connectTimer) clearTimeout(connectTimer);
      try {
        const r = await api("/api/connect/claude/read?since=" + connectCursor);
        connectCursor = r.cursor;
        view.showConnect(r.output, r.state, r.finished);
        if (r.finished) { await refreshConnectors(); return; }
      } catch (e) { view.showConnect("", "error", true); return; }
      connectTimer = setTimeout(poll, 700);
    }
    async function connectInput(text) { return api("/api/connect/claude/input", {method: "POST", body: JSON.stringify({text})}); }
    async function connectCancel() { const r = await api("/api/connect/claude/cancel", {method: "POST"}); view.showConnect("", "cancelled", true); await refreshConnectors(); return r; }
    async function disconnect() { const r = await api("/api/connect/claude/disconnect", {method: "POST"}); view.hideConnect(); await refreshConnectors(); return r; }

    async function refreshList() {
      const d = await api("/api/conversations");
      state.conversations = d.conversations;
      view.showList(state.conversations, state.cid, Object.keys(state.jobs));
    }

    async function newConversation(provider, model, language, connectorName) {
      const c = await api("/api/conversations", {method: "POST", body: JSON.stringify({provider, model: model || null, language, connector: connectorName || "claude"})});
      await refreshList();
      await open(c.cid);
      return c;
    }

    async function open(cid) {
      const c = await api("/api/conversations/" + cid);
      state.cid = cid;                                  // the open conversation; events for others stay off screen
      view.showConversation(c, !!state.jobs[cid]);
      view.showList(state.conversations, state.cid, Object.keys(state.jobs));
      return c;
    }

    function isOpen(cid) { return state.cid === cid; }

    // One question, bound to the conversation it started in.
    async function ask(question, modelKey) {
      const cid = state.cid;
      if (!cid || !question) return null;
      if (state.jobs[cid]) throw new Error("A question is already running in this conversation.");
      const job = {cid, started: Date.now(), controller: env.abort ? env.abort() : null};
      state.jobs[cid] = job;
      view.setBusy(cid, true);
      const headers = {};
      const conv = state.conversations.find(c => c.cid === cid);
      const usesApi = !conv || !conv.connector || conv.connector === "api";
      if (usesApi && state.me && state.me.credentials === "user") headers["X-Model-Key"] = modelKey || "";
      let outcome = null;
      try {
        const r = await env.fetch("/api/conversations/" + cid + "/ask", {
          method: "POST", signal: job.controller ? job.controller.signal : undefined,
          headers: Object.assign({"Content-Type": "application/json", "X-Requested-With": "Bench2Agent"}, headers),
          body: JSON.stringify({question})});
        if (!r.ok) { let m = r.statusText || String(r.status); try { m = (await r.json()).detail || m; } catch (e) {} throw new Error(m); }
        for await (const ev of env.ndjson(r)) {
          if (ev.t === "tool") { if (isOpen(cid)) view.showEvent(cid, ev); }
          else if (ev.t === "turn") { outcome = ev; if (isOpen(cid)) view.appendTurn(cid, ev.turn); }
          else if (ev.t === "error") { outcome = ev; if (isOpen(cid)) view.showError(cid, ev.message, !!ev.cancelled); }
        }
      } catch (e) {
        outcome = {t: "error", message: e.message};
        if (isOpen(cid)) view.showError(cid, e.message, false);
      } finally {
        delete state.jobs[cid];
        view.setBusy(cid, isOpen(cid) ? false : null);
        try { await refreshList(); } catch (e) { /* list refresh is best effort */ }
        if (!isOpen(cid) && outcome && outcome.t === "turn") view.notifyFinished(cid);
      }
      return outcome;
    }

    // Cancel the question of the conversation it was asked in, whichever is open now.
    async function cancel(cid) {
      const target = cid || state.cid;
      if (!target || !state.jobs[target]) return {cancelled: false, message: "No question is running here."};
      const r = await api("/api/conversations/" + target + "/cancel", {method: "POST"});
      if (isOpen(target)) view.showStatus(target, r.message);
      return r;
    }

    return {state, connect, start, refreshList, refreshConnectors, newConversation, open, ask, cancel, isOpen,
            connectStart, connectInput, connectCancel, disconnect};
  }

  // NDJSON lines from a fetch Response, as parsed objects.
  async function* ndjson(response) {
    const reader = response.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i); buf = buf.slice(i + 1);
        if (line.trim()) yield JSON.parse(line);
      }
    }
    if (buf.trim()) yield JSON.parse(buf);
  }

  return {create, ndjson};
});
