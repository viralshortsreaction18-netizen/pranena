/**
 * Frontend smoke test.
 *
 * Loads the real index.html and the real app.js into jsdom, points it at a
 * running Pranena server, and asserts that the app boots, renders the page
 * list, renders the extracted blocks and can export a document.
 *
 *   node tests/frontend/smoke.mjs [baseUrl]
 */
import { JSDOM, VirtualConsole } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BASE = process.argv[2] || "http://127.0.0.1:8000";
const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", "..");

let failures = 0;
const checks = [];

function check(name, condition, detail = "") {
  checks.push({ name, ok: !!condition, detail });
  if (!condition) failures++;
  console.log(`${condition ? "  ok  " : " FAIL "} ${name}${detail ? " — " + detail : ""}`);
}

async function api(path, options = {}) {
  const res = await fetch(BASE + path, options);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function main() {
  console.log(`\nPranena frontend smoke test against ${BASE}\n`);

  /* ---------- prepare a document with content on the server ---------- */
  const doc = await api("/api/documents", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "Frontend smoke test" }),
  });

  // A 1x1 PNG is enough - the demo engine does not read pixels.
  const png = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
    "base64"
  );
  const form = new FormData();
  form.append("files", new Blob([png], { type: "image/png" }), "page1.png");
  await api(`/api/documents/${doc.id}/pages`, { method: "POST", body: form });

  const eng = await api("/api/engines");
  if (!eng.engines.find((e) => e.id === "demo" && e.enabled)) {
    throw new Error("demo engine is not enabled");
  }

  await api(`/api/documents/${doc.id}/process`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ language: "en" }),
  });
  for (let i = 0; i < 60; i++) {
    const d = await api(`/api/documents/${doc.id}`);
    if (d.document.status !== "running") break;
    await new Promise((r) => setTimeout(r, 200));
  }
  const serverDoc = await api(`/api/documents/${doc.id}`);
  check("server produced blocks", serverDoc.progress.blocks > 0,
    `${serverDoc.progress.blocks} blocks`);

  /* ---------- boot the UI in jsdom ---------- */
  const html = readFileSync(join(ROOT, "frontend", "index.html"), "utf8");
  const virtualConsole = new VirtualConsole();
  const consoleErrors = [];
  virtualConsole.on("jsdomError", (e) => consoleErrors.push(e.message));
  virtualConsole.on("error", (...args) => consoleErrors.push(args.join(" ")));

  const dom = new JSDOM(html, {
    url: BASE + "/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
    virtualConsole,
  });
  const { window } = dom;

  // Provide fetch + the small browser APIs the app touches.
  window.fetch = (input, init) =>
    fetch(typeof input === "string" && input.startsWith("/") ? BASE + input : input, init);
  window.URL.createObjectURL = () => "blob:mock";
  window.URL.revokeObjectURL = () => {};
  window.confirm = () => true;
  window.HTMLElement.prototype.scrollIntoView = () => {};
  window.open = () => null;

  // Load the two scripts exactly as the browser would.
  const apiJs = readFileSync(join(ROOT, "frontend", "js", "api.js"), "utf8");
  const appJs = readFileSync(join(ROOT, "frontend", "js", "app.js"), "utf8");
  window.eval(apiJs);
  check("api.js loaded", typeof window.PranenaAPI === "object");

  window.eval(appJs);
  check("app.js loaded", typeof window.PranenaAPI === "object");

  window.document.dispatchEvent(new window.Event("DOMContentLoaded", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 1500));

  const $ = (sel) => window.document.querySelector(sel);
  const $$ = (sel) => Array.from(window.document.querySelectorAll(sel));

  check("no uncaught JS errors", consoleErrors.length === 0,
    consoleErrors.slice(0, 3).join(" | "));

  check("document name populated", $("#doc-name").value.length > 0,
    $("#doc-name").value);

  /* ---------- page rail ---------- */
  const pageCards = $$(".page-card");
  check("page list rendered", pageCards.length >= 1, `${pageCards.length} card(s)`);
  check("page card shows a status pill", $$(".page-card .pill").length >= 1);

  /* ---------- transcription list ---------- */
  const blocks = $$("#block-list .block");
  check("blocks rendered", blocks.length > 0, `${blocks.length} blocks`);
  check("block text is editable",
    $$("#block-list .block-text[contenteditable='true']").length === blocks.length);
  check("confidence bars rendered", $$("#block-list .block-conf").length === blocks.length);
  check("block type selectors rendered",
    $$("#block-list select.block-type").length === blocks.length);

  const firstText = $("#block-list .block-text")?.textContent || "";
  check("block text is non-empty", firstText.trim().length > 0, firstText.slice(0, 40));

  /* ---------- overlay ---------- */
  check("overlay rectangles drawn", $$("#overlay rect").length > 0,
    `${$$("#overlay rect").length} rects`);

  /* ---------- progress ---------- */
  check("progress bar present", $("#progress-bar") !== null);
  check("progress percentage rendered", /%$/.test($("#progress-pct").textContent),
    $("#progress-pct").textContent);

  /* ---------- tabs ---------- */
  const tabs = $$(".tab");
  check("three tabs present", tabs.length === 3, tabs.map((t) => t.textContent).join(", "));
  tabs.find((t) => t.dataset.tab === "preview").dispatchEvent(
    new window.Event("click", { bubbles: true })
  );
  await new Promise((r) => setTimeout(r, 300));
  check("preview tab renders a paper",
    $$(".preview .paper").length === 1 && $$(".preview .paper-q").length > 0,
    `${$$(".preview .paper-q").length} question(s)`);

  tabs.find((t) => t.dataset.tab === "paper").dispatchEvent(
    new window.Event("click", { bubbles: true })
  );
  await new Promise((r) => setTimeout(r, 200));
  check("paper details form is populated from the document settings",
    $("#paper-form [data-field='font_latin']").value.length > 0,
    $("#paper-form [data-field='font_latin']").value);

  /* ---------- filters ---------- */
  tabs.find((t) => t.dataset.tab === "text").dispatchEvent(
    new window.Event("click", { bubbles: true })
  );
  const search = $("#search");
  search.value = "zzzznotfound";
  search.dispatchEvent(new window.Event("input", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 150));
  check("search filters the block list", $$("#block-list .block").length === 0);
  search.value = "";
  search.dispatchEvent(new window.Event("input", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 150));
  check("clearing the search restores the list", $$("#block-list .block").length > 0);

  /* ---------- settings modal ---------- */
  $("#btn-settings").dispatchEvent(new window.Event("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 800));
  check("settings modal opens", $("#settings-modal").hidden === false);
  check("providers listed", $$("#provider-list .provider").length >= 5,
    `${$$("#provider-list .provider").length} providers`);
  check("engines listed", $$("#engine-list .engine-row").length >= 1);
  $("#settings-close").dispatchEvent(new window.Event("click", { bubbles: true }));
  check("settings modal closes", $("#settings-modal").hidden === true);

  /* ---------- export ---------- */
  const exported = await fetch(BASE + `/api/documents/${doc.id}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fmt: "docx" }),
  });
  const buffer = Buffer.from(await exported.arrayBuffer());
  check("docx export works from the API", exported.ok && buffer.length > 5000,
    `${buffer.length} bytes`);
  check("docx is a valid zip", buffer.subarray(0, 2).toString() === "PK");

  /* ---------- cleanup ---------- */
  await fetch(BASE + `/api/documents/${doc.id}`, { method: "DELETE" });

  console.log(`\n${checks.length - failures}/${checks.length} checks passed\n`);
  process.exit(failures ? 1 : 0);
}

main().catch((err) => {
  console.error("\nsmoke test crashed:", err);
  process.exit(1);
});
