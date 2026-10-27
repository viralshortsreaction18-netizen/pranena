/* Pranena — API client. */
(function (global) {
  "use strict";

  async function request(path, options = {}) {
    const opts = Object.assign({ headers: {} }, options);
    if (opts.body && !(opts.body instanceof FormData)) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const j = await res.json();
        detail = j.detail || detail;
      } catch (_) { /* not json */ }
      const err = new Error(detail);
      err.status = res.status;
      throw err;
    }
    const type = res.headers.get("content-type") || "";
    if (type.includes("application/json")) return res.json();
    if (type.includes("text/")) return res.text();
    return res.blob();
  }

  const api = {
    health: () => request("/api/health"),
    providers: () => request("/api/providers"),
    engines: () => request("/api/engines"),
    saveEngine: (spec) => request("/api/engines", { method: "POST", body: spec }),
    deleteEngine: (id) => request("/api/engines/" + encodeURIComponent(id), { method: "DELETE" }),

    listDocuments: () => request("/api/documents"),
    createDocument: (name) => request("/api/documents", { method: "POST", body: { name: name || "Untitled paper" } }),
    getDocument: (id) => request("/api/documents/" + id),
    deleteDocument: (id) => request("/api/documents/" + id, { method: "DELETE" }),
    updateDocument: (id, patch) => request("/api/documents/" + id, { method: "PATCH", body: patch }),
    updateSettings: (id, settings) => request("/api/documents/" + id + "/settings", { method: "PATCH", body: settings }),

    uploadPages: (id, files) => {
      const fd = new FormData();
      for (const f of files) fd.append("files", f, f.name);
      return request("/api/documents/" + id + "/pages", { method: "POST", body: fd });
    },
    deletePage: (id, pageId) => request("/api/documents/" + id + "/pages/" + pageId, { method: "DELETE" }),
    reorderPages: (id, ids) => request("/api/documents/" + id + "/pages/reorder", { method: "POST", body: { block_ids: ids } }),

    process: (id, body) => request("/api/documents/" + id + "/process", { method: "POST", body: body || {} }),
    verify: (id, body) => request("/api/documents/" + id + "/verify", { method: "POST", body: body || {} }),

    updateBlock: (id, blockId, patch) =>
      request("/api/documents/" + id + "/blocks/" + blockId, { method: "PATCH", body: patch }),
    reorderBlocks: (id, ids) => request("/api/documents/" + id + "/blocks/reorder", { method: "POST", body: { block_ids: ids } }),
    review: (id, blockId, body) =>
      request("/api/documents/" + id + "/blocks/" + blockId + "/review", { method: "POST", body: body }),

    exportDoc: async (id, fmt) => {
      const res = await fetch("/api/documents/" + id + "/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fmt: fmt }),
      });
      if (!res.ok) {
        let d = res.statusText;
        try { d = (await res.json()).detail || d; } catch (_) {}
        throw new Error(d);
      }
      const blob = await res.blob();
      const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") || "");
      const file = name ? name[1] : "exam-paper." + (fmt === "docx" ? "docx" : fmt);
      return { blob: blob, filename: file };
    },
    preview: (id) => request("/api/documents/" + id + "/export/preview"),
  };

  global.PranenaAPI = api;
})(window);
