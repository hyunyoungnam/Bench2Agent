"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const http = require("node:http");
const { spawn } = require("node:child_process");

function expandHome(value) {
  return value === "~" ? os.homedir() :
    value.startsWith(`~${path.sep}`) ? path.join(os.homedir(), value.slice(2)) : value;
}

function isReadyRoot(root) {
  const required = [
    "src/bench2agent/core/cli.py",
    "reports/index.html",
    "reports/chat.html",
    "data/processed/union.json",
    "data/processed/card_terms.json",
    "data/processed/resources.json"
  ];
  if (!required.every((name) => fs.existsSync(path.join(root, name)))) return false;
  try {
    return fs.readdirSync(path.join(root, "data/processed"))
      .some((name) => /^papers.*\.jsonl$/.test(name));
  } catch {
    return false;
  }
}

function resolveRoot(configured, workspacePaths = []) {
  if (configured && configured.trim()) {
    const root = path.resolve(expandHome(configured.trim()));
    if (!isReadyRoot(root)) {
      throw new Error(`Bench2Agent data or site is missing in ${root}. Check installPath and the processed data bundle.`);
    }
    return root;
  }
  const candidates = [...workspacePaths, path.join(os.homedir(), ".bench2agent")];
  const root = candidates.find(isReadyRoot);
  if (!root) {
    throw new Error("No complete Bench2Agent install found. Install the data bundle or set bench2agent.installPath.");
  }
  return root;
}

function resolvePython(root, configured) {
  if (configured && configured.trim()) {
    const value = expandHome(configured.trim());
    return value.includes(path.sep) ? path.resolve(value) : value;
  }
  const binary = process.platform === "win32" ? "python.exe" : "python";
  const candidates = [
    path.join(root, ".venv-serve", process.platform === "win32" ? "Scripts" : "bin", binary),
    path.join(root, ".venv", process.platform === "win32" ? "Scripts" : "bin", binary)
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) ||
    (process.platform === "win32" ? "python" : "python3");
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      server.close((error) => error ? reject(error) : resolve(port));
    });
  });
}

function probe(port) {
  return new Promise((resolve) => {
    const req = http.get({ hostname: "127.0.0.1", port, path: "/features", timeout: 1000 }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { body += chunk; });
      res.on("end", () => {
        try {
          const features = JSON.parse(body);
          resolve(res.statusCode === 200 && typeof features.ko === "boolean");
        } catch {
          resolve(false);
        }
      });
    });
    req.on("timeout", () => req.destroy());
    req.on("error", () => resolve(false));
  });
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function stopServer(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  if (!child.pid) return;
  let timer;
  const exited = new Promise((resolve) => child.once("exit", resolve));
  child.kill("SIGTERM");
  await Promise.race([exited, new Promise((resolve) => { timer = setTimeout(resolve, 3000); })]);
  clearTimeout(timer);
  if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
}

async function startServer({ root, python, extraPath = "", useMeilisearch = true, onOutput = () => {} }) {
  const port = await freePort();
  const args = ["-m", "bench2agent", "serve", "--port", String(port)];
  if (!useMeilisearch) args.push("--no-meili");
  const env = {
    ...process.env,
    PYTHONPATH: [path.join(root, "src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
    PYTHONUNBUFFERED: "1"
  };
  if (extraPath.trim()) env.PATH = [path.resolve(expandHome(extraPath.trim())), env.PATH].filter(Boolean).join(path.delimiter);
  const child = spawn(python, args, { cwd: root, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  let failure = "";
  let spawnError = null;
  child.on("error", (error) => { spawnError = error; });
  for (const stream of [child.stdout, child.stderr]) {
    stream.on("data", (chunk) => {
      const line = chunk.toString();
      failure = (failure + line).slice(-3000);
      onOutput(line);
    });
  }
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    if (spawnError || child.exitCode !== null || child.signalCode !== null) break;
    if (await probe(port)) {
      return { child, port, root, url: `http://127.0.0.1:${port}/` };
    }
    await delay(150);
  }
  await stopServer(child);
  const reason = spawnError?.message || failure.trim() || "The server did not become ready within 30 seconds.";
  throw new Error(`Could not start Bench2Agent: ${reason}`);
}

module.exports = { isReadyRoot, resolveRoot, resolvePython, startServer, stopServer };
