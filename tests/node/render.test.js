// node --test tests/node/render.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const R = require(path.join(__dirname, "../../src/bench2agent/web/static/render.js"));

test("markdown links render as anchors with text and address escaped separately", () => {
  const html = R.toHtml("LIBERO — 80 papers. [Repository](https://github.com/Lifelong-Robot-Learning/LIBERO) · [Paper](https://arxiv.org/abs/2306.03310)");
  assert.equal(html, 'LIBERO — 80 papers. <a href="https://github.com/Lifelong-Robot-Learning/LIBERO" target="_blank" rel="noopener noreferrer">Repository</a> · '
    + '<a href="https://arxiv.org/abs/2306.03310" target="_blank" rel="noopener noreferrer">Paper</a>');
});

test("ampersands and query strings survive in the address, and are escaped in the attribute", () => {
  const html = R.toHtml("[HF](https://huggingface.co/datasets?search=gsm8k&sort=downloads) and https://example.org/a?b=1&c=2.");
  assert.match(html, /href="https:\/\/huggingface\.co\/datasets\?search=gsm8k&amp;sort=downloads"/);
  assert.match(html, /href="https:\/\/example\.org\/a\?b=1&amp;c=2" [^>]*>https:\/\/example\.org\/a\?b=1&amp;c=2<\/a>\.$/);
});

test("bare URLs drop trailing punctuation and sentence parentheses, keep wiki-style parentheses", () => {
  const tokens = R.tokenize("See https://arxiv.org/abs/2306.03310, then (https://github.com/x/y). Also https://en.wikipedia.org/wiki/COCO_(dataset) here.");
  const links = tokens.filter(t => t.t === "link").map(t => t.href);
  assert.deepEqual(links, ["https://arxiv.org/abs/2306.03310", "https://github.com/x/y", "https://en.wikipedia.org/wiki/COCO_(dataset)"]);
  assert.equal(R.toHtml("(https://github.com/x/y)."), '(<a href="https://github.com/x/y" target="_blank" rel="noopener noreferrer">https://github.com/x/y</a>).');
});

test("only http and https become links; markup and scripts stay text", () => {
  assert.equal(R.toHtml("[x](javascript:alert(1)) <b>bold</b> [y](ftp://host/f)"),
    "[x](javascript:alert(1)) &lt;b&gt;bold&lt;/b&gt; [y](ftp://host/f)");
  assert.equal(R.toHtml('[<img src=x onerror=alert(1)>](https://a.b/c)'),
    '<a href="https://a.b/c" target="_blank" rel="noopener noreferrer">&lt;img src=x onerror=alert(1)&gt;</a>');
  const broken = R.toHtml('[t](https://a.b/c" onclick="x)');   // a malformed link: no attribute escapes the anchor
  assert.doesNotMatch(broken, /<a [^>]*onclick/);
  assert.match(broken, /&quot; onclick=&quot;x\)/);
});
