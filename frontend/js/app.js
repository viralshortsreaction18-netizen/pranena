/* Pranena — application controller. */
(function () {
  "use strict";

  const API = window.PranenaAPI;
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  const state = {
    docId: null,
    doc: null,
    issues: [],
    activePageId: null,
    selectedBlockId: null,
    zoom: 1,
    fitMode: true,
    showBoxes: true,
    polling: null,
    lastCorrections: [],
    progress: { blocks: 0, verified: 0, flagged: 0, percent: 0 },
  };

  const BLOCK_TYPES = [
    "title", "subtitle", "section", "instruction", "question", "options",
    "subcompletion", "marks", "note", "diagram_caption", "table",
    "header", "footer", "unknown",
  ];

  const FLAG_SEVERITY = {
    engine_disagreement: "high",
    hallucination_risk: "high",
    suspect_truncation: "high",
    low_confidence: "mid",
    devanagari_confusion: "mid",
    missing_marks: "low",
    unknown_word: "low",
    empty: "mid",
  };

  /* ══════════════════════════ helpers ══════════════════════════ */

  function toast(msg, kind, ms) {
    const el = document.createElement("div");
    el.className = "toast" + (kind ? " " + kind : "");
    el.textContent = msg;
    $("#toasts").appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transition = "opacity .3s";
      setTimeout(() => el.remove(), 320);
    }, ms || 4200);
  }

  function busy(on, title, sub) {
    const b = $("#busy");
    b.hidden = !on;
    if (on) {
      $("#busy-title").textContent = title || "Working…";
      $("#busy-sub").textContent = sub || "Please wait";
    }
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function flagsOf(block) {
    return (block.flags || []).map((f) => (typeof f === "string" ? f : f.value));
  }

  /* ══════════════════════════ document lifecycle ══════════════════════════ */

  let booted = false;

  async function boot() {
    // Guard against a double boot (the DOMContentLoaded event can fire more
    // than once when the page is restored from the back/forward cache).
    if (booted) return;
    booted = true;
    wireStaticEvents();
    try {
      const { documents } = await API.listDocuments();
      if (documents && documents.length) {
        await loadDocument(documents[0].id);
      } else {
        const doc = await API.createDocument("Untitled paper");
        await loadDocument(doc.id);
      }
    } catch (e) {
      toast("Could not reach the server: " + e.message, "err");
    }
  }

  async function loadDocument(id) {
    state.docId = id;
    const data = await API.getDocument(id);
    state.doc = data.document;
    state.issues = data.issues || [];
    state.progress = data.progress || state.progress;
    const pages = state.doc.pages || [];
    if (!pages.find((p) => p.id === state.activePageId)) {
      state.activePageId = pages.length ? pages[0].id : null;
    }
    $("#doc-name").value = state.doc.name || "";
    renderAll();
  }

  async function refresh() {
    if (!state.docId) return;
    const data = await API.getDocument(state.docId);
    state.doc = data.document;
    state.issues = data.issues || [];
    state.progress = data.progress || state.progress;
    renderAll();
  }

  function renderAll() {
    renderTop();
    renderPages();
    renderBlocks();
    renderViewer();
    renderPreview();
    renderPaperForm();
    renderProgress();
  }

  function renderTop() {
    const doc = state.doc;
    if (!doc) return;
    const pages = (doc.pages || []).length;
    const blocks = (state.progress && state.progress.blocks) || 0;
    $("#doc-meta").textContent =
      pages + (pages === 1 ? " page" : " pages") +
      (blocks ? " · " + blocks + " lines" : "");
    $$("#lang-seg button").forEach((b) =>
      b.classList.toggle("active", b.dataset.lang === (doc.language || "mixed")));
  }

  function renderProgress() {
    const p = state.doc ? state.doc.progress : null;
    const prog = p || { percent: 0, verified: 0, blocks: 0, flagged: 0 };
    $("#progress-pct").textContent = (prog.percent || 0) + "%";
    $("#progress-bar").style.width = (prog.percent || 0) + "%";
    $("#progress-detail").textContent =
      (prog.verified || 0) + " of " + (prog.blocks || 0) + " lines reviewed" +
      (prog.flagged ? " · " + prog.flagged + " flagged" : "");
  }

  /* ══════════════════════════ pages ══════════════════════════ */

  function renderPages() {
    const list = $("#page-list");
    const pages = (state.doc && state.doc.pages) || [];
    list.innerHTML = "";
    if (!pages.length) {
      const hint = document.createElement("div");
      hint.className = "muted small";
      hint.style.padding = "4px 2px";
      hint.textContent = "No pages yet.";
      list.appendChild(hint);
      return;
    }
    pages.forEach((page, idx) => {
      const card = document.createElement("div");
      card.className = "page-card" + (page.id === state.activePageId ? " active" : "");
      card.dataset.pageId = page.id;

      const thumb = document.createElement("div");
      thumb.className = "page-thumb";
      if (page.width && page.height) {
        const img = document.createElement("img");
        img.className = "page-thumb";
        img.src = `/api/documents/${state.docId}/pages/${page.id}/image?variant=prepared&t=${page.preprocessed ? 1 : 0}`;
        img.alt = "";
        img.onerror = () => { img.replaceWith(thumb); };
        thumb.replaceWith(img);
      } else {
        thumb.textContent = page.order + 1;
      }

      const info = document.createElement("div");
      info.className = "page-info";
      const name = document.createElement("div");
      name.className = "page-name";
      name.textContent = page.filename || "Page " + (idx + 1);
      name.title = page.filename;

      const sub = document.createElement("div");
      sub.className = "page-sub";
      const status = page.status === "done" ? "ok" : page.status === "running" ? "run"
        : page.status === "error" ? "err" : "todo";
      const label = page.status === "done" ? (page.blocks || []).length + " lines"
        : page.status === "running" ? "reading…"
        : page.status === "error" ? "failed" : "not read";
      sub.innerHTML = `<span class="pill ${status}">${esc(label)}</span>`;
      if (page.agreement) {
        sub.innerHTML += `<span title="engine agreement">${Math.round(page.agreement * 100)}%</span>`;
      }

      const actions = document.createElement("div");
      actions.className = "page-actions";
      const up = mkBtn("↑", "Move up", () => movePage(idx, -1));
      const down = mkBtn("↓", "Move down", () => movePage(idx, 1));
      const del = mkBtn("✕", "Delete page", () => deletePage(page.id));
      del.classList.add("del");
      actions.append(up, down, del);

      info.append(name, sub, actions);
      card.append(thumb, info);
      card.addEventListener("click", (e) => {
        if (e.target.closest(".page-actions")) return;
        state.activePageId = page.id;
        state.selectedBlockId = null;
        renderPages();
        renderBlocks();
        renderViewer();
      });
      list.appendChild(card);
    });
    updatePageIndicator();
  }

  function mkBtn(text, title, onClick) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = text;
    b.title = title;
    b.addEventListener("click", (e) => { e.stopPropagation(); onClick(); });
    return b;
  }

  async function movePage(index, delta) {
    const pages = state.doc.pages.slice();
    const target = index + delta;
    if (target < 0 || target >= pages.length) return;
    const tmp = pages[index];
    pages[index] = pages[target];
    pages[target] = tmp;
    const ids = pages.map((p) => p.id);
    await API.reorderPages(state.docId, ids);
    await refresh();
  }

  async function deletePage(pageId) {
    if (!confirm("Remove this page and its extracted text?")) return;
    await API.deletePage(state.docId, pageId);
    if (state.activePageId === pageId) state.activePageId = null;
    await refresh();
  }

  async function uploadFiles(fileList) {
    const files = Array.from(fileList).filter((f) => f.type.startsWith("image/"));
    if (!files.length) {
      toast("Please choose image files (JPG, PNG or WebP).", "warn");
      return;
    }
    busy(true, "Uploading…", files.length + " page(s)");
    try {
      await API.uploadPages(state.docId, files);
      toast(files.length + " page(s) added.", "ok");
      await refresh();
    } catch (e) {
      toast("Upload failed: " + e.message, "err");
    } finally {
      busy(false);
    }
  }

  /* ══════════════════════════ viewer ══════════════════════════ */

  function currentPage() {
    const pages = (state.doc && state.doc.pages) || [];
    return pages.find((p) => p.id === state.activePageId) || pages[0] || null;
  }

  function updatePageIndicator() {
    const pages = (state.doc && state.doc.pages) || [];
    const idx = pages.findIndex((p) => p.id === state.activePageId);
    $("#page-indicator").textContent = pages.length ? (idx + 1) + " / " + pages.length : "–";
  }

  function renderViewer() {
    const page = currentPage();
    const wrap = $("#canvas-wrap");
    const empty = $("#viewer-empty");
    if (!page) {
      wrap.hidden = true;
      empty.hidden = false;
      $("#viewer-title").textContent = "No page selected";
      return;
    }
    wrap.hidden = false;
    empty.hidden = true;
    $("#viewer-title").textContent = page.filename || "Page";
    updatePageIndicator();

    const img = $("#page-image");
    const src = `/api/documents/${state.docId}/pages/${page.id}/image?variant=prepared&t=${page.preprocessed ? 1 : 0}`;
    if (img.dataset.src !== src) {
      img.dataset.src = src;
      img.onload = () => { applyZoom(); renderOverlay(); };
      img.onerror = () => { applyZoom(); renderOverlay(); };
      img.src = src;
    }
    // The overlay lives in percentage space, so it can be drawn straight away
    // instead of waiting for the image to decode.
    applyZoom();
    renderOverlay();
  }

  function applyZoom() {
    const img = $("#page-image");
    if (!img.naturalWidth) return;
    let scale = state.zoom;
    if (state.fitMode) {
      const body = $("#viewer-body");
      const avail = body.clientWidth - 44;
      scale = Math.min(1, avail / img.naturalWidth);
    }
    img.style.width = Math.round(img.naturalWidth * scale) + "px";
    img.style.height = "auto";
    $("#zoom-level").textContent = Math.round(scale * 100) + "%";
  }

  function renderOverlay() {
    const svg = $("#overlay");
    const page = currentPage();
    svg.innerHTML = "";
    if (!page || !state.showBoxes) return;
    (page.blocks || []).forEach((b) => {
      if (!b.bbox) return;
      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      const flags = flagsOf(b);
      rect.setAttribute("x", b.bbox[0] * 100);
      rect.setAttribute("y", b.bbox[1] * 100);
      rect.setAttribute("width", Math.max(0.4, (b.bbox[2] - b.bbox[0]) * 100));
      rect.setAttribute("height", Math.max(0.4, (b.bbox[3] - b.bbox[1]) * 100));
      if (flags.length) rect.classList.add("flagged");
      if (b.id === state.selectedBlockId) rect.classList.add("selected");
      const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
      title.textContent = (b.type || "") + " · " + Math.round((b.confidence || 0) * 100) + "%";
      rect.appendChild(title);
      rect.addEventListener("click", () => selectBlock(b.id, true));
      svg.appendChild(rect);
    });
  }

  function selectBlock(blockId, scrollTo) {
    state.selectedBlockId = blockId;
    $$(".block").forEach((el) => el.classList.toggle("selected", el.dataset.id === blockId));
    renderOverlay();
    if (scrollTo) {
      const el = $('.block[data-id="' + blockId + '"]');
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
      scrollViewerToBlock(blockId);
    }
  }

  /** Bring the region of the page that the selected line occupies into view. */
  function scrollViewerToBlock(blockId) {
    const page = currentPage();
    if (!page) return;
    const block = (page.blocks || []).find((b) => b.id === blockId);
    if (!block || !block.bbox) return;
    const body = $("#viewer-body");
    const wrap = $("#canvas-wrap");
    if (!body || !wrap) return;
    const img = $("#page-image");
    const displayHeight = img.clientHeight || wrap.clientHeight;
    const target = block.bbox[1] * displayHeight - body.clientHeight / 2;
    body.scrollTo({
      top: Math.max(0, target),
      behavior: "smooth",
    });
  }

  /* ══════════════════════════ blocks (transcription) ══════════════════════════ */

  function renderBlocks() {
    const list = $("#block-list");
    const q = ($("#search").value || "").trim().toLowerCase();
    const onlyFlagged = $("#filter-flagged").checked;
    const onlyTodo = $("#filter-todo").checked;

    list.innerHTML = "";
    let shown = 0;

    const pages = (state.doc && state.doc.pages) || [];
    pages.forEach((page) => {
      const blocks = (page.blocks || []).filter((b) => {
        if (q && !(b.text || "").toLowerCase().includes(q)) return false;
        if (onlyFlagged && !flagsOf(b).length) return false;
        if (onlyTodo && b.review_status !== "pending") return false;
        return true;
      });
      if (!blocks.length) return;
      shown += blocks.length;

      if (pages.length > 1) {
        const divider = document.createElement("div");
        divider.className = "page-divider";
        divider.textContent = "Page " + (page.order + 1) +
          (page.filename ? " · " + page.filename : "");
        list.appendChild(divider);
      }
      blocks.forEach((b) => list.appendChild(blockEl(b, page)));
    });

    if (!shown) {
      const empty = document.createElement("div");
      empty.className = "empty-state small";
      const total = (state.progress && state.progress.blocks) || 0;
      empty.innerHTML = total
        ? "<p>No lines match the current filter.</p>"
        : "<p>Nothing extracted yet.</p><p class='muted'>Upload pages and press <strong>Read paper</strong>.</p>";
      list.appendChild(empty);
    }
  }

  function blockEl(b, page) {
    const el = document.createElement("div");
    el.className = "block";
    el.dataset.id = b.id;
    const flags = flagsOf(b);
    if (flags.length) el.classList.add("flagged");
    if (b.review_status !== "pending") el.classList.add("reviewed");

    /* header */
    const head = document.createElement("div");
    head.className = "block-head";

    const type = document.createElement("select");
    type.className = "block-type";
    BLOCK_TYPES.forEach((t) => {
      const o = document.createElement("option");
      o.value = t; o.textContent = t;
      if (t === b.type) o.selected = true;
      type.appendChild(o);
    });
    type.addEventListener("change", async () => {
      await API.updateBlock(state.docId, b.id, { type: type.value });
      await refresh();
    });

    const conf = Math.round((b.confidence || 0) * 100);
    const bar = document.createElement("div");
    bar.className = "block-conf" + (conf >= 85 ? "" : conf >= 65 ? " mid" : " low");
    bar.innerHTML = `<i style="width:${conf}%"></i>`;
    bar.title = "Confidence " + conf + "%";

    const pct = document.createElement("span");
    pct.className = "block-pct";
    pct.textContent = conf + "%";

    const dots = document.createElement("div");
    dots.className = "block-flags";
    flags.forEach((f) => {
      const d = document.createElement("span");
      d.className = "flag-dot " + (FLAG_SEVERITY[f] || "low");
      d.title = f.replace(/_/g, " ");
      dots.appendChild(d);
    });

    const ok = document.createElement("button");
    ok.className = "block-ok" + (b.review_status !== "pending" ? " done" : "");
    ok.type = "button";
    ok.textContent = b.review_status !== "pending" ? "✓" : "○";
    ok.title = "Mark as reviewed";
    ok.addEventListener("click", async () => {
      await API.review(state.docId, b.id, {
        status: b.review_status === "pending" ? "verified" : "pending",
      });
      await refresh();
    });

    head.append(type, bar, pct, dots, ok);

    /* body */
    const body = document.createElement("div");
    body.className = "block-body";

    const text = document.createElement("div");
    text.className = "block-text";
    text.setAttribute("contenteditable", "true");
    text.setAttribute("spellcheck", "false");
    text.spellcheck = false;
    text.textContent = b.text || "";
    text.addEventListener("focus", () => {
      state.selectedBlockId = b.id;
      renderOverlay();
      $$(".block").forEach((x) => x.classList.toggle("selected", x.dataset.id === b.id));
    });
    text.addEventListener("blur", async () => {
      const newText = text.innerText.replace(/ /g, " ").replace(/\n$/, "");
      if (newText !== (b.text || "")) {
        await API.updateBlock(state.docId, b.id, { text: newText });
        b.text = newText;
        toast("Line updated.", "ok", 1600);
        await refresh();
      }
    });
    text.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); text.blur(); }
      if (e.key === "Escape") { text.textContent = b.text || ""; text.blur(); }
    });

    const meta = document.createElement("div");
    meta.className = "block-meta";
    const marks = document.createElement("input");
    marks.className = "mini-field";
    marks.placeholder = "marks";
    marks.value = b.marks || "";
    marks.title = "Marks for this question";
    marks.addEventListener("change", async () => {
      await API.updateBlock(state.docId, b.id, { marks: marks.value });
      await refresh();
    });
    const qnum = document.createElement("input");
    qnum.className = "mini-field";
    qnum.placeholder = "Q#";
    qnum.value = b.question_number || "";
    qnum.title = "Question number";
    qnum.addEventListener("change", async () => {
      await API.updateBlock(state.docId, b.id, { question_number: qnum.value });
      await refresh();
    });
    const scriptTag = document.createElement("span");
    scriptTag.className = "pill todo";
    scriptTag.textContent = b.script === "devanagari" ? "देवनागरी" : b.script === "latin" ? "latin" : "?";
    scriptTag.title = "Detected script";
    meta.append(qnum, marks, scriptTag);

    if (b.note) {
      const note = document.createElement("div");
      note.className = "block-note";
      note.textContent = b.note;
      meta.appendChild(note);
    }

    body.append(text, meta);

    /* alternative readings */
    const readings = b.engine_texts || {};
    const keys = Object.keys(readings);
    if (keys.length > 1) {
      const v = document.createElement("div");
      v.className = "block-variants";
      keys.forEach((k) => {
        const row = document.createElement("div");
        row.className = "variant";
        row.innerHTML = "<b>" + esc(k) + ":</b> " + esc(readings[k]);
        v.appendChild(row);
      });
      const toggle = document.createElement("button");
      toggle.className = "btn tiny ghost";
      toggle.type = "button";
      toggle.textContent = "Show " + keys.length + " readings";
      toggle.addEventListener("click", () => {
        v.classList.toggle("open");
        toggle.textContent = v.classList.contains("open")
          ? "Hide readings" : "Show " + keys.length + " readings";
      });
      meta.appendChild(toggle);
      body.appendChild(v);
    }

    el.append(head, body);
    return el;
  }

  /* ══════════════════════════ paper preview ══════════════════════════ */

  function renderPreview() {
    const host = $("#preview");
    const doc = state.doc;
    if (!doc) { host.innerHTML = ""; return; }
    const s = doc.settings || {};
    const pages = doc.pages || [];
    if (!pages.some((p) => (p.blocks || []).length)) {
      host.innerHTML = "<div class='empty-state small'><p>Nothing to preview yet.</p></div>";
      return;
    }

    const parts = [];
    parts.push('<div class="paper">');
    if (s.title) parts.push(`<h1>${esc(s.title)}</h1>`);
    if (s.subtitle) parts.push(`<div class="sub">${esc(s.subtitle)}</div>`);
    const meta = [];
    if (s.subject) meta.push("Subject: " + s.subject);
    if (s.class_name) meta.push("Class: " + s.class_name);
    if (s.duration) meta.push("Duration: " + s.duration);
    if (s.max_marks) meta.push("Max. marks: " + s.max_marks);
    if (s.date) meta.push("Date: " + s.date);
    if (meta.length) parts.push(`<div class="meta">${esc(meta.join("  ·  "))}</div>`);

    let counter = 0;
    pages.forEach((page) => {
      (page.blocks || []).forEach((b) => {
        const t = (b.text || "").trim();
        if (!t) return;
        const type = b.type;
        if (type === "title") parts.push(`<div class="paper-title2">${esc(t)}</div>`);
        else if (type === "subtitle") parts.push(`<div class="paper-sub2">${esc(t)}</div>`);
        else if (type === "section") parts.push(`<div class="paper-section">${esc(t)}</div>`);
        else if (type === "instruction") parts.push(`<div class="paper-instr">${esc(t)}</div>`);
        else if (type === "question") {
          counter++;
          let body = t;
          if (s.renumber_questions) {
            body = t.replace(/^\s*(?:\d{1,3}|[०-९]{1,3})\s*[.)]\s*/, "");
            body = (s.question_number_format || "{n}.").replace("{n}", counter) + " " + body;
          }
          const mk = b.marks && s.show_marks_column ? `<span class="mk">(${esc(b.marks)})</span>` : "";
          parts.push(`<div class="paper-q"><span class="mk">${mk}</span><span class="txt">${esc(body)}</span></div>`);
        }
        else if (type === "options") parts.push(`<div class="paper-opts">${esc(t)}</div>`);
        else if (type === "subcompletion") parts.push(`<div class="paper-sub">- ${esc(t)}</div>`);
        else if (type === "note") parts.push(`<div class="paper-note">${esc(t)}</div>`);
        else parts.push(`<div>${esc(t)}</div>`);
      });
    });
    parts.push("</div>");
    host.innerHTML = parts.join("");
  }

  /* ══════════════════════════ paper details form ══════════════════════════ */

  function renderPaperForm() {
    const s = (state.doc && state.doc.settings) || {};
    $$("#paper-form [data-field]").forEach((el) => {
      const key = el.dataset.field;
      if (!(key in s)) return;
      if (el.type === "checkbox") el.checked = !!s[key];
      else el.value = s[key];
    });
  }

  async function savePaperForm() {
    const s = Object.assign({}, state.doc.settings || {});
    $$("#paper-form [data-field]").forEach((el) => {
      const key = el.dataset.field;
      if (el.type === "checkbox") s[key] = el.checked;
      else if (el.type === "number") s[key] = parseFloat(el.value) || 0;
      else s[key] = el.value;
    });
    await API.updateSettings(state.docId, s);
    state.doc.settings = s;
    renderPreview();
    toast("Paper details saved.", "ok");
  }

  /* ══════════════════════════ OCR ══════════════════════════ */

  async function runProcess() {
    if (!state.docId) return;
    const pages = (state.doc.pages || []).length;
    if (!pages) { toast("Add at least one page image first.", "warn"); return; }
    busy(true, "Reading the paper…", "Vision models are transcribing each page.");
    try {
      await API.process(state.docId, { language: state.doc.language || "mixed", verify: false });
      busy(false);
      toast("Reading started — this can take a minute per page.", "ok");
      startPolling();
    } catch (e) {
      busy(false);
      toast("Could not start: " + e.message, "err");
    }
  }

  function startPolling() {
    if (state.polling) clearInterval(state.polling);
    state.polling = setInterval(async () => {
      try {
        const data = await API.getDocument(state.docId);
        const wasRunning = state.doc && state.doc.status === "running";
        state.doc = data.document;
        state.issues = data.issues || [];
        renderPages();
        renderBlocks();
        renderPreview();
        renderProgress();
        if (state.doc.status !== "running") {
          clearInterval(state.polling);
          state.polling = null;
          busy(false);
          const total = state.doc.pages.reduce((n, p) => n + (p.blocks || []).length, 0);
          toast(wasRunning ? "Finished — " + total + " lines extracted." : "Nothing new to read.",
            wasRunning ? "ok" : "warn");
        }
      } catch (e) {
        clearInterval(state.polling);
        state.polling = null;
        busy(false);
      }
    }, 1400);
  }

  async function runVerify(apply) {
    if (!state.docId) return;
    if (!state.doc.pages.some((p) => (p.blocks || []).length)) {
      toast("Read the paper first.", "warn");
      return;
    }
    busy(true, "Proofreading…", "Comparing every line against the original image.");
    try {
      const res = await API.verify(state.docId, { apply: !!apply });
      state.lastCorrections = res.corrections || 0;
      await refresh();
      busy(false);
      toast(res.corrections
        ? res.corrections + " proposed correction(s)."
        : "No differences found — the transcription matches the image.",
        res.corrections ? "warn" : "ok");
      renderCorrections();
    } catch (e) {
      busy(false);
      toast("Verification failed: " + e.message, "err");
    }
  }

  function renderCorrections() {
    const host = $("#corrections");
    const pages = (state.doc && state.doc.pages) || [];
    const all = [];
    pages.forEach((p) => (p.corrections || []).forEach((c) => all.push({ c: c, page: p })));
    if (!all.length) { host.hidden = true; host.innerHTML = ""; return; }
    host.hidden = false;
    host.innerHTML = "<h4>AI proofreading — " + all.length + " proposed change(s)</h4>";
    all.forEach(({ c, page }) => {
      const row = document.createElement("div");
      row.className = "corr";
      row.innerHTML =
        '<div><span class="from">' + esc(c.original) + '</span> → <span class="to">' + esc(c.corrected) + '</span></div>' +
        (c.reason ? '<div class="why">' + esc(c.reason) + '</div>' : "");
      const acts = document.createElement("div");
      acts.className = "corr-actions";
      const accept = document.createElement("button");
      accept.className = "btn tiny primary";
      accept.textContent = c.applied ? "Applied ✓" : "Accept";
      accept.disabled = !!c.applied;
      accept.addEventListener("click", async () => {
        await API.updateBlock(state.docId, c.block_id, { text: c.corrected });
        await refresh();
        renderCorrections();
        toast("Correction applied.", "ok", 1800);
      });
      const jump = document.createElement("button");
      jump.className = "btn tiny ghost";
      jump.textContent = "Show in image";
      jump.addEventListener("click", () => {
        state.activePageId = page.id;
        state.selectedBlockId = c.block_id;
        renderPages(); renderBlocks(); renderViewer();
        selectBlock(c.block_id, true);
      });
      acts.append(accept, jump);
      row.appendChild(acts);
      host.appendChild(row);
    });
  }

  /* ══════════════════════════ settings modal ══════════════════════════ */

  async function openSettings() {
    const modal = $("#settings-modal");
    modal.hidden = false;
    const [provs, engs] = await Promise.all([API.providers(), API.engines()]);
    renderProviders(provs.providers || []);
    renderEngines(engs.engines || []);
  }

  function renderProviders(providers) {
    const host = $("#provider-list");
    host.innerHTML = "";
    providers.forEach((p) => {
      const box = document.createElement("div");
      box.className = "provider";

      const top = document.createElement("div");
      top.className = "provider-top";
      const nm = document.createElement("div");
      nm.className = "provider-name";
      nm.textContent = p.label;
      const badge = document.createElement("span");
      badge.className = "configured";
      badge.textContent = p.configured ? "configured" : (p.local ? "offline" : "no key");
      if (!p.configured && !p.local) badge.style.background = "var(--surface-2)";
      if (!p.configured && !p.local) badge.style.color = "var(--muted)";
      top.append(nm, badge);

      const note = document.createElement("div");
      note.className = "provider-note";
      note.textContent = p.notes || "";

      const fields = document.createElement("div");
      fields.className = "provider-fields";

      if (p.local) {
        const info = document.createElement("span");
        info.className = "provider-note";
        info.textContent = "Runs entirely on this machine — no key required.";
        fields.appendChild(info);
      } else {
        const key = document.createElement("input");
        key.className = "key";
        key.type = "password";
        key.placeholder = "Paste your API key";
        key.autocomplete = "off";
        key.dataset.provider = p.id;

        const model = document.createElement("select");
        model.dataset.provider = p.id;
        const models = p.recommended_models && p.recommended_models.length
          ? p.recommended_models : [p.default_model];
        models.forEach((m) => {
          const o = document.createElement("option");
          o.value = m; o.textContent = m;
          model.appendChild(o);
        });
        model.value = p.default_model;

        const btn = document.createElement("button");
        btn.className = "btn tiny primary";
        btn.type = "button";
        btn.textContent = "Use this";
        btn.addEventListener("click", async () => {
          const k = key.value.trim();
          if (!k) { toast("Paste an API key first.", "warn"); return; }
          try {
            await API.saveEngine({
              id: p.id, label: p.label, provider: p.id, model: model.value,
              api_key: k, base_url: p.base_url, enabled: true,
            });
            key.value = "";
            toast(p.label + " enabled.", "ok");
            openSettings();
          } catch (e) { toast("Could not save: " + e.message, "err"); }
        });

        fields.append(key, model, btn);
        if (p.key_url) {
          const link = document.createElement("a");
          link.className = "link";
          link.href = p.key_url;
          link.target = "_blank";
          link.rel = "noopener";
          link.textContent = "Get a key ↗";
          fields.appendChild(link);
        }
      }
      box.append(top, note, fields);
      host.appendChild(box);
    });
  }

  function renderEngines(engines) {
    const host = $("#engine-list");
    host.innerHTML = "";
    if (!engines.length) {
      host.innerHTML = "<p class='muted small'>No engines configured yet.</p>";
      return;
    }
    engines.forEach((e) => {
      const row = document.createElement("div");
      row.className = "engine-row" + (e.enabled ? "" : " off");

      const name = document.createElement("span");
      name.className = "name";
      name.textContent = e.label;

      const model = document.createElement("span");
      model.className = "model";
      model.textContent = e.model;

      const toggle = document.createElement("label");
      toggle.className = "chk";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = e.enabled;
      cb.addEventListener("change", async () => {
        await API.saveEngine(Object.assign({}, e, { enabled: cb.checked, api_key: undefined, has_key: undefined }));
        toast(e.label + (cb.checked ? " enabled." : " disabled."), "ok", 1600);
      });
      toggle.append(cb, document.createTextNode(" use"));
      row.append(name, model, toggle);

      if (!e.local) {
        const del = document.createElement("button");
        del.className = "btn tiny danger";
        del.textContent = "Remove";
        del.addEventListener("click", async () => {
          await API.deleteEngine(e.id);
          toast(e.label + " removed.", "ok");
          openSettings();
        });
        row.appendChild(del);
      }
      host.appendChild(row);
    });
  }

  /* ══════════════════════════ export ══════════════════════════ */

  async function doExport(fmt) {
    if (!state.docId) return;
    if (!state.doc.pages.some((p) => (p.blocks || []).length)) {
      toast("Nothing to export yet.", "warn");
      return;
    }
    busy(true, "Preparing export…", "Building the Word document.");
    try {
      if (fmt === "pdf") {
        const { blob } = await API.exportDoc(state.docId, "html");
        const url = URL.createObjectURL(blob);
        const w = window.open(url, "_blank");
        if (w) {
          w.onload = () => { setTimeout(() => w.print(), 350); };
        } else {
          toast("Allow pop-ups to print to PDF.", "warn");
        }
        busy(false);
        return;
      }
      const { blob, filename } = await API.exportDoc(state.docId, fmt);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      busy(false);
      toast("Downloaded " + filename, "ok");
    } catch (e) {
      busy(false);
      toast("Export failed: " + e.message, "err");
    }
  }

  /* ══════════════════════════ wiring ══════════════════════════ */

  function wireStaticEvents() {
    /* page upload */
    const dz = $("#dropzone");
    dz.addEventListener("click", () => {
      const input = document.createElement("input");
      input.type = "file";
      input.accept = "image/*";
      input.multiple = true;
      input.addEventListener("change", () => uploadFiles(input.files));
      input.click();
    });
    dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") dz.click(); });
    ["dragenter", "dragover"].forEach((ev) =>
      dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("dragging"); }));
    ["dragleave", "drop"].forEach((ev) =>
      dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("dragging"); }));
    dz.addEventListener("drop", (e) => uploadFiles(e.dataTransfer.files));
    $("#btn-add-page").addEventListener("click", () => dz.click());

    /* language */
    $$("#lang-seg button").forEach((b) =>
      b.addEventListener("click", async () => {
        await API.updateDocument(state.docId, { language: b.dataset.lang });
        state.doc.language = b.dataset.lang;
        renderTop();
        toast("Language set to " + b.textContent + ".", "ok", 1800);
      }));

    /* paper name */
    $("#doc-name").addEventListener("change", async (e) => {
      await API.updateDocument(state.docId, { name: e.target.value });
      state.doc.name = e.target.value;
    });

    /* main actions */
    $("#btn-process").addEventListener("click", runProcess);
    $("#btn-verify").addEventListener("click", () => runVerify(false));
    $("#btn-verify-all").addEventListener("click", async () => {
      await API.review(state.docId, "all", { status: "verified", scope: "all" });
      await refresh();
      toast("Everything marked as reviewed.", "ok");
    });
    $("#btn-export").addEventListener("click", () => doExport("docx"));
    $("#btn-export-menu").addEventListener("click", (e) => {
      e.stopPropagation();
      const m = $("#export-menu");
      m.hidden = !m.hidden;
    });
    $$("#export-menu button").forEach((b) =>
      b.addEventListener("click", () => {
        $("#export-menu").hidden = true;
        doExport(b.dataset.fmt);
      }));
    document.addEventListener("click", () => { $("#export-menu").hidden = true; });

    /* tabs */
    $$(".tab").forEach((t) =>
      t.addEventListener("click", () => {
        $$(".tab").forEach((x) => x.classList.remove("active"));
        $$(".tab-panel").forEach((x) => x.classList.remove("active"));
        t.classList.add("active");
        $('.tab-panel[data-panel="' + t.dataset.tab + '"]').classList.add("active");
      }));

    /* filters */
    $("#search").addEventListener("input", renderBlocks);
    $("#filter-flagged").addEventListener("change", renderBlocks);
    $("#filter-todo").addEventListener("change", renderBlocks);

    /* zoom */
    $("#zoom-in").addEventListener("click", () => { state.fitMode = false; state.zoom = Math.min(4, state.zoom * 1.25); applyZoom(); renderOverlay(); });
    $("#zoom-out").addEventListener("click", () => { state.fitMode = false; state.zoom = Math.max(0.1, state.zoom / 1.25); applyZoom(); renderOverlay(); });
    $("#zoom-fit").addEventListener("click", () => { state.fitMode = true; applyZoom(); renderOverlay(); });
    $("#toggle-boxes").addEventListener("change", (e) => { state.showBoxes = e.target.checked; renderOverlay(); });

    /* page nav */
    $("#prev-page").addEventListener("click", () => stepPage(-1));
    $("#next-page").addEventListener("click", () => stepPage(1));

    /* settings modal */
    $("#btn-settings").addEventListener("click", openSettings);
    $("#settings-close").addEventListener("click", () => { $("#settings-modal").hidden = true; });
    $("#settings-save").addEventListener("click", () => { $("#settings-modal").hidden = true; });
    $("#settings-modal").addEventListener("click", (e) => {
      if (e.target.id === "settings-modal") $("#settings-modal").hidden = true;
    });

    /* paper form */
    $("#btn-save-settings").addEventListener("click", savePaperForm);

    /* keyboard */
    document.addEventListener("keydown", (e) => {
      if (e.target.isContentEditable || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
      if (e.key === "ArrowRight") stepPage(1);
      if (e.key === "ArrowLeft") stepPage(-1);
      if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); savePaperForm(); }
      if ((e.ctrlKey || e.metaKey) && e.key === "e") { e.preventDefault(); doExport("docx"); }
    });

    window.addEventListener("resize", () => { applyZoom(); renderOverlay(); });
  }

  function stepPage(delta) {
    const pages = (state.doc && state.doc.pages) || [];
    if (!pages.length) return;
    const idx = pages.findIndex((p) => p.id === state.activePageId);
    const next = Math.max(0, Math.min(pages.length - 1, (idx < 0 ? 0 : idx) + delta));
    state.activePageId = pages[next].id;
    state.selectedBlockId = null;
    renderPages();
    renderBlocks();
    renderViewer();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
