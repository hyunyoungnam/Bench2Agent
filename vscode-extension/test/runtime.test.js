"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const { test } = require("node:test");
const {
  isReadyRoot, resolveRoot, resolvePython, startServer, stopServer
} = require("../runtime");

const root = path.resolve(__dirname, "../..");

function get(port, pathname) {
  return new Promise((resolve, reject) => {
    http.get({ hostname: "127.0.0.1", port, path: pathname }, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => { body += chunk; });
      response.on("end", () => resolve({ status: response.statusCode, body }));
    }).on("error", reject);
  });
}

test("selects a complete install and rejects a missing bundle", () => {
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), "bellwether-extension-"));
  try {
    for (const file of [
      "src/bellwether/cli.py", "reports/index.html", "reports/chat.html",
      "data/processed/union.json", "data/processed/card_terms.json",
      "data/processed/resources.json", "data/processed/papers.jsonl"
    ]) {
      const target = path.join(fixture, file);
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, "");
    }
    assert.equal(isReadyRoot(fixture), true);
    assert.equal(resolveRoot("", [fixture]), fixture);
    fs.unlinkSync(path.join(fixture, "data/processed/resources.json"));
    assert.equal(isReadyRoot(fixture), false);
    assert.throws(() => resolveRoot(fixture), /data or site is missing/);
  } finally {
    fs.rmSync(fixture, { recursive: true, force: true });
  }
});

test("starts the real server on a free port and stops it", { skip: !isReadyRoot(root) }, async () => {
  const active = await startServer({ root, python: resolvePython(root, ""), useMeilisearch: false });
  try {
    assert.ok(active.port > 0);
    const features = await get(active.port, "/features");
    assert.equal(features.status, 200);
    assert.equal(typeof JSON.parse(features.body).ko, "boolean");
    const browse = await get(active.port, "/browse");
    assert.equal(browse.status, 200);
    assert.match(browse.body, /<html/i);
  } finally {
    await stopServer(active.child);
  }
  assert.ok(active.child.exitCode !== null || active.child.signalCode !== null);
});
