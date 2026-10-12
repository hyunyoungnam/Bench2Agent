// Safe rendering of the model's prose: plain text with links.
//
// The text is split into tokens first (markdown links `[text](url)` and bare
// URLs), then each token's text and address are escaped separately, so a
// query string, an ampersand or a trailing parenthesis in the address is
// never run through the text escaper and back. Only http and https addresses
// become links; anything else stays visible text. No other markup is
// interpreted. Works in the page and under node (module.exports).
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.B2ARender = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const esc = s => String(s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  // an address runs to whitespace or a closing bracket; one level of balanced
  // parentheses inside it is allowed (wiki-style "COCO_(dataset)")
  const MD_LINK = /\[([^\]\n]{1,300})\]\((https?:\/\/[^\s()]+(?:\([^\s()]*\)[^\s()]*)*)\)/g;
  const BARE = /https?:\/\/[^\s<>"'()\]]+(?:\([^\s()]*\)[^\s<>"'()\]]*)*/g;
  const TRAILING = /[.,;:!?]+$/;

  function safeUrl(url) {
    try {
      const u = new URL(url);
      return (u.protocol === "http:" || u.protocol === "https:") ? u.href : null;
    } catch (e) { return null; }
  }

  // tokens: {t: "text", s} | {t: "link", text, href}
  function tokenize(text) {
    const out = [];
    let pos = 0;
    const push = (s) => { if (s) out.push({t: "text", s}); };
    for (const m of String(text).matchAll(MD_LINK)) {
      push(text.slice(pos, m.index));
      const href = safeUrl(m[2]);
      if (href) out.push({t: "link", text: m[1], href});
      else push(m[0]);
      pos = m.index + m[0].length;
    }
    push(text.slice(pos));
    // bare URLs inside the remaining text runs
    const final = [];
    for (const tok of out) {
      if (tok.t !== "text") { final.push(tok); continue; }
      let p = 0;
      for (const m of tok.s.matchAll(BARE)) {
        let raw = m[0];
        const trail = raw.match(TRAILING);
        if (trail) raw = raw.slice(0, -trail[0].length);
        // a closing parenthesis belongs to the sentence unless the URL opened one
        if (raw.endsWith(")") && (raw.split("(").length - 1) < (raw.split(")").length - 1)) raw = raw.slice(0, -1);
        if (tok.s.slice(p, m.index)) final.push({t: "text", s: tok.s.slice(p, m.index)});
        const href = safeUrl(raw);
        if (href) final.push({t: "link", text: raw, href});
        else final.push({t: "text", s: raw});
        p = m.index + raw.length;
      }
      if (tok.s.slice(p)) final.push({t: "text", s: tok.s.slice(p)});
    }
    return final;
  }

  function toHtml(text) {
    return tokenize(text).map(tok => tok.t === "link"
      ? `<a href="${esc(tok.href)}" target="_blank" rel="noopener noreferrer">${esc(tok.text)}</a>`
      : esc(tok.s)).join("");
  }

  return {esc, tokenize, toHtml, safeUrl};
});
