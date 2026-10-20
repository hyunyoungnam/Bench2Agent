"use strict";
/* Local benchmark workspace. Everything shown in the details panel comes from
   the structured tool results recorded for that answer, never from the
   model's prose; numbers in the answer carry the same verification marks as
   in the terminal. No framework, no external requests. */

const LABELS = {
  benchmark_scope: "Checking data coverage", benchmark_usage: "Counting benchmark use",
  benchmark_trend: "Comparing editions", new_benchmarks: "Finding introductions",
  benchmark_adoption: "Counting adoption", benchmark_evidence: "Checking paper evidence",
};
const UNVERIFIED = new Set(["no", "na", "unconnected"]);
const $ = (id) => document.getElementById(id);
const S = { token: null, chats: [], chat: null, chatId: null, connectors: [], details: null, following: null };

/* ---------- token and API ---------- */
function initToken() {
  const url = new URL(location.href);
  const fromUrl = url.searchParams.get("token");
  if (fromUrl) {
    try { sessionStorage.setItem("ws-token", fromUrl); } catch (e) { /* storage may be off */ }
    url.searchParams.delete("token");
    history.replaceState(null, "", url.pathname + url.search + url.hash);
  }
  try { S.token = fromUrl || sessionStorage.getItem("ws-token"); } catch (e) { S.token = fromUrl; }
}
async function api(path, body) {
  const opts = { headers: { "X-Workspace-Token": S.token || "" } };
  if (body !== undefined) {
    opts.method = "POST";
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  let data = {};
  try { data = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok) { const err = new Error(data.error || `Request failed (${r.status})`); err.status = r.status; throw err; }
  return data;
}

/* ---------- small helpers ---------- */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function safeUrl(u) { return /^https?:\/\//i.test(String(u || "")) ? String(u) : null; }
function link(u, text) {
  const s = safeUrl(u);
  return s ? `<a href="${esc(s)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>` : esc(text);
}
function fmt(n) { return typeof n === "number" ? n.toLocaleString("en-US") : esc(n ?? "–"); }
function shortTool(name) { return String(name || "").split("__").pop(); }
function argText(args) {
  if (!args || typeof args !== "object") return "";
  return Object.entries(args).map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ");
}
function when(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) + ", " +
    d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}
function hashParams() { return new URLSearchParams(location.hash.slice(1)); }
function setHash(p) {
  const h = new URLSearchParams();
  for (const [k, v] of Object.entries(p)) if (v !== null && v !== undefined && v !== "") h.set(k, v);
  history.replaceState(null, "", location.pathname + location.search + (h.toString() ? "#" + h : ""));
}

/* ---------- answer rendering (safe markdown subset + verification marks) ---------- */
function inline(s) {
  s = s.replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`);
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, t, u) => `<a href="${u}" target="_blank" rel="noopener noreferrer">${t}</a>`);
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, (_, pre, u) => `${pre}<a href="${u}" target="_blank" rel="noopener noreferrer">${u}</a>`);
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  return s;
}
function markdown(src) {
  const lines = esc(src).split("\n");
  const out = [];
  let para = [], list = null;
  const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`); list = null; } };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const cells = (l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
    if (/^\s*\|/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      flushPara(); flushList();
      const head = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(cells(lines[i++]));
      i--;
      out.push(`<table><thead><tr>${head.map((h) => `<th>${inline(h)}</th>`).join("")}</tr></thead><tbody>` +
        rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("") + "</tbody></table>");
      continue;
    }
    let m;
    if ((m = line.match(/^\s*(#{1,6})\s+(.*)$/))) { flushPara(); flushList(); out.push(`<h3>${inline(m[2])}</h3>`); continue; }
    if ((m = line.match(/^\s*([-*]|\d+\.)\s+(.*)$/))) {
      flushPara();
      const tag = /\d/.test(m[1]) ? "ol" : "ul";
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      list.items.push(m[2]);
      continue;
    }
    if (!line.trim()) { flushPara(); flushList(); continue; }
    if (list && /^\s{2,}\S/.test(line)) { list.items[list.items.length - 1] += " " + line.trim(); continue; }
    flushList();
    para.push(line.trim());
  }
  flushPara(); flushList();
  return out.join("");
}
function renderAnswer(turn) {
  const segs = turn.segs || [];
  const tokens = [], cites = [];
  let text = "";
  for (const s of segs) {
    if (s.t === "p") text += String(s.s || "").replace(/⟦[^|⟧]*\|([^⟧]*)⟧/g, "$1");
    else if (s.t === "n" || s.t === "c") { text += `${tokens.length}`; tokens.push(s); }
  }
  let html = markdown(text);
  html = html.replace(/(\d+)/g, (_, k) => {
    const s = tokens[+k];
    if (s.t === "n") {
      const unv = UNVERIFIED.has(s.v);
      const title = unv ? "Not connected to a tool result of this answer" : "Connected to a tool result of this answer";
      return `<span class="fig${unv ? " unv" : ""}${unv && s.kind === "date" ? " date" : ""}" title="${title}">${esc(s.s)}</span>`;
    }
    cites.push(s);
    const n = cites.length;
    const url = safeUrl(s.url) || (s.paper_id ? `https://arxiv.org/abs/${encodeURIComponent(s.paper_id)}` : null);
    return `<span class="quote">${esc(s.q || "")}</span><a class="cite${s.v ? "" : " bad"}" href="${esc(url || "#")}" target="_blank" rel="noopener noreferrer" title="${s.v ? "Quote matches the paper's text" : "Quote not found verbatim"}">${n}</a>`;
  });
  let sources = "";
  if (cites.length) {
    sources = `<ol class="sources">` + cites.map((s, i) => {
      const url = safeUrl(s.url) || (s.paper_id ? `https://arxiv.org/abs/${encodeURIComponent(s.paper_id)}` : null);
      return `<li><span class="mark ${s.v ? "ok" : "bad"}">${s.v ? "✓" : "✗"}</span>[${i + 1}] ${link(url, s.title || s.paper_id || "source")}${s.paper_id ? ` <span class="small">arXiv ${esc(s.paper_id)}</span>` : ""}</li>`;
    }).join("") + `</ol>`;
  }
  return { html, sources };
}

/* ---------- conversation list and thread ---------- */
async function loadState() {
  const st = await api("/api/state");
  S.connectors = st.connectors || [];
  const sel = $("connector");
  sel.innerHTML = "";
  const usable = S.connectors.filter((c) => c.connected);
  for (const c of S.connectors) {
    const o = document.createElement("option");
    o.value = c.name;
    o.textContent = (c.name === "claude" ? "Claude Code" : c.name === "codex" ? "Codex" : c.name) + (c.connected ? "" : " (not signed in)");
    o.disabled = !c.connected;
    sel.appendChild(o);
  }
  if (usable.length) sel.value = usable[0].name;
  $("new-chat").disabled = !usable.length;
  if (!usable.length) $("composer-note").textContent = "Sign in to Claude Code (claude auth login) or Codex (codex login), then reload.";
  $("data-badge").innerHTML = st.available
    ? `Data revision <b>${esc(st.revision)}</b><br>${fmt(st.editions)} conference editions`
    : "No data revision is installed.";
}
async function loadChats() {
  const { chats } = await api("/api/chats");
  S.chats = chats;
  const nav = $("chat-list");
  nav.innerHTML = "";
  for (const c of chats) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chat-item" + (c.id === S.chatId ? " active" : "");
    const label = c.connector === "claude" ? "Claude Code" : c.connector === "codex" ? "Codex" : (c.connector || "API");
    b.innerHTML = `<span class="t"></span><span class="m"></span>`;
    b.querySelector(".t").textContent = c.title || "New conversation";
    b.querySelector(".m").textContent = `${label} · ${c.turns || 0} answer${c.turns === 1 ? "" : "s"} · ${when(c.ts)}`;
    b.addEventListener("click", () => openChat(c.id));
    nav.appendChild(b);
  }
}
async function openChat(id, opts = {}) {
  S.chatId = id;
  $("sidebar").classList.remove("open");
  S.chat = await api(`/api/chats/${id}`);
  renderThread();
  await loadChats();
  const keepDetails = opts.turn !== undefined ? opts : null;
  if (keepDetails) await openDetails(keepDetails.turn, keepDetails.tab || "benchmarks");
  else { closeDetails(); setHash({ chat: id }); }
  if (S.chat.active_job) follow(S.chat.active_job);
  else resumePending();
}
function renderThread() {
  const chat = S.chat;
  const thread = $("thread");
  thread.innerHTML = "";
  const empty = !chat || !chat.turns.length;
  $("empty").hidden = !empty || !!(chat && chat.active_job);
  $("chat-title").textContent = chat && chat.turns.length ? (chat.title || "Conversation") : "Ask about evaluation benchmarks";
  $("chat-meta").textContent = chat && chat.snapshot_id ? `revision ${chat.snapshot_id}` : "";
  if (!chat) return;
  chat.turns.forEach((t) => thread.appendChild(turnNode(t)));
  thread.scrollTop = thread.scrollHeight;
}
function turnNode(t) {
  const el = document.createElement("article");
  el.className = "turn";
  const q = document.createElement("div");
  q.className = "q";
  q.textContent = t.q || "";
  el.appendChild(q);
  const a = document.createElement("div");
  a.className = "answer";
  if (t.unverifiable) {
    a.innerHTML = `<div class="banner warn">This answer could not be verified (${esc(t.unverifiable)}). It is kept for reading and is not used as context for follow-up questions.</div>`;
  }
  const r = renderAnswer(t);
  a.insertAdjacentHTML("beforeend", r.html + r.sources);
  const v = t.verified || {};
  const foot = document.createElement("div");
  foot.className = "answer-foot";
  if (!t.unverifiable && (v.checked || v.fchecked)) {
    foot.insertAdjacentHTML("beforeend",
      `<span class="chip ${v.passed === v.checked ? "ok" : "warn"}" title="Quotes found verbatim in the paper's text">Quotes ${fmt(v.passed || 0)}/${fmt(v.checked || 0)}</span>` +
      `<span class="chip ${v.fpassed === v.fchecked ? "ok" : "warn"}" title="Figures connected to a tool result of the same benchmark, edition, role and metric">Figures ${fmt(v.fpassed || 0)}/${fmt(v.fchecked || 0)}</span>` +
      (v.fpassed !== v.fchecked ? `<span><span class="legend-unv">Dotted</span> figures could not be connected to a tool result of this answer; that does not mean they are wrong.</span>` : ""));
  }
  if (t.on && t.on.benchmarks && t.on.benchmarks.revision) foot.insertAdjacentHTML("beforeend", `<span>revision ${esc(t.on.benchmarks.revision)}</span>`);
  if (t.has_tools) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn btn-small";
    b.textContent = `Details · ${(t.trail || []).length} tool call${(t.trail || []).length === 1 ? "" : "s"}`;
    b.addEventListener("click", () => openDetails(t.index, "benchmarks"));
    foot.appendChild(b);
  }
  a.appendChild(foot);
  el.appendChild(a);
  return el;
}

/* ---------- asking, following, cancelling ---------- */
function newRequestId() {
  const a = new Uint8Array(12);
  crypto.getRandomValues(a);
  return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
}
function savePending(p) { try { p ? sessionStorage.setItem("ws-pending", JSON.stringify(p)) : sessionStorage.removeItem("ws-pending"); } catch (e) { /* ignore */ } }
function readPending() { try { return JSON.parse(sessionStorage.getItem("ws-pending") || "null"); } catch (e) { return null; } }

async function ask(question) {
  question = question.trim();
  if (!question) return;
  if (!S.chatId) {
    const connector = $("connector").value;
    const { id } = await api("/api/chats", { connector });
    S.chatId = id;
    S.chat = { id, turns: [], active_job: null };
    setHash({ chat: id });
  }
  const pending = { chat: S.chatId, request_id: newRequestId(), question };
  savePending(pending);
  await submit(pending);
}
async function submit(pending) {
  try {
    const job = await api(`/api/chats/${pending.chat}/ask`, { question: pending.question, request_id: pending.request_id });
    savePending({ ...pending, job: job.job });
    $("question").value = "";
    follow(job.job, pending.question);
  } catch (e) {
    if (e.status === 409 || e.status === 400) savePending(null);
    showError(e.message);
  }
}
function resumePending() {
  const p = readPending();
  if (p && p.chat === S.chatId && !S.following) submit(p);   // same request id: never runs twice
}
function progressNode(question) {
  const el = document.createElement("article");
  el.className = "turn";
  el.id = "live";
  el.innerHTML = `<div class="q"></div><div class="progress"><div class="progress-head"><span><span class="spinner"></span><span id="live-status">Starting…</span></span><button id="cancel" class="btn btn-small" type="button">Cancel</button></div><ul class="steps" id="steps"></ul></div>`;
  el.querySelector(".q").textContent = question || "";
  return el;
}
async function follow(jobId, question) {
  if (S.following === jobId) return;
  S.following = jobId;
  $("empty").hidden = true;
  $("send").disabled = true;
  let node = $("live");
  if (node) node.remove();
  node = progressNode(question);
  $("thread").appendChild(node);
  $("thread").scrollTop = $("thread").scrollHeight;
  $("cancel").addEventListener("click", () => cancel(jobId));
  let since = 0, view = null;
  const t0 = Date.now();
  while (S.following === jobId) {
    try {
      view = await api(`/api/jobs/${jobId}?since=${since}`);
    } catch (e) {
      if (e.status === 404) break;                    // the server restarted: nothing to follow
      $("live-status").textContent = "Reconnecting…";
      await new Promise((r) => setTimeout(r, 1500));
      continue;                                       // a dropped connection never cancels the question
    }
    if (question === undefined && view.question) node.querySelector(".q").textContent = view.question;
    for (const ev of view.events || []) {
      if (ev.t !== "tool") continue;
      const li = document.createElement("li");
      const name = shortTool(ev.name);
      li.innerHTML = `<span class="dot"></span><span><span>${esc(LABELS[name] || name)}</span><br><span class="args">${esc(name)}(${esc(argText(ev.arguments))})</span></span>`;
      $("steps").appendChild(li);
    }
    since = view.next;
    const secs = Math.round((Date.now() - t0) / 1000);
    if (view.state === "running") {
      $("live-status").textContent = (view.events || []).length || since ? `Working · ${view.elapsed}s` : `Waiting for the model · ${view.elapsed}s`;
      continue;
    }
    break;
  }
  S.following = null;
  $("send").disabled = false;
  savePending(null);
  const live = $("live");
  if (live) live.remove();
  if (S.chatId) {
    try {
      S.chat = await api(`/api/chats/${S.chatId}`);
      renderThread();
      await loadChats();
    } catch (e) { /* a new conversation that saved nothing has no file yet */ }
  }
  if (view && view.state === "cancelled") showNotice(`Cancelled: “${view.question}”. Nothing was saved; the next question sees only the saved answers.`);
  else if (view && view.state === "failed") showError(view.error || "The question failed.");
}
async function cancel(jobId) {
  const btn = $("cancel");
  if (btn) btn.disabled = true;
  try {
    const r = await api(`/api/jobs/${jobId}/cancel`, {});
    if (r.result === "too_late") $("live-status").textContent = "Too late to cancel: the answer is being saved.";
    else if (r.result === "cancelled") $("live-status").textContent = "Cancelling… nothing will be saved.";
  } catch (e) { showError(e.message); }
}
function showNotice(msg, kind = "warn") {
  const el = document.createElement("div");
  el.className = `banner ${kind}`;
  el.textContent = msg;
  const wrap = document.createElement("div");
  wrap.className = "turn";
  wrap.appendChild(el);
  $("thread").appendChild(wrap);
  $("thread").scrollTop = $("thread").scrollHeight;
}
function showError(msg) { showNotice(msg, "bad"); }

/* ---------- details panel: built from the answer's tool results ---------- */
function model(tools) {
  const m = { calls: [], cards: new Map(), usage: [], trends: [], adoption: [], evidence: new Map(), coverages: [], meta: {}, scope: null, notes: new Set() };
  const card = (id, name) => {
    if (!m.cards.has(id)) m.cards.set(id, { id, name, locations: [], introducing: undefined });
    const c = m.cards.get(id);
    if (name) c.name = name;
    return c;
  };
  const absorb = (c, r) => {
    if (r.kind) c.kind = r.kind;
    if (Array.isArray(r.locations) && r.locations.length) c.locations = r.locations;
    if (r.introducing_paper !== undefined) c.introducing = r.introducing_paper;
    if (r.first_claim) c.first_claim = r.first_claim;
  };
  const addEvidence = (id, papers) => {
    if (!Array.isArray(papers) || !papers.length) return;
    const seen = m.evidence.get(id) || [];
    for (const p of papers) if (!seen.some((x) => x.paper_id === p.paper_id)) seen.push(p);
    m.evidence.set(id, seen);
  };
  for (const row of tools) {
    const name = shortTool(row.name), r = row.result || {}, a = row.arguments || {};
    m.calls.push({ name, args: a });
    for (const k of ["revision", "generated_at", "source"]) if (r[k]) m.meta[k] = r[k];
    if (r.note) m.notes.add(r.note);
    if (r.coverage) { m.coverages.push({ ...r.coverage, role: r.role, args: a, tool: name, call: m.calls.length }); if (r.coverage.note) m.notes.add(r.coverage.note); }
    if (name === "benchmark_scope") m.scope = r;
    if (name === "benchmark_usage") {
      const denom = r.coverage && r.coverage.parsed_papers;
      m.usage.push({ args: a, role: r.role, denom, rows: r.results || [], total: r.total_benchmarks, truncated: r.truncated });
      for (const x of r.results || []) {
        const c = card(x.id, x.name);
        absorb(c, x);
        (c.usages = c.usages || []).push({ papers: x.papers, per_1000: x.per_1000, denom, role: r.role, scope: a });
        addEvidence(x.id, x.evidence);
      }
    }
    if (name === "benchmark_trend" && r.id) {
      const c = card(r.id, r.name);
      absorb(c, r);
      c.trend = { role: r.role, rows: r.results || [], comparisons: r.comparisons || [], rule: r.rule };
      m.trends.push({ id: r.id, name: r.name, args: a, ...c.trend });
    }
    if (name === "benchmark_adoption" && r.id) {
      const c = card(r.id, r.name);
      absorb(c, r);
      c.adoption = { evaluates_on: r.evaluates_on, trains_on: r.trains_on, unattributed: r.unattributed_using_papers, before_claim: r.before_claim };
      m.adoption.push({ id: r.id, name: r.name, ...c.adoption });
    }
    if (name === "benchmark_evidence" && r.id) {
      const c = card(r.id, r.name);
      absorb(c, r);
      c.evidenceTotal = r.total;
      addEvidence(r.id, r.results);
    }
    if (name === "new_benchmarks") {
      for (const x of r.results || []) {
        const c = card(x.id, x.name);
        absorb(c, x);
        c.isNew = true;
        if (x.adoption) c.adoption = { evaluates_on: x.adoption.evaluates_on, trains_on: x.adoption.trains_on };
      }
    }
  }
  return m;
}
function locationLabel(l) {
  if (l.artifact === "code_repository") return "Repository";
  if (l.artifact === "data_repository") return "Dataset";
  if (l.artifact === "project_page") return "Project page";
  const host = String(l.host || "");
  if (host === "github") return "Repository";
  if (host === "hf" || host.startsWith("hf") || /huggingface\.co\/datasets/.test(l.url || "")) return "Dataset";
  return "Homepage";
}
function locationsHtml(c) {
  if (!c.locations || !c.locations.length) return `<span class="small">No confirmed location</span>`;
  return c.locations.map((l) => {
    const how = l.check === "api" ? `API check${l.checked_at ? " " + esc(l.checked_at) : ""}`
      : l.check === "reviewed" ? `reviewed${l.checked_at ? " " + esc(l.checked_at) : ""}`
      : l.check === "none" ? "authored homepage" : esc(l.check || "");
    const status = l.data_status ? `<span class="status-note">Data: ${esc(l.data_status)}</span>` : "";
    return `<div>${link(l.url, locationLabel(l))} <span class="small">${how}</span>${status}</div>`;
  }).join("");
}
function introducingHtml(c) {
  const p = c.introducing;
  if (p === undefined) return `<span class="small">Not requested in this answer</span>`;
  if (!p) return `<span class="small">None reviewed in this corpus</span>`;
  return `${link(p.url, "arXiv " + p.paper_id)}${p.edition ? ` <span class="small">${esc(p.edition)}</span>` : ""}${p.title ? `<div class="small">${esc(p.title)}</div>` : ""}`;
}
function scopeText(a) {
  if (!a) return "";
  const bits = [];
  if (a.topic) bits.push(`field ${a.topic}`);
  if (a.venue) bits.push(`venue ${String(a.venue).toUpperCase()}`);
  if (a.edition) bits.push(a.edition);
  if (a.years) bits.push(`years ${[].concat(a.years).join(", ")}`);
  if (a.latest) bits.push("latest edition per venue");
  return bits.length ? bits.join(" · ") : "all analysed editions";
}
function roleTag(role) {
  if (role === "trains_on") return `<span class="tag train">training use</span>`;
  if (role === "evaluates_on") return `<span class="tag eval">evaluation use</span>`;
  return "";
}
function groupsHtml(g, label) {
  if (!g) return "";
  return `<div class="groups"><span class="small">${label}</span><span class="chip">other authors ${fmt(g.others)}</span><span class="chip">self ${fmt(g.self)}</span><span class="chip">undetermined ${fmt(g.undetermined)}</span></div>`;
}
function benchmarksTab(m) {
  if (!m.cards.size) return `<p class="note">This answer did not return benchmark rows${m.scope ? "; see Scope &amp; data for the coverage it checked" : ""}.</p>`;
  let html = "";
  for (const c of m.cards.values()) {
    const us = c.usages || [];
    const u = us[0];
    const metric = us.map((x) => `<div class="metric"><span class="big">${fmt(x.papers)}</span> <span class="of">of ${fmt(x.denom)} parsed papers</span><span class="rate">${x.per_1000 != null ? fmt(x.per_1000) + " per 1,000" : ""}</span>${us.length > 1 || Object.keys(x.scope || {}).length ? `<span class="scope">${esc(scopeText(x.scope))}</span>` : ""}</div>`).join("");
    const trend = c.trend && c.trend.rows.length ? `<div class="small">${c.trend.rows.length} editions in the trend · see Compare</div>` : "";
    const ev = m.evidence.get(c.id);
    html += `<div class="card"><div class="card-head"><span class="card-name">${esc(c.name || c.id)}</span>${c.kind ? `<span class="tag">${esc(c.kind)}</span>` : ""}${roleTag(u && u.role || (c.trend && c.trend.role))}${c.isNew ? `<span class="tag">introduced ${esc(c.first_claim || "")}</span>` : ""}</div>` +
      metric + trend +
      (c.adoption ? groupsHtml(c.adoption.evaluates_on, "Evaluation use, unique papers:") + (c.adoption.unattributed ? `<div class="small">${fmt(c.adoption.unattributed)} same-name uses not attributed to this benchmark</div>` : "") : "") +
      `<dl class="kv"><dt>Locations</dt><dd>${locationsHtml(c)}</dd><dt>Introducing paper</dt><dd>${introducingHtml(c)}</dd>` +
      (ev ? `<dt>Evidence</dt><dd><button class="btn btn-small" type="button" data-evidence="${esc(c.id)}">${ev.length} paper${ev.length === 1 ? "" : "s"} with sentences</button></dd>` : "") +
      `</dl></div>`;
  }
  return html + `<p class="note">Counts measure stated use, not benchmark quality. Locations are a mapping, not proof that data can be downloaded.</p>`;
}
function compareTab(m) {
  let html = "";
  for (const u of m.usage) {
    const max = Math.max(1, ...u.rows.map((r) => r.papers || 0));
    html += `<div class="section-title">Usage ranking · ${esc(scopeText(u.args))}</div><div class="small">${esc(u.role === "trains_on" ? "training use" : "evaluation use")} · denominator ${fmt(u.denom)} parsed papers</div>` +
      `<table class="data-table"><thead><tr><th>#</th><th>Benchmark</th><th class="n">Papers</th><th class="n">per 1,000</th><th></th></tr></thead><tbody>` +
      u.rows.map((r, i) => `<tr><td>${i + 1}</td><td>${esc(r.name)}</td><td class="n">${fmt(r.papers)}</td><td class="n">${fmt(r.per_1000)}</td><td><span class="bar" style="width:${Math.round(80 * (r.papers || 0) / max)}px"></span></td></tr>`).join("") +
      `</tbody></table>` + (u.truncated ? `<div class="small">Showing ${u.rows.length} of ${fmt(u.total)} benchmarks.</div>` : "");
  }
  for (const t of m.trends) {
    const cmp = new Map((t.comparisons || []).map((c) => [c.to, c]));
    html += `<div class="section-title">${esc(t.name)} across editions</div><div class="small">${esc(scopeText(t.args))} · ${esc(t.role === "trains_on" ? "training use" : "evaluation use")}</div>` +
      `<table class="data-table"><thead><tr><th>Edition</th><th class="n">Papers</th><th class="n">Parsed</th><th class="n">per 1,000</th><th>vs. previous</th></tr></thead><tbody>` +
      t.rows.map((r) => { const c = cmp.get(r.edition); return `<tr><td>${esc(r.edition)}</td><td class="n">${fmt(r.papers)}</td><td class="n">${fmt(r.parsed_papers)}</td><td class="n">${fmt(r.per_1000)}</td><td>${c ? `<span class="state-${esc(c.state)}">${esc(String(c.state).replace(/_/g, " "))}</span> <span class="small">z ${fmt(c.z)}</span>` : ""}</td></tr>`; }).join("") +
      `</tbody></table>` + (t.rule ? `<p class="note">${esc(t.rule)}</p>` : "");
  }
  for (const a of m.adoption) {
    html += `<div class="section-title">${esc(a.name)} adoption (unique papers, introducing papers excluded)</div>` +
      `<table class="data-table"><thead><tr><th>Role</th><th class="n">Other authors</th><th class="n">Self</th><th class="n">Undetermined</th></tr></thead><tbody>` +
      [["Evaluation", a.evaluates_on], ["Training", a.trains_on]].filter(([, g]) => g).map(([k, g]) => `<tr><td>${k}</td><td class="n">${fmt(g.others)}</td><td class="n">${fmt(g.self)}</td><td class="n">${fmt(g.undetermined)}</td></tr>`).join("") +
      `</tbody></table>` + `<div class="small">Unattributed same-name uses: ${fmt(a.unattributed)} · used before its first claim: ${fmt(a.before_claim)}</div>`;
  }
  return html || `<p class="note">This answer did not request figures that can be compared side by side.</p>`;
}
function evidenceTab(m, only) {
  const ids = only ? [only] : [...m.evidence.keys()];
  if (!ids.length) return `<p class="note">This answer did not return paper evidence. Ask for "the evidence behind" a result to see the sentences.</p>`;
  let html = only ? `<button class="btn btn-small" type="button" data-evidence="">Show all benchmarks</button>` : "";
  for (const id of ids) {
    const c = m.cards.get(id) || { name: id };
    const papers = m.evidence.get(id) || [];
    html += `<div class="section-title">${esc(c.name)} · ${papers.length} paper${papers.length === 1 ? "" : "s"}${c.evidenceTotal ? ` of ${fmt(c.evidenceTotal)}` : ""}</div>`;
    for (const p of papers) {
      html += `<div class="ev-paper"><div class="ev-title">${link(p.url || (p.paper_id ? "https://arxiv.org/abs/" + p.paper_id : null), p.title || p.paper_id)}</div>` +
        `<div class="small">arXiv ${esc(p.paper_id)} · ${esc(p.edition || "")}</div>` +
        (p.evidence || []).map((e) => `<div class="ev-sent"><span class="ev-where">${esc(e.where)}</span>${esc(e.text)}</div>`).join("") + `</div>`;
    }
  }
  return html + `<p class="note">Sentences are quoted from the parsed paper text; table records ending in :cell are assembled, not verbatim.</p>`;
}
function editionsTable(eds, withScope) {
  const rows = Object.entries(eds || {}).sort(([a], [b]) => a.localeCompare(b));
  const inScope = rows.filter(([, e]) => e.in_scope_parsed === undefined || e.in_scope_parsed > 0);
  if (!inScope.length) return "";
  return `<table class="data-table"><thead><tr><th>Edition</th><th class="n">Listed</th><th class="n">arXiv</th><th class="n">Parsed</th>${withScope ? `<th class="n">In scope</th>` : ""}<th class="n">Labelled</th></tr></thead><tbody>` +
    inScope.map(([k, e]) => `<tr><td>${esc(k)}</td><td class="n">${fmt(e.total_papers)}</td><td class="n">${fmt(e.arxiv_matched)}</td><td class="n">${fmt(e.parsed_papers)}</td>${withScope ? `<td class="n">${fmt(e.in_scope_parsed)}</td>` : ""}<td class="n">${fmt(e.field_labelled_papers)}</td></tr>`).join("") +
    `</tbody></table>` + (rows.length !== inScope.length ? `<p class="note">${rows.length - inScope.length} other editions have no papers in this scope; for a field filter that means no papers carry the label, not that none exist.</p>` : "");
}
function scopeTab(m) {
  let html = `<div class="section-title">Tool calls of this answer</div><div class="calls">` +
    m.calls.map((c, i) => `<div>${i + 1}. <b>${esc(c.name)}</b>(${esc(argText(c.args))})</div>`).join("") + `</div>`;
  html += `<dl class="kv"><dt>Data revision</dt><dd>${esc(m.meta.revision || "–")}</dd><dt>Generated</dt><dd>${esc(m.meta.generated_at || "–")}</dd><dt>Source</dt><dd>${esc(m.meta.source || "–")}</dd>` +
    (m.scope ? `<dt>Catalogue</dt><dd>${fmt(m.scope.catalogue_entries)} benchmark/dataset entries</dd>` : "") + `</dl>`;
  for (const cov of m.coverages) {
    const eds = Object.values(cov.editions || {}).filter((e) => e.in_scope_parsed === undefined || e.in_scope_parsed > 0).length;
    html += `<div class="section-title">Call ${cov.call} · ${esc(scopeText(cov.args))}</div>` +
      `<dl class="kv"><dt>Field filter</dt><dd>${esc(cov.topic || "none")}</dd><dt>Role</dt><dd>${esc(cov.role === "trains_on" ? "training use" : cov.role ? "evaluation use" : "–")}</dd>` +
      `<dt>Denominator</dt><dd>${fmt(cov.parsed_papers)} parsed papers in ${fmt(eds)} edition${eds === 1 ? "" : "s"}</dd>` +
      `<dt>No stated use</dt><dd>${fmt(cov.unattributed_papers)} papers (still in the denominator)</dd></dl>` + editionsTable(cov.editions, true);
  }
  if (!m.coverages.length && m.scope) html += `<div class="section-title">Installed editions</div>` + editionsTable(m.scope.editions, false);
  for (const n of m.notes) html += `<p class="note">${esc(n)}</p>`;
  return html;
}
async function openDetails(index, tab) {
  if (!S.chat) return;
  const turn = S.chat.turns[index];
  if (!turn) return;
  if (!S.details || S.details.index !== index || S.details.chat !== S.chat.id) {
    const { tools } = await api(`/api/chats/${S.chat.id}/turns/${index}/tools`);
    S.details = { chat: S.chat.id, index, model: model(tools), only: null };
  }
  S.details.tab = tab;
  $("details").hidden = false;
  $("app").classList.add("with-details");
  $("details-title").textContent = turn.q || "Details";
  renderDetails();
  setHash({ chat: S.chat.id, turn: index, tab });
}
function renderDetails() {
  const d = S.details;
  for (const b of document.querySelectorAll(".tab")) b.setAttribute("aria-selected", String(b.dataset.tab === d.tab));
  const body = $("details-body");
  body.innerHTML = d.tab === "compare" ? compareTab(d.model) : d.tab === "evidence" ? evidenceTab(d.model, d.only)
    : d.tab === "scope" ? scopeTab(d.model) : benchmarksTab(d.model);
  for (const b of body.querySelectorAll("[data-evidence]")) {
    b.addEventListener("click", () => { d.only = b.dataset.evidence || null; d.tab = "evidence"; renderDetails(); });
  }
}
function closeDetails() {
  $("details").hidden = true;
  $("app").classList.remove("with-details");
  S.details = null;
  if (S.chatId) setHash({ chat: S.chatId });
}

/* ---------- wiring ---------- */
function newChat() {
  S.chatId = null;
  S.chat = null;
  closeDetails();
  setHash({});
  renderThread();
  $("empty").hidden = false;
  loadChats();
  $("question").focus();
}
async function main() {
  initToken();
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); ask($("question").value).catch((err) => showError(err.message)); });
  $("question").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); }
  });
  for (const b of document.querySelectorAll(".example")) b.addEventListener("click", () => { $("question").value = b.textContent; $("question").focus(); });
  $("new-chat").addEventListener("click", newChat);
  $("close-details").addEventListener("click", closeDetails);
  for (const b of document.querySelectorAll(".tab")) b.addEventListener("click", () => { if (S.details) { S.details.tab = b.dataset.tab; S.details.only = null; renderDetails(); setHash({ chat: S.chat.id, turn: S.details.index, tab: b.dataset.tab }); } });
  $("toggle-sidebar").addEventListener("click", () => {
    const open = $("sidebar").classList.toggle("open");
    $("toggle-sidebar").setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("details").hidden) closeDetails(); });
  const state = loadState();                       // connector status may take a moment; the conversations do not wait
  try {
    await loadChats();
  } catch (e) {
    $("thread").innerHTML = "";
    showError(e.status === 403 ? "Open the workspace from the address its launcher printed (it carries this session's key)." : e.message);
    return;
  }
  state.catch((e) => showError(e.message));
  const h = hashParams();
  const chat = h.get("chat");
  if (chat && /^[0-9a-f]{12}$/.test(chat)) {
    try {
      await openChat(chat, h.has("turn") ? { turn: +h.get("turn"), tab: h.get("tab") || "benchmarks" } : {});
      return;
    } catch (e) { showError(e.message); }
  }
  renderThread();
  $("empty").hidden = false;
}
main();
