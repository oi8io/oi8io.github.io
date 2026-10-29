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
  function forget(q) {
    try {
      localStorage.setItem(RECENT_KEY, JSON.stringify(recent().filter((x) => x !== q)));
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
  function thumbHTML(doc, cls) {
    if (doc.i) return `<span class="${cls}"><img src="${escapeHTML(doc.i)}" alt="" loading="lazy" decoding="async"></span>`;
    return `<span class="${cls} ${cls}--icon">${icon(doc.k === "project" ? "box" : "file-text")}</span>`;
  }
  function paletteDoc(hit, id) {
    const { doc } = hit;
    return `
<a class="ss-opt ss-opt--doc" href="${escapeHTML(doc.u)}" id="${id}" role="option" aria-selected="false" tabindex="-1">
    ${thumbHTML(doc, "ss-thumb")}
    <span class="ss-main">
        <span class="ss-title">${hit.titleHTML}</span>
        <span class="ss-meta">${metaHTML(doc)}</span>
        ${hit.snippetHTML ? `<span class="ss-snip">${hit.snippetHTML}</span>` : ""}
    </span>
    <span class="ss-go" aria-hidden="true">${icon("corner-down-left")}</span>
</a>`;
  }
  function paletteSection(s, id) {
    return `
<a class="ss-opt ss-opt--sec" href="${escapeHTML(s.url)}" id="${id}" role="option" aria-selected="false" tabindex="-1">
    <span class="ss-hash" aria-hidden="true">${icon("hash")}</span>
    <span class="ss-main">
        <span class="ss-title">${s.parent ? `<span class="ss-parent">${escapeHTML(s.parent)}</span><span class="ss-sep" aria-hidden="true">\u203A</span>` : ""}${s.titleHTML}</span>
        ${s.snippetHTML ? `<span class="ss-snip">${s.snippetHTML}</span>` : ""}
    </span>
    <span class="ss-go" aria-hidden="true">${icon("corner-down-left")}</span>
</a>`;
  }

  // <stdin>
  var DOC_LIMIT = 8;
  var SECTION_LIMIT = 3;
  var isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  var Palette = class {
    root;
    panel;
    input;
    list;
    clearBtn;
    allLink;
    indexURL;
    pageURL;
    docs = null;
    active = -1;
    opener = null;
    composing = false;
    lastQuery = "";
    constructor(root) {
      this.root = root;
      this.panel = root.querySelector(".ss-panel");
      this.input = root.querySelector(".ss-input");
      this.list = root.querySelector(".ss-list");
      this.clearBtn = root.querySelector(".ss-clear");
      this.allLink = root.querySelector(".ss-all");
      this.indexURL = root.dataset.index;
      this.pageURL = root.dataset.page;
      this.bindTriggers();
      this.bindInput();
      this.bindList();
    }
    /* ---------- 打开 / 关闭 ---------- */
    get isOpen() {
      return !this.root.hidden;
    }
    open(query) {
      if (this.isOpen) {
        this.input.select();
        return;
      }
      this.opener = document.activeElement;
      this.root.hidden = false;
      requestAnimationFrame(() => this.root.classList.add("is-open"));
      if (query !== void 0) this.input.value = query;
      this.input.focus();
      this.input.select();
      this.update();
      this.ensureData();
    }
    close() {
      if (!this.isOpen) return;
      this.root.classList.remove("is-open");
      const done = () => {
        this.root.hidden = true;
      };
      if (matchMedia("(prefers-reduced-motion: reduce)").matches) done();
      else setTimeout(done, 160);
      if (this.opener instanceof HTMLElement) this.opener.focus({ preventScroll: true });
    }
    async ensureData() {
      if (this.docs) return;
      try {
        this.docs = await load(this.indexURL);
        if (this.isOpen) this.update(true);
      } catch (e) {
        this.list.innerHTML = this.message("\u7D22\u5F15\u52A0\u8F7D\u5931\u8D25", "\u68C0\u67E5\u7F51\u7EDC\u540E\u518D\u8BD5\u4E00\u6B21");
      }
    }
    /* ---------- 触发方式 ---------- */
    bindTriggers() {
      document.querySelectorAll("#main-menu a[href]").forEach((a) => {
        if (new URL(a.href, location.href).pathname !== this.pageURL) return;
        a.addEventListener("click", (e) => {
          if (e.metaKey || e.ctrlKey || e.shiftKey) return;
          e.preventDefault();
          const pageInput = document.querySelector(".ssp-form input");
          if (pageInput) {
            pageInput.focus();
            pageInput.select();
            return;
          }
          this.open();
        });
        a.addEventListener("pointerenter", () => load(this.indexURL).catch(() => {
        }), { once: true });
        if (matchMedia("(hover: hover) and (pointer: fine)").matches && !a.querySelector(".ss-menu-kbd")) {
          a.insertAdjacentHTML(
            "beforeend",
            `<kbd class="ss-menu-kbd" aria-hidden="true">${isMac ? "\u2318" : "Ctrl "}K</kbd>`
          );
        }
      });
      document.addEventListener("keydown", (e) => {
        const k = e.key.toLowerCase();
        const hotkey = (e.metaKey || e.ctrlKey) && k === "k";
        if (!hotkey && !(k === "/" && !this.isOpen && !isEditable(e.target))) return;
        e.preventDefault();
        if (this.isOpen) return this.close();
        const pageInput = document.querySelector(".ssp-form input");
        if (pageInput) {
          pageInput.focus();
          pageInput.select();
          return;
        }
        this.open();
      });
      this.root.addEventListener("click", (e) => {
        if (e.target.closest("[data-ss-close]")) this.close();
      });
      const block = (e) => {
        const list = e.target.closest(".ss-list");
        if (!list || list.scrollHeight <= list.clientHeight) e.preventDefault();
      };
      this.root.addEventListener("wheel", block, { passive: false });
      this.root.addEventListener("touchmove", block, { passive: false });
    }
    /* ---------- 输入 ---------- */
    bindInput() {
      this.input.addEventListener("compositionstart", () => {
        this.composing = true;
      });
      this.input.addEventListener("compositionend", () => {
        this.composing = false;
        this.update();
      });
      this.input.addEventListener("input", () => {
        if (!this.composing) this.update();
      });
      this.clearBtn.addEventListener("click", () => {
        this.input.value = "";
        this.input.focus();
        this.update();
      });
      this.input.addEventListener("keydown", (e) => {
        if (e.isComposing || e.keyCode === 229) return;
        switch (e.key) {
          case "ArrowDown":
            e.preventDefault();
            this.move(1);
            break;
          case "ArrowUp":
            e.preventDefault();
            this.move(-1);
            break;
          case "Home":
            if (e.ctrlKey) {
              e.preventDefault();
              this.setActive(0);
            }
            break;
          case "End":
            if (e.ctrlKey) {
              e.preventDefault();
              this.setActive(this.options().length - 1);
            }
            break;
          case "Enter": {
            e.preventDefault();
            const opt = this.options()[this.active];
            if (opt) this.go(opt, e.metaKey || e.ctrlKey);
            else if (this.input.value.trim()) this.go(this.allLink, e.metaKey || e.ctrlKey);
            break;
          }
          case "Escape":
            e.preventDefault();
            this.close();
            break;
          case "Tab":
            e.preventDefault();
            this.move(e.shiftKey ? -1 : 1);
            break;
        }
      });
      this.root.addEventListener("focusout", (e) => {
        if (this.isOpen && !this.root.contains(e.relatedTarget)) this.input.focus();
      });
    }
    bindList() {
      this.list.addEventListener("mousemove", (e) => {
        const opt = e.target.closest(".ss-opt");
        if (opt) this.setActive(this.options().indexOf(opt), false);
      });
      this.list.addEventListener("click", (e) => {
        const t = e.target;
        const del = t.closest("[data-forget]");
        if (del) {
          e.preventDefault();
          e.stopPropagation();
          forget(del.dataset.forget);
          this.update(true);
          this.input.focus();
          return;
        }
        const opt = t.closest(".ss-opt");
        if (!opt) return;
        if (opt.dataset.query !== void 0) {
          e.preventDefault();
          this.input.value = opt.dataset.query;
          this.update();
          this.input.focus();
          return;
        }
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        this.go(opt, false);
      });
      this.allLink.addEventListener("click", (e) => {
        if (e.metaKey || e.ctrlKey) return;
        e.preventDefault();
        this.go(this.allLink, false);
      });
    }
    /* ---------- 渲染 ---------- */
    update(force = false) {
      const q = this.input.value.trim();
      this.clearBtn.hidden = !this.input.value;
      this.allLink.hidden = !q;
      this.allLink.href = `${this.pageURL}?keyword=${encodeURIComponent(q)}`;
      if (!force && q === this.lastQuery && this.list.childElementCount) return;
      this.lastQuery = q;
      if (!q) return this.renderIdle();
      if (!this.docs) {
        this.list.innerHTML = `<div class="ss-state ss-state--loading">\u6B63\u5728\u52A0\u8F7D\u7D22\u5F15\u2026</div>`;
        return;
      }
      this.renderResults(search(this.docs, q, { sectionLimit: SECTION_LIMIT }));
    }
    renderIdle() {
      let html = "";
      const recents = recent();
      if (recents.length) {
        html += `<div class="ss-group" role="presentation">\u6700\u8FD1\u641C\u7D22</div>`;
        html += recents.map((q, i) => `
<a class="ss-opt ss-opt--recent" href="${this.pageURL}?keyword=${encodeURIComponent(q)}" data-query="${escapeHTML(q)}" id="ss-opt-r${i}" role="option" aria-selected="false" tabindex="-1">
    <span class="ss-hash" aria-hidden="true">${icon("clock")}</span>
    <span class="ss-main"><span class="ss-title">${escapeHTML(q)}</span></span>
    <button type="button" class="ss-forget" data-forget="${escapeHTML(q)}" aria-label="\u5220\u9664\u8FD9\u6761\u641C\u7D22\u8BB0\u5F55" tabindex="-1">${icon("x")}</button>
</a>`).join("");
      }
      if (this.docs) {
        const latest = this.docs.filter((d) => d.k === "post" && d.d).sort((a, b) => b.d.localeCompare(a.d)).slice(0, 5);
        if (latest.length) {
          html += `<div class="ss-group" role="presentation">\u6700\u8FD1\u66F4\u65B0</div>`;
          html += latest.map((d, i) => `
<a class="ss-opt ss-opt--doc ss-opt--compact" href="${escapeHTML(d.u)}" id="ss-opt-l${i}" role="option" aria-selected="false" tabindex="-1">
    ${thumbHTML(d, "ss-thumb")}
    <span class="ss-main">
        <span class="ss-title">${escapeHTML(d.t)}</span>
        <span class="ss-meta">${metaHTML(d)}</span>
    </span>
    <span class="ss-go" aria-hidden="true">${icon("corner-down-left")}</span>
</a>`).join("");
        }
      }
      this.list.innerHTML = html || this.message("\u641C\u7D22\u6587\u7AE0\u3001\u5C0F\u8282\u548C\u9879\u76EE", "\u652F\u6301\u591A\u4E2A\u5173\u952E\u8BCD\uFF0C\u7528\u7A7A\u683C\u5206\u5F00");
      this.setActive(-1);
    }
    renderResults(r) {
      if (!r.hits.length) {
        this.list.innerHTML = this.message(`\u6CA1\u6709\u627E\u5230\u201C${escapeHTML(this.input.value.trim())}\u201D`, "\u6362\u4E2A\u8BF4\u6CD5\uFF0C\u6216\u8005\u5C11\u8F93\u5165\u51E0\u4E2A\u5B57\u8BD5\u8BD5");
        this.setActive(-1);
        return;
      }
      let html = "", n = 0;
      if (r.partial) html += `<div class="ss-note">\u6CA1\u6709\u540C\u65F6\u5305\u542B\u6240\u6709\u5173\u952E\u8BCD\u7684\u7ED3\u679C\uFF0C\u4EE5\u4E0B\u662F\u90E8\u5206\u5339\u914D</div>`;
      for (const hit of r.hits.slice(0, DOC_LIMIT)) {
        html += `<div class="ss-hit" role="group">`;
        html += paletteDoc(hit, `ss-opt-${n++}`);
        for (const s of hit.sections) html += paletteSection(s, `ss-opt-${n++}`);
        html += `</div>`;
      }
      const total = r.hits.length;
      this.allLink.querySelector(".ss-all-count").textContent = total > DOC_LIMIT ? `\u67E5\u770B\u5168\u90E8 ${total} \u4E2A\u7ED3\u679C` : "\u5728\u641C\u7D22\u9875\u4E2D\u67E5\u770B";
      this.list.innerHTML = html;
      this.list.scrollTop = 0;
      this.setActive(0);
    }
    message(title, sub) {
      return `<div class="ss-state">${icon("search")}<p class="ss-state-title">${title}</p><p class="ss-state-sub">${sub}</p></div>`;
    }
    /* ---------- 选中项 ---------- */
    options() {
      return Array.from(this.list.querySelectorAll(".ss-opt"));
    }
    move(delta) {
      const opts = this.options();
      if (!opts.length) return;
      let i = this.active + delta;
      if (i < 0) i = opts.length - 1;
      if (i >= opts.length) i = 0;
      this.setActive(i);
    }
    setActive(i, scroll = true) {
      const opts = this.options();
      opts.forEach((o, j) => o.setAttribute("aria-selected", String(j === i)));
      this.active = i;
      const opt = opts[i];
      if (opt) {
        this.input.setAttribute("aria-activedescendant", opt.id);
        if (scroll) opt.scrollIntoView({ block: "nearest" });
      } else {
        this.input.removeAttribute("aria-activedescendant");
      }
    }
    /* ---------- 跳转 ---------- */
    go(link, newTab) {
      if (link.dataset.query !== void 0) {
        this.input.value = link.dataset.query;
        this.update();
        return;
      }
      const q = this.input.value.trim();
      if (q) remember(q);
      if (newTab) {
        window.open(link.href, "_blank", "noopener");
        return;
      }
      const url = new URL(link.href, location.href);
      const samePage = url.pathname === location.pathname && url.search === location.search;
      this.close();
      if (samePage && url.hash) {
        history.pushState(null, "", url.hash);
        const el = document.getElementById(decodeURIComponent(url.hash.slice(1)));
        el?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
        flash(el);
      } else {
        location.href = link.href;
      }
    }
  };
  function isEditable(t) {
    const el = t;
    return !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  }
  function flash(el) {
    if (!el) return;
    el.classList.remove("ss-flash");
    void el.offsetWidth;
    el.classList.add("ss-flash");
    el.addEventListener("animationend", () => el.classList.remove("ss-flash"), { once: true });
  }
  function init() {
    const root = document.getElementById("site-search");
    if (!root) return;
    const palette = new Palette(root);
    window.siteSearch = palette;
    if (location.hash) {
      const el = document.getElementById(decodeURIComponent(location.hash.slice(1)));
      if (el) flash(el);
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
