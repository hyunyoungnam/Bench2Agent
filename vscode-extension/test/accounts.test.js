"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { EventEmitter } = require("node:events");
const { test } = require("node:test");

test("account commands launch only the official CLI; iframe bridge rejects other sources", async () => {
  const commands = new Map(), sent = [], subscriptions = [];
  let receive;
  const panel = {
    webview: { onDidReceiveMessage(fn) { receive = fn; } },
    onDidDispose() {}, reveal() {}, dispose() {}
  };
  const vscode = {
    workspace: { getConfiguration: () => ({ get: (_key, fallback) => fallback }), workspaceFolders: [] },
    Uri: { parse: value => ({ toString: () => value }) },
    env: { asExternalUri: async uri => uri }, ViewColumn: { One: 1 },
    commands: { registerCommand: (name, action) => { commands.set(name, action); return {}; } },
    window: {
      createOutputChannel: () => ({ appendLine() {}, append() {}, show() {} }),
      createWebviewPanel: () => panel,
      createTerminal: opts => ({ show() {}, sendText: value => sent.push({ opts, value }) }),
      showInformationMessage() {}, showErrorMessage: error => { throw new Error(error); }
    }
  };
  const child = new EventEmitter(); child.exitCode = null; child.signalCode = null;
  const runtime = { resolveRoot: () => "/install", resolvePython: () => "python3",
    startServer: async () => ({ child, root: "/install", url: "http://127.0.0.1:9876/" }) };
  const module = { exports: {} };
  const source = fs.readFileSync(path.join(__dirname, "../extension.js"), "utf8");
  vm.runInNewContext(source, { module, require: name => name === "vscode" ? vscode :
    name === "./runtime" ? runtime : require(name), process, URL });
  module.exports.activate({ subscriptions });
  await commands.get("bench2agent.connectOpenAI")();
  await commands.get("bench2agent.connectClaude")();
  assert.deepEqual(sent.map(x => x.value), ["codex login --device-auth", "claude auth login"]);
  assert.equal(sent[0].opts.cwd, "/install");
  await commands.get("bench2agent.openChat")();
  assert.match(panel.webview.html, /script-src 'nonce-/);
  const script = panel.webview.html.match(/<script nonce="[^"]+">([\s\S]*?)<\/script>/)[1];
  const frame = { contentWindow: {}, src: "http://127.0.0.1:9876/" }, messages = [];
  let listener;
  vm.runInNewContext(script, {
    acquireVsCodeApi: () => ({ postMessage: message => messages.push(message) }),
    document: { querySelector: () => frame },
    window: { addEventListener: (_name, fn) => { listener = fn; } }, URL
  });
  const event = { source: frame.contentWindow, origin: "http://127.0.0.1:9876",
    data: { type: "bench2agent.connect", provider: "codex" } };
  listener({ ...event, origin: "https://another.example" });
  listener({ ...event, source: {} });
  listener({ ...event, data: { ...event.data, provider: "arbitrary shell command" } });
  assert.equal(messages.length, 0);
  listener(event);
  assert.equal(messages[0].provider, "codex");
  receive({ type: "connect", provider: "arbitrary shell command" });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(sent.length, 2);
});
