(() => {
  // ns-hugo-imp:/Users/dymo/Develop/learn/hugo_blog/assets/ts/sitesearch/engine.ts
  var cache = null;
  function load(url) {
    if (!cache) {
      cache = fetch(url, { credentials: "same-origin" }).then((r) => {
        if (!r.ok) throw new Error(`\u641C\u7D22\u7D22\u5F15\u52A0\u8F7D\u5931\u8D25\uFF1A${r.status}`);
        return r.json();
      }).then((docs) => docs.map(prepare)).catch((err) => {
        cache = null;
        throw err;
      });
    }
    return cache;
  }
  function prepare(doc) {
    return {
      ...doc,
      _t: low(doc.t),
      _x: low(doc.x),
      _ds: low(doc.ds),
      _meta: low([...doc.c || [], ...doc.g || [], doc.sc || ""].join(" ")),
      _s: (doc.s || []).map((s) => ({ t: low(s.t), x: low(s.x) }))
    };
  }
  var low = (s) => (s || "").toLowerCase();
  function tokenize(query) {
    const seen = /* @__PURE__ */ new Set();
    return query.toLowerCase().split(/\s+/).filter((t) => t && !seen.has(t) && seen.add(t));
  }
  function search(docs, query, opts = {}) {
    const start = performance.now();
    const tokens = tokenize(query);
    if (!tokens.length) return { hits: [], tokens, partial: false, ms: 0 };
    let hits = run(docs, tokens, true, opts.sectionLimit ?? 3);
    let partial = false;
    if (!hits.length && tokens.length > 1) {
      hits = run(docs, tokens, false, opts.sectionLimit ?? 3);
      partial = hits.length > 0;
    }
    return { hits, tokens, partial, ms: performance.now() - start };
  }
  function run(docs, tokens, requireAll, sectionLimit) {
    const hits = [];
    for (const doc of docs) {
      const haystack = [doc._t, doc._ds, doc._meta, doc._x, ...doc._s.map((s) => s.t + " " + s.x)].join("\n");
      const present = tokens.filter((t) => haystack.includes(t));
      if (requireAll ? present.length < tokens.length : present.length === 0) continue;
      let score = 0;
      const q = tokens.join(" ");
      for (const t of present) {
        if (doc._t.includes(t)) score += 30;
        if (doc._meta.includes(t)) score += 12;
        if (doc._ds.includes(t)) score += 6;
        score += Math.min(count(haystack, t), 20);
      }
      if (doc._t === q) score += 40;
      else if (doc._t.startsWith(q)) score += 20;
      const sections = [];
      let parent3 = "";
      doc.s.forEach((sec, i) => {
        if (sec.l <= 3) parent3 = sec.t;
        const p = doc._s[i];
        let sScore = 0;
        for (const t of present) {
          if (p.t.includes(t)) sScore += 10;
          const c = count(p.x, t);
          if (c) sScore += 2 + Math.min(c, 8);
        }
        if (!sScore) return;
        sections.push({
          section: sec,
          index: i,
          url: `${doc.u}#${encodeURIComponent(sec.id)}`,
          parent: sec.l === 4 && parent3 ? parent3 : void 0,
          titleHTML: highlight(sec.t, present),
          snippetHTML: p.x ? snippet(sec.x, present, 72) : "",
          score: sScore
        });
      });
      sections.sort((a, b) => b.score - a.score);
      if (sections.length) score += Math.min(sections[0].score, 20);
      hits.push({
        doc,
        score,
        titleHTML: highlight(doc.t, present),
        snippetHTML: snippet(pickSnippetSource(doc, present), present, 96),
        sections: sections.slice(0, sectionLimit),
        sectionTotal: sections.length
      });
    }
    return hits.sort((a, b) => b.score - a.score || (b.doc.d || "").localeCompare(a.doc.d || ""));
  }
  function pickSnippetSource(doc, tokens) {
    const has = (s) => tokens.some((t) => s.includes(t));
    if (doc.ds && has(doc._ds)) return doc.ds;
    if (doc.x && has(doc._x)) return doc.x;
    return doc.ds || doc.x || doc.s.find((s) => s.x)?.x || "";
  }
  function count(s, t) {
    let n = 0, i = s.indexOf(t);
    while (i !== -1) {
      n++;
      i = s.indexOf(t, i + t.length);
    }
    return n;
  }
  var ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  var escapeHTML = (s) => s.replace(/[&<>"']/g, (c) => ESC[c]);
  function ranges(text, tokens) {
    const lower = text.toLowerCase();
    const out = [];
    for (const t of tokens) {
      let i = lower.indexOf(t);
      while (i !== -1) {
        out.push([i, i + t.length]);
        i = lower.indexOf(t, i + t.length);
      }
    }
    out.sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const r of out) {
      const last = merged[merged.length - 1];
      if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
      else merged.push([r[0], r[1]]);
    }
    return merged;
  }
  function highlight(text, tokens) {
    const rs = ranges(text, tokens);
    let html = "", last = 0;
    for (const [a, b] of rs) {
      html += escapeHTML(text.slice(last, a)) + "<mark>" + escapeHTML(text.slice(a, b)) + "</mark>";
      last = b;
    }
    return html + escapeHTML(text.slice(last));
  }
  function snippet(text, tokens, length) {
    if (!text) return "";
    const rs = ranges(text, tokens);
    if (!rs.length) return escapeHTML(text.length > length ? text.slice(0, length) + "\u2026" : text);
    let best = rs[0][0], bestScore = -1;
    for (const [a] of rs) {
      const win = text.slice(a, a + length).toLowerCase();
      const sc = tokens.filter((t) => win.includes(t)).length;
      if (sc > bestScore) {
        bestScore = sc;
        best = a;
      }
    }
    let start = Math.max(0, best - Math.round(length * 0.3));
    let end = Math.min(text.length, start + length);
    start = Math.max(0, Math.min(start, end - length));
    const piece = text.slice(start, end);
    return (start > 0 ? "\u2026" : "") + highlight(piece, tokens) + (end < text.length ? "\u2026" : "");
  }
  var RECENT_KEY = "site-search:recent";
  function recent() {
    try {
      return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]").slice(0, 6);
    } catch {
      return [];
    }
  }
  function remember(q) {
    q = q.trim();
    if (!q) return;
    try {
      const list = [q, ...recent().filter((x) => x !== q)].slice(0, 6);
      localStorage.setItem(RECENT_KEY, JSON.stringify(list));
    } catch {
    }
  }

  // ns-hugo-imp:/Users/dymo/Develop/learn/hugo_blog/assets/ts/sitesearch/render.ts
  var iconCache = null;
  function icon(name) {
    if (!iconCache) {
      iconCache = {};
      const tpl = document.getElementById("ss-icons");
      tpl?.content.querySelectorAll("[data-icon]").forEach((el) => {
        iconCache[el.dataset.icon] = el.innerHTML;
      });
    }
    return iconCache[name] || "";
  }
  function metaHTML(doc) {
    const parts = [];
    if (doc.k === "project") parts.push("\u9879\u76EE");
    if (doc.sc) parts.push(escapeHTML(doc.sc));
    if (doc.d) parts.push(`<time datetime="${doc.d}">${doc.d}</time>`);
    return parts.join('<span class="ss-dot" aria-hidden="true">\xB7</span>');
  }
  var PAGE_SECTIONS_VISIBLE = 4;
  function pageCard(hit) {
    const { doc } = hit;
    const tags = (doc.g || []).slice(0, 6).map((t) => `<span class="ssp-tag">${escapeHTML(t)}</span>`).join("");
    const secs = hit.sections.map((s, i) => `
        <li${i >= PAGE_SECTIONS_VISIBLE ? ' class="ssp-sec--extra"' : ""}>
            <a class="ssp-sec" href="${escapeHTML(s.url)}">
                <span class="ssp-sec-icon" aria-hidden="true">${icon("hash")}</span>
                <span class="ssp-sec-main">
                    <span class="ssp-sec-title">${s.parent ? `<span class="ss-parent">${escapeHTML(s.parent)}</span><span class="ss-sep" aria-hidden="true">\u203A</span>` : ""}${s.titleHTML}</span>
                    ${s.snippetHTML ? `<span class="ssp-sec-snip">${s.snippetHTML}</span>` : ""}
                </span>
            </a>
        </li>`).join("");
    const extra = hit.sections.length - PAGE_SECTIONS_VISIBLE;
    return `
<article class="ssp-card">
    <a class="ssp-head" href="${escapeHTML(doc.u)}">
        <div class="ssp-text">
            <div class="ssp-meta">${metaHTML(doc)}</div>
            <h2 class="ssp-title">${hit.titleHTML}</h2>
            ${hit.snippetHTML ? `<p class="ssp-snip">${hit.snippetHTML}</p>` : ""}
            ${tags ? `<div class="ssp-tags">${tags}</div>` : ""}
        </div>
        ${doc.i ? `<div class="ssp-img"><img src="${escapeHTML(doc.i)}" alt="" loading="lazy" decoding="async"></div>` : ""}
    </a>
    ${secs ? `
    <div class="ssp-secs-wrap">
        <div class="ssp-secs-label">${hit.sectionTotal} \u4E2A\u5C0F\u8282\u5339\u914D</div>
        <ol class="ssp-secs">${secs}</ol>
        ${extra > 0 ? `<button type="button" class="ssp-more" data-more>\u5C55\u5F00\u5176\u4F59 ${extra} \u4E2A\u5C0F\u8282</button>` : ""}
    </div>` : ""}
</article>`;
  }

  // <stdin>
  function init() {
    const form = document.querySelector(".ssp-form");
    const input = form?.querySelector("input");
    const status = document.querySelector(".ssp-status");
    const list = document.querySelector(".ssp-list");
    if (!form || !input || !status || !list) return;
    let docs = null;
    let composing = false;
    let timer = 0;
    const render = () => {
      const q = input.value.trim();
      syncURL(q);
      if (!q) {
        status.textContent = docs ? `\u5171 ${docs.length} \u7BC7\u6587\u7AE0\u548C\u9879\u76EE\u53EF\u641C\u7D22` : "";
        list.innerHTML = "";
        return;
      }
      if (!docs) {
        status.textContent = "\u6B63\u5728\u52A0\u8F7D\u7D22\u5F15\u2026";
        return;
      }
      const r = search(docs, q, { sectionLimit: Infinity });
      if (!r.hits.length) {
        status.innerHTML = `\u6CA1\u6709\u627E\u5230\u201C<strong>${escapeHTML(q)}</strong>\u201D\u76F8\u5173\u7684\u5185\u5BB9`;
        list.innerHTML = "";
        return;
      }
      const secCount = r.hits.reduce((n, h) => n + h.sectionTotal, 0);
      status.innerHTML = `${r.partial ? "\u6CA1\u6709\u540C\u65F6\u5305\u542B\u6240\u6709\u5173\u952E\u8BCD\u7684\u7ED3\u679C\uFF0C\u4EE5\u4E0B\u662F\u90E8\u5206\u5339\u914D \xB7 " : ""}\u627E\u5230 <strong>${r.hits.length}</strong> \u4E2A\u7ED3\u679C` + (secCount ? `\uFF0C<strong>${secCount}</strong> \u4E2A\u5C0F\u8282` : "") + `<span class="ssp-time">${r.ms < 1 ? "<1" : Math.round(r.ms)} ms</span>`;
      for (const h of r.hits) h.sections.sort((a, b) => a.index - b.index);
      list.innerHTML = r.hits.map(pageCard).join("");
      clearTimeout(timer);
      timer = window.setTimeout(() => remember(q), 1200);
    };
    input.addEventListener("compositionstart", () => {
      composing = true;
    });
    input.addEventListener("compositionend", () => {
      composing = false;
      render();
    });
    input.addEventListener("input", () => {
      if (!composing) render();
    });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      render();
      remember(input.value);
    });
    list.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-more]");
      if (!btn) return;
      btn.closest(".ssp-secs-wrap")?.classList.add("is-expanded");
      btn.remove();
    });
    window.addEventListener("popstate", () => {
      input.value = new URL(location.href).searchParams.get("keyword") || "";
      render();
    });
    input.value = new URL(location.href).searchParams.get("keyword") || input.value;
    render();
    if (!input.value) input.focus();
    load(form.dataset.index).then((d) => {
      docs = d;
      render();
    }).catch(() => {
      status.textContent = "\u7D22\u5F15\u52A0\u8F7D\u5931\u8D25\uFF0C\u5237\u65B0\u9875\u9762\u518D\u8BD5\u4E00\u6B21";
    });
  }
  function syncURL(q) {
    const url = new URL(location.href);
    if (q) url.searchParams.set("keyword", q);
    else url.searchParams.delete("keyword");
    if (url.href !== location.href) history.replaceState(null, "", url);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
