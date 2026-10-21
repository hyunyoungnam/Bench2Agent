"use strict";
/* Bench2Agent local workspace. Everything in the evidence panel comes from the
   structured tool results recorded for that answer, never from the model's
   prose. Verbatim quotes are highlighted; figures the checker could not match
   to this answer's tool results carry a pencil mark, as in the terminal.
   No framework, no external requests. */

const LABELS = {
  benchmark_scope: "Checking data coverage", benchmark_usage: "Counting benchmark use",
  benchmark_trend: "Comparing editions", new_benchmarks: "Finding introductions",
  benchmark_adoption: "Counting adoption", benchmark_evidence: "Reading paper sentences",
};
const UNVERIFIED = new Set(["no", "na", "unconnected"]);
const CLIENTS = { claude: "Claude Code", codex: "Codex" };
const $ = (id) => document.getElementById(id);
const S = { token: null, chats: [], chat: null, chatId: null, connectors: [], details: null, following: null };

/* ---------- key and API ---------- */
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

/* ---------- helpers ---------- */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function safeUrl(u) { return /^https?:\/\//i.test(String(u || "")) ? String(u) : null; }
function link(u, text, cls = "") {
  const s = safeUrl(u);
  return s ? `<a${cls ? ` class="${cls}"` : ""} href="${esc(s)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>` : esc(text);
}
function fmt(n) { return typeof n === "number" ? n.toLocaleString("en-US") : esc(n ?? "–"); }
function plural(n, one, many) { return `${fmt(n)} ${n === 1 ? one : (many || one + "s")}`; }
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
function venueName(v) {
  const k = String(v || "").toLowerCase();
  return { neurips: "NeurIPS", corl: "CoRL" }[k] || k.toUpperCase();
}
function edition(e) {
  const m = String(e || "").match(/^([A-Za-z]+)-(\d{4})$/);
  return m ? `${venueName(m[1])} ${m[2]}` : esc(e);
}
function hashParams() { return new URLSearchParams(location.hash.slice(1)); }
function setHash(p) {
  const h = new URLSearchParams();
  for (const [k, v] of Object.entries(p)) if (v !== null && v !== undefined && v !== "") h.set(k, v);
  history.replaceState(null, "", location.pathname + location.search + (h.toString() ? "#" + h : ""));
}
function scopeText(a) {
  if (!a) return "";
  const bits = [];
  if (a.topic) bits.push(`field “${a.topic}”`);
  if (a.venue) bits.push(esc(venueName(a.venue)));
  if (a.edition) bits.push(edition(a.edition));
  if (a.years) bits.push([].concat(a.years).join(", "));
  if (a.latest) bits.push("latest edition per venue");
  return bits.length ? bits.join(", ") : "all analysed editions";
}

/* ---------- answer rendering (safe markdown subset) ---------- */
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
  const cells = (l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
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
function sourceUrl(s) { return safeUrl(s.url) || (s.paper_id ? `https://arxiv.org/abs/${encodeURIComponent(s.paper_id)}` : null); }
function renderAnswer(turn) {
  const tokens = [], cites = [];
  let text = "";
  for (const s of turn.segs || []) {
    if (s.t === "p") text += String(s.s || "").replace(/⟦[^|⟧]*\|([^⟧]*)⟧/g, "$1");
    else if (s.t === "n" || s.t === "c") { text += `${tokens.length}`; tokens.push(s); }
  }
  const html = markdown(text).replace(/(\d+)/g, (_, k) => {
    const s = tokens[+k];
    if (s.t === "n") {
      return UNVERIFIED.has(s.v)
        ? `<span class="fig unv" title="${s.kind === "date" ? "This date" : "This figure"} could not be matched to this answer's tool results">${esc(s.s)}</span>`
        : `<span class="fig">${esc(s.s)}</span>`;
    }
    cites.push(s);
    return `<span class="quote${s.v ? "" : " unfound"}" title="${s.v ? "Found word for word in the paper" : "Not found word for word in the paper"}">${esc(s.q || "")}</span>` +
      `<a class="cite" href="${esc(sourceUrl(s) || "#")}" target="_blank" rel="noopener noreferrer">${cites.length}</a>`;
  });
  const sources = cites.length ? `<ol class="sources">` + cites.map((s, i) =>
    `<li><span class="n">${i + 1}</span><span>${link(sourceUrl(s), s.title || s.paper_id || "Source")}` +
    `${s.paper_id ? ` <span class="meta">arXiv ${esc(s.paper_id)}</span>` : ""}${s.v ? "" : ` <span class="unfound">not found word for word</span>`}</span></li>`).join("") + `</ol>` : "";
  return { html, sources };
}

/* ---------- library and thread ---------- */
async function loadState() {
  const st = await api("/api/state");
  S.connectors = st.connectors || [];
  const sel = $("connector");
  sel.innerHTML = "";
  for (const c of S.connectors) {
    const o = document.createElement("option");
    o.value = c.name;
    o.textContent = (CLIENTS[c.name] || c.name) + (c.connected ? "" : " (signed out)");
    o.disabled = !c.connected;
    sel.appendChild(o);
  }
  const usable = S.connectors.filter((c) => c.connected);
  if (usable.length) sel.value = usable[0].name;
  $("new-chat").disabled = !usable.length;
  if (!usable.length) $("composer-note").textContent = "No signed-in AI CLI found. Run claude auth login or codex login, then reload this page.";
  $("data-badge").innerHTML = st.available
    ? `Data revision <strong>${esc(st.revision)}</strong><br>${plural(st.editions, "conference edition")}`
    : "No data revision is installed. Run bench2agent data install.";
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
    if (c.id === S.chatId) b.setAttribute("aria-current", "true");
    b.innerHTML = `<span class="t"></span><span class="m"></span>`;
    b.querySelector(".t").textContent = c.title || "New conversation";
    b.querySelector(".m").textContent = `${CLIENTS[c.connector] || "API key"}, ${c.turns === 1 ? "1 answer" : `${c.turns || 0} answers`}, ${when(c.ts)}`;
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
  if (opts.turn !== undefined) await openDetails(opts.turn, opts.tab || "benchmarks");
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
  $("chat-title").textContent = chat && chat.turns.length ? (chat.title || "Conversation") : "";
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
    a.innerHTML = `<div class="banner warn">This answer could not be checked (${esc(t.unverifiable)}). It stays here for reading and is not used as context for later questions.</div>`;
  }
  const r = renderAnswer(t);
  a.insertAdjacentHTML("beforeend", r.html + r.sources);
  el.appendChild(a);
  const v = t.verified || {};
  const check = document.createElement("div");
  check.className = "check";
  let html = "";
  if (!t.unverifiable && v.checked) {
    html += `<span class="item${v.passed === v.checked ? " all" : ""}"><b>${fmt(v.passed)} of ${fmt(v.checked)}</b> quotes found in the papers</span>`;
  }
  if (!t.unverifiable && v.fchecked) {
    html += `<span class="item${v.fpassed === v.fchecked ? " all" : ""}"><b>${fmt(v.fpassed)} of ${fmt(v.fchecked)}</b> figures matched to tool results</span>`;
  }
  check.innerHTML = html;
  if (t.has_tools) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn btn-small open";
    b.textContent = "Open evidence";
    b.title = `${plural((t.trail || []).length, "tool call")} behind this answer`;
    b.addEventListener("click", () => openDetails(t.index, "benchmarks"));
    check.appendChild(b);
  }
  if (!t.unverifiable && v.fpassed !== v.fchecked) {
    check.insertAdjacentHTML("beforeend", `<span class="legend"><span class="pencil-sample">Pencilled</span> figures could not be matched to this answer's tool results. That does not make them wrong; check them in the evidence.</span>`);
  }
  if (check.childNodes.length) a.appendChild(check);
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
    const { id } = await api("/api/chats", { connector: $("connector").value });
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
    showNotice(e.message, "bad");
  }
}
function resumePending() {
  const p = readPending();
  if (!p || p.chat !== S.chatId || S.following) return;
  if (p.job) follow(p.job, p.question);     // a known job: reattach, never resend
  else submit(p);                           // the request may not have arrived: same request id, runs once
}
function progressNode(question) {
  const el = document.createElement("article");
  el.className = "turn";
  el.id = "live";
  el.innerHTML = `<div class="q"></div><div class="progress"><div class="progress-head"><span><span class="pulse" aria-hidden="true"></span><span id="live-status">Sending the question</span></span><button id="cancel" class="btn btn-small" type="button">Cancel question</button></div><ul class="steps" id="steps"></ul></div>`;
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
  let since = 0, view = null, missing = false;
  while (S.following === jobId) {
    try {
      view = await api(`/api/jobs/${jobId}?since=${since}`);
    } catch (e) {
      if (e.status === 404) { missing = true; break; }     // the workspace was restarted: nothing to follow
      $("live-status").textContent = "Connection lost, retrying";
      await new Promise((r) => setTimeout(r, 1500));
      continue;                                             // a dropped connection never cancels the question
    }
    if (!question && view.question) node.querySelector(".q").textContent = view.question;
    for (const ev of view.events || []) {
      if (ev.t !== "tool") continue;
      const li = document.createElement("li");
      const name = shortTool(ev.name);
      li.innerHTML = `<span>${esc(LABELS[name] || name)}</span><span class="args">${esc(argText(ev.arguments)) || "no arguments"}</span>`;
      $("steps").appendChild(li);
    }
    since = view.next;
    if (view.state !== "running") break;
    $("live-status").textContent = since ? `Working, ${Math.round(view.elapsed)} s` : `Waiting for the model, ${Math.round(view.elapsed)} s`;
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
  if (missing) showNotice("The workspace was restarted while this question ran. Its answer was not saved; ask again.", "warn");
  else if (view && view.state === "cancelled") showNotice(`Cancelled, nothing saved: “${String(view.question || "").replace(/[.?!]+$/, "")}”. The next question sees only the saved answers.`, "warn");
  else if (view && view.state === "failed") showNotice(view.error || "The question failed.", "bad");
}
async function cancel(jobId) {
  const btn = $("cancel");
  if (btn) btn.disabled = true;
  try {
    const r = await api(`/api/jobs/${jobId}/cancel`, {});
    if (r.result === "too_late") $("live-status").textContent = "Too late to cancel: the answer is being saved";
    else if (r.result === "cancelled") $("live-status").textContent = "Cancelling; nothing will be saved";
  } catch (e) { showNotice(e.message, "bad"); }
}
function showNotice(msg, kind) {
  const wrap = document.createElement("div");
  wrap.className = "notice";
  const el = document.createElement("div");
  el.className = `banner ${kind}`;
  el.textContent = msg;
  wrap.appendChild(el);
  $("thread").appendChild(wrap);
  $("thread").scrollTop = $("thread").scrollHeight;
}

/* ---------- evidence panel: built from the answer's tool results ---------- */
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
    if (Array.isArray(r.other_paper_links) && r.other_paper_links.length) c.otherLinks = r.other_paper_links;
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
  if (host.startsWith("hf") || /huggingface\.co\/datasets/.test(l.url || "")) return "Dataset";
  return "Homepage";
}
function locationsHtml(c) {
  if (!c.locations || !c.locations.length) return `<span class="muted">No confirmed location</span>`;
  return c.locations.map((l) => {
    const how = l.check === "api" ? `API check ${esc(l.checked_at || "")}`.trim()
      : l.check === "reviewed" ? `reviewed ${esc(l.checked_at || "")}`.trim()
      : l.check === "none" ? "listed by its authors" : esc(l.check || "");
    const status = l.data_status ? `<span class="status-note">Data: ${esc(l.data_status)}</span>` : "";
    return `<div>${link(l.url, locationLabel(l))} <span class="muted">${how}</span>${status}</div>`;
  }).join("");
}
function introducingHtml(c) {
  const p = c.introducing;
  if (p === undefined) return `<span class="muted">Not looked up in this answer</span>`;
  if (!p) return `<span class="muted">None reviewed in this corpus</span>`;
  return `${link(p.url, "arXiv " + p.paper_id)}${p.edition ? ` <span class="muted">${edition(p.edition)}</span>` : ""}${p.title ? `<div class="paper-title">${esc(p.title)}</div>` : ""}` +
    paperLinksHtml(p.links, "Links this paper prints") + paperLinksHtml(c.otherLinks, "Links another introducing paper prints");
}
/* the paper's own links that were not confirmed as this benchmark's location: what was seen there, dated */
function paperLinksHtml(links, title) {
  if (!Array.isArray(links) || !links.length) return "";
  return `<div class="paper-links"><div class="paper-links-title">${esc(title)}, not a confirmed location</div>` + links.map((l) => {
    const fixed = l.corrected ? `<span class="status-note">Printed address could not be opened; ${link(l.corrected.url, "a corrected address")} exists: ${esc(l.corrected.observed || "")}. ${esc(l.corrected.note || "")}</span>` : "";
    const paper = l.paper_id && title.startsWith("Links another") ? ` <span class="muted">arXiv ${esc(l.paper_id)}</span>` : "";
    return `<div class="paper-link"><span class="muted">${esc(l.label || "Link")}</span> ${link(l.url, l.url.replace(/^https?:\/\//, ""))}${paper}` +
      `<span class="status-note">Seen: ${esc(l.observed || "not checked")}</span>` +
      (l.not_confirmed_because ? `<span class="status-note">Not a location: ${esc(l.not_confirmed_because)}</span>` : "") + fixed + `</div>`;
  }).join("") + `</div>`;
}
function roleLabel(role) {
  if (role === "trains_on") return `<span class="role train">training use</span>`;
  if (role === "evaluates_on") return `<span class="role eval">evaluation use</span>`;
  return "";
}
function measureHtml(x, showScope) {
  const share = x.denom ? Math.max(0, Math.min(100, (x.papers / x.denom) * 100)) : 0;
  return `<div class="measure"><div class="measure-line"><span class="measure-num"><b>${fmt(x.papers)}</b> of ${fmt(x.denom)} parsed papers</span>` +
    `<span class="share" title="${x.per_1000 != null ? fmt(x.per_1000) + " per 1,000 papers" : ""}"><span data-w="${share.toFixed(2)}"></span></span></div>` +
    (showScope ? `<div class="measure-scope">${esc(scopeText(x.scope))}</div>` : "") + `</div>`;
}
function groupsHtml(g) {
  if (!g) return "";
  return `<div class="groups"><span>Other authors <b>${fmt(g.others)}</b></span><span>Self <b>${fmt(g.self)}</b></span><span>Undetermined <b>${fmt(g.undetermined)}</b></span></div>`;
}
function benchmarksTab(m) {
  if (!m.cards.size) return `<p class="note">This answer returned no benchmark rows${m.scope ? ". The Scope tab shows the coverage it checked" : ""}.</p>`;
  let html = "";
  for (const c of m.cards.values()) {
    const us = c.usages || [];
    const role = (us[0] && us[0].role) || (c.trend && c.trend.role) || (c.adoption ? "evaluates_on" : null);
    const ev = m.evidence.get(c.id);
    const kind = [c.kind, c.isNew && c.first_claim ? `first claimed at ${edition(c.first_claim)}` : null].filter(Boolean).join(", ");
    html += `<section class="entry"><div class="entry-head"><span class="entry-name">${esc(c.name || c.id)}</span>${roleLabel(role)}</div>` +
      (kind ? `<div class="entry-kind">${kind}</div>` : "") +
      us.map((x) => measureHtml(x, true)).join("") +
      (c.trend && c.trend.rows.length ? `<div class="measure-scope">Trend over ${plural(c.trend.rows.length, "edition")} in Compare</div>` : "") +
      (c.adoption ? `<div class="measure-scope gap">Unique papers using it for evaluation</div>${groupsHtml(c.adoption.evaluates_on)}` +
        (c.adoption.unattributed ? `<div class="measure-scope">${plural(c.adoption.unattributed, "use")} of the same name not attributed to it</div>` : "") : "") +
      `<dl class="facts"><dt>Location</dt><dd>${locationsHtml(c)}</dd><dt>Introduced in</dt><dd>${introducingHtml(c)}</dd>` +
      (ev ? `<dt>Sentences</dt><dd><button class="link-btn" type="button" data-evidence="${esc(c.id)}">${plural(ev.length, "paper")} with quoted sentences</button></dd>` : "") +
      `</dl></section>`;
  }
  return html + `<p class="note">Counts measure stated use, not benchmark quality. A location is our mapping and not proof that the data can be downloaded.</p>`;
}
function compareTab(m) {
  let html = "";
  for (const u of m.usage) {
    const max = Math.max(1, ...u.rows.map((r) => r.papers || 0));
    html += `<h3 class="section">Usage, ${esc(scopeText(u.args))}</h3><div class="section-sub">${u.role === "trains_on" ? "Training use" : "Evaluation use"} out of ${fmt(u.denom)} parsed papers</div>` +
      `<table class="data-table"><thead><tr><th>Benchmark</th><th class="n">Papers</th><th class="n">Per 1,000</th><th class="barcell"></th></tr></thead><tbody>` +
      u.rows.map((r) => `<tr><td>${esc(r.name)}</td><td class="n">${fmt(r.papers)}</td><td class="n">${fmt(r.per_1000)}</td><td class="barcell"><span class="share"><span data-w="${(100 * (r.papers || 0) / max).toFixed(1)}"></span></span></td></tr>`).join("") +
      `</tbody></table>` + (u.truncated ? `<p class="note">Showing ${fmt(u.rows.length)} of ${fmt(u.total)} benchmarks.</p>` : "");
  }
  for (const t of m.trends) {
    const cmp = new Map((t.comparisons || []).map((c) => [c.to, c]));
    html += `<h3 class="section">${esc(t.name)} across editions</h3><div class="section-sub">${t.role === "trains_on" ? "Training use" : "Evaluation use"}, ${esc(scopeText(t.args))}</div>` +
      `<table class="data-table"><thead><tr><th>Edition</th><th class="n">Papers</th><th class="n">Parsed</th><th class="n">Per 1,000</th><th>Change</th></tr></thead><tbody>` +
      t.rows.map((r) => { const c = cmp.get(r.edition); return `<tr><td>${edition(r.edition)}</td><td class="n">${fmt(r.papers)}</td><td class="n">${fmt(r.parsed_papers)}</td><td class="n">${fmt(r.per_1000)}</td><td>${c ? `<span class="state-${esc(c.state)}">${esc(String(c.state).replace(/_/g, " "))}</span> <span class="muted">z ${fmt(c.z)}</span>` : ""}</td></tr>`; }).join("") +
      `</tbody></table>` + (t.rule ? `<p class="note">${esc(t.rule)}</p>` : "");
  }
  for (const a of m.adoption) {
    html += `<h3 class="section">Who uses ${esc(a.name)}</h3><div class="section-sub">Unique papers; the introducing papers are excluded</div>` +
      `<table class="data-table"><thead><tr><th>Use</th><th class="n">Other authors</th><th class="n">Self</th><th class="n">Undetermined</th></tr></thead><tbody>` +
      [["Evaluation", a.evaluates_on], ["Training", a.trains_on]].filter(([, g]) => g).map(([k, g]) => `<tr><td>${k}</td><td class="n">${fmt(g.others)}</td><td class="n">${fmt(g.self)}</td><td class="n">${fmt(g.undetermined)}</td></tr>`).join("") +
      `</tbody></table><p class="note">${plural(a.unattributed || 0, "same-name use")} not attributed; ${plural(a.before_claim || 0, "use")} before its first claim.</p>`;
  }
  return html || `<p class="note">This answer did not ask for figures that compare side by side.</p>`;
}
function highlightSurfaces(text, surfaces) {
  let html = esc(text);
  const names = Object.keys(surfaces || {}).filter((s) => s.length > 1).sort((a, b) => b.length - a.length);
  for (const n of names) html = html.split(esc(n)).join(`${esc(n)}`);
  return html.replace(//g, "<mark>").replace(//g, "</mark>");
}
function evidenceTab(m, only) {
  const ids = only ? [only] : [...m.evidence.keys()];
  if (!ids.length) return `<p class="note">This answer did not return paper sentences. Ask for the evidence behind a count to see them here.</p>`;
  let html = only ? `<p><button class="link-btn" type="button" data-evidence="">Show every benchmark</button></p>` : "";
  for (const id of ids) {
    const c = m.cards.get(id) || { name: id };
    const papers = m.evidence.get(id) || [];
    html += `<h3 class="section">${esc(c.name)}</h3><div class="section-sub">${plural(papers.length, "paper")}${c.evidenceTotal ? ` of ${fmt(c.evidenceTotal)}` : ""} with sentences that state the use</div>`;
    for (const p of papers) {
      html += `<article class="ev-paper"><div class="ev-title">${link(p.url || (p.paper_id ? "https://arxiv.org/abs/" + p.paper_id : null), p.title || p.paper_id)}</div>` +
        `<div class="ev-meta">arXiv ${esc(p.paper_id)}, ${edition(p.edition)}</div>` +
        (p.evidence || []).filter((e, i, all) => all.findIndex((x) => x.text === e.text) === i).map((e) => `<p class="ev-sent"><span class="ev-where">${esc(String(e.where || "").replace(/[:@]/g, " "))}</span>${highlightSurfaces(e.text, p.surfaces)}</p>`).join("") + `</article>`;
    }
  }
  return html + `<p class="note">Sentences are quoted from the parsed paper text. Records ending in “cell” are assembled from tables, not quoted.</p>`;
}
function editionsTable(eds, withScope) {
  const rows = Object.entries(eds || {}).sort(([a], [b]) => a.localeCompare(b));
  const inScope = rows.filter(([, e]) => e.in_scope_parsed === undefined || e.in_scope_parsed > 0);
  if (!inScope.length) return "";
  return `<table class="data-table"><thead><tr><th>Edition</th><th class="n">Listed</th><th class="n">On arXiv</th><th class="n">Parsed</th>${withScope ? `<th class="n">In scope</th>` : ""}<th class="n">Labelled</th></tr></thead><tbody>` +
    inScope.map(([k, e]) => `<tr><td>${edition(k)}</td><td class="n">${fmt(e.total_papers)}</td><td class="n">${fmt(e.arxiv_matched)}</td><td class="n">${fmt(e.parsed_papers)}</td>${withScope ? `<td class="n">${fmt(e.in_scope_parsed)}</td>` : ""}<td class="n">${fmt(e.field_labelled_papers)}</td></tr>`).join("") +
    `</tbody></table>` + (rows.length !== inScope.length ? `<p class="note">${plural(rows.length - inScope.length, "other edition")} have no papers in this scope. For a field filter that means no paper carries the label, not that none exist.</p>` : "");
}
function scopeTab(m) {
  let html = `<h3 class="section">What this answer looked up</h3><ol class="calls">` +
    m.calls.map((c) => `<li><b>${esc(LABELS[c.name] || c.name)}</b><br>${esc(c.name)}(${esc(argText(c.args))})</li>`).join("") + `</ol>`;
  html += `<dl class="facts"><dt>Data revision</dt><dd>${esc(m.meta.revision || "–")}</dd><dt>Built</dt><dd>${esc(String(m.meta.generated_at || "–").slice(0, 10))}</dd><dt>Source</dt><dd>${m.meta.source === "arxiv_html_stated_roles" ? "Roles stated in the papers' arXiv full text" : esc(m.meta.source || "–")}</dd>` +
    (m.scope ? `<dt>Catalogue</dt><dd>${plural(m.scope.catalogue_entries, "benchmark and dataset entry", "benchmark and dataset entries")}</dd>` : "") + `</dl>`;
  for (const cov of m.coverages) {
    const eds = Object.values(cov.editions || {}).filter((e) => e.in_scope_parsed === undefined || e.in_scope_parsed > 0).length;
    html += `<h3 class="section">Lookup ${cov.call}: ${esc(scopeText(cov.args))}</h3>` +
      `<dl class="facts"><dt>Field filter</dt><dd>${esc(cov.topic || "none")}</dd><dt>Use</dt><dd>${cov.role === "trains_on" ? "training" : cov.role ? "evaluation" : "–"}</dd>` +
      `<dt>Denominator</dt><dd>${fmt(cov.parsed_papers)} parsed papers in ${plural(eds, "edition")}</dd>` +
      `<dt>Unresolved names</dt><dd>${plural(cov.papers_with_unattributed_benchmark_mentions ?? cov.unattributed_papers ?? 0, "paper")} with at least one benchmark name not attributed to a benchmark; not a count of papers that use none</dd></dl>` + editionsTable(cov.editions, true);
  }
  if (!m.coverages.length && m.scope) html += `<h3 class="section">Installed editions</h3>` + editionsTable(m.scope.editions, false);
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
  $("details-title").textContent = turn.q || "Evidence";
  const rev = turn.on && turn.on.benchmarks && turn.on.benchmarks.revision;
  $("details-sub").textContent = `${plural(S.details.model.calls.length, "lookup")} on data revision ${rev || "unknown"}`;
  renderDetails();
  setHash({ chat: S.chat.id, turn: index, tab });
}
function renderDetails() {
  const d = S.details;
  for (const b of document.querySelectorAll(".tab")) b.setAttribute("aria-selected", String(b.dataset.tab === d.tab));
  const body = $("details-body");
  body.innerHTML = d.tab === "compare" ? compareTab(d.model) : d.tab === "evidence" ? evidenceTab(d.model, d.only)
    : d.tab === "scope" ? scopeTab(d.model) : benchmarksTab(d.model);
  body.scrollTop = 0;
  // widths are set through the CSSOM: the page's policy refuses inline style attributes
  for (const el of body.querySelectorAll("[data-w]")) el.style.width = `${Math.max(0, Math.min(100, +el.dataset.w))}%`;
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
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); ask($("question").value).catch((err) => showNotice(err.message, "bad")); });
  $("question").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); }
  });
  for (const b of document.querySelectorAll(".example")) b.addEventListener("click", () => { $("question").value = b.textContent; $("question").focus(); });
  $("new-chat").addEventListener("click", newChat);
  $("close-details").addEventListener("click", closeDetails);
  for (const b of document.querySelectorAll(".tab")) b.addEventListener("click", () => {
    if (!S.details) return;
    S.details.tab = b.dataset.tab; S.details.only = null; renderDetails();
    setHash({ chat: S.chat.id, turn: S.details.index, tab: b.dataset.tab });
  });
  $("toggle-sidebar").addEventListener("click", () => {
    const open = $("sidebar").classList.toggle("open");
    $("toggle-sidebar").setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("details").hidden) closeDetails(); });
  const state = loadState();                       // connector status may take a moment; conversations do not wait
  try {
    await loadChats();
  } catch (e) {
    $("thread").innerHTML = "";
    showNotice(e.status === 403 ? "Open the workspace from the address its launcher printed; it carries this session's key." : e.message, "bad");
    return;
  }
  state.catch((e) => showNotice(e.message, "bad"));
  const h = hashParams();
  const chat = h.get("chat");
  if (chat && /^[0-9a-f]{12}$/.test(chat)) {
    try {
      await openChat(chat, h.has("turn") ? { turn: +h.get("turn"), tab: h.get("tab") || "benchmarks" } : {});
      return;
    } catch (e) { showNotice(e.message, "bad"); }
  }
  renderThread();
  $("empty").hidden = false;
}
main();
