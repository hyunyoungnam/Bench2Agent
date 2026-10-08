"use strict";

const vscode = require("vscode");
const crypto = require("node:crypto");
const path = require("node:path");
const { resolveRoot, resolvePython, startServer, stopServer } = require("./runtime");

let session;
let starting;
let output;
const panels = new Map();

async function ensureServer() {
  if (session && session.child.exitCode === null && session.child.signalCode === null) return session;
  if (starting) return starting;
  const config = vscode.workspace.getConfiguration("bench2agent");
  const folders = (vscode.workspace.workspaceFolders || []).map((folder) => folder.uri.fsPath);
  starting = (async () => {
    const root = resolveRoot(config.get("installPath", ""), folders);
    const python = resolvePython(root, config.get("pythonPath", ""));
    output.appendLine(`Starting ${root} with ${python}`);
    const launched = await startServer({
      root, python, extraPath: config.get("extraPath", ""),
      useMeilisearch: config.get("useMeilisearch", true),
      onOutput: (line) => output.append(line)
    });
    session = launched;
    launched.child.once("exit", () => {
      if (session === launched) {
        session = undefined;
        for (const panel of panels.values()) panel.dispose();
        panels.clear();
      }
    });
    return launched;
  })();
  try {
    return await starting;
  } finally {
    starting = undefined;
  }
}

function escaped(value) {
  return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;")
    .replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

async function forwardedUrl(pathname) {
  const active = await ensureServer();
  return vscode.env.asExternalUri(vscode.Uri.parse(active.url + pathname));
}

async function openPanel(kind) {
  const existing = panels.get(kind);
  if (existing) {
    existing.reveal();
    return;
  }
  const pathname = kind === "browse" ? "browse" : "";
  const target = await forwardedUrl(pathname);
  const panel = vscode.window.createWebviewPanel(
    `bench2agent.${kind}`,
    kind === "browse" ? "Bench2Agent · Papers" : "Bench2Agent · Chat",
    vscode.ViewColumn.One,
    { enableScripts: true, retainContextWhenHidden: true }
  );
  const src = escaped(target.toString());
  const origin = escaped(new URL(target.toString()).origin);
  const nonce = crypto.randomBytes(16).toString("hex");
  panel.webview.html = `<!doctype html><html><head>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; frame-src ${origin}; style-src 'unsafe-inline'; script-src 'nonce-${nonce}'">
<style>html,body,iframe{width:100%;height:100%;margin:0;border:0;background:#10141b}</style>
</head><body><iframe title="Bench2Agent ${kind}" src="${src}" allow="clipboard-read; clipboard-write"></iframe>
<script nonce="${nonce}">
const api=acquireVsCodeApi(), frame=document.querySelector('iframe');
window.addEventListener('message', e=>{
  if(e.source!==frame.contentWindow || e.origin!==new URL(frame.src).origin)return;
  if(e.data?.type==='bench2agent.connect' && ['codex','claude'].includes(e.data.provider))
    api.postMessage({type:'connect',provider:e.data.provider});
});
</script></body></html>`;
  panel.webview.onDidReceiveMessage(message => {
    if (message?.type === "connect" && ["codex", "claude"].includes(message.provider)) {
      command(() => connectAccount(message.provider));
    }
  });
  panels.set(kind, panel);
  panel.onDidDispose(() => { if (panels.get(kind) === panel) panels.delete(kind); });
}

async function command(action) {
  try {
    await action();
  } catch (error) {
    output.appendLine(String(error.stack || error));
    output.show(true);
    vscode.window.showErrorMessage(String(error.message || error));
  }
}

async function connectAccount(provider) {
  const active = await ensureServer();
  const extraPath = vscode.workspace.getConfiguration("bench2agent").get("extraPath", "");
  const env = extraPath.trim() ? {
    PATH: [path.resolve(extraPath.trim().replace(/^~(?=[/\\])/, require("node:os").homedir())), process.env.PATH]
      .filter(Boolean).join(path.delimiter)
  } : undefined;
  const terminal = vscode.window.createTerminal({name: `Bench2Agent · ${provider} sign in`, cwd: active.root, env});
  terminal.show();
  terminal.sendText(provider === "codex" ? "codex login --device-auth" : "claude auth login", true);
  vscode.window.showInformationMessage("Finish the CLI sign-in, then refresh agent status in Bench2Agent settings.");
}

function activate(context) {
  output = vscode.window.createOutputChannel("Bench2Agent");
  context.subscriptions.push(output);
  context.subscriptions.push(
    vscode.commands.registerCommand("bench2agent.openChat", () => command(() => openPanel("chat"))),
    vscode.commands.registerCommand("bench2agent.openBrowse", () => command(() => openPanel("browse"))),
    vscode.commands.registerCommand("bench2agent.connectOpenAI", () => command(() => connectAccount("codex"))),
    vscode.commands.registerCommand("bench2agent.connectClaude", () => command(() => connectAccount("claude"))),
    vscode.commands.registerCommand("bench2agent.openBrowser", () => command(async () => {
      const target = await forwardedUrl("");
      await vscode.env.openExternal(target);
    })),
    vscode.commands.registerCommand("bench2agent.stop", () => command(async () => {
      if (starting) await starting;
      if (session) await stopServer(session.child);
      for (const panel of panels.values()) panel.dispose();
      panels.clear();
      session = undefined;
    }))
  );
}

function deactivate() {
  if (session) return stopServer(session.child);
}

module.exports = { activate, deactivate };
