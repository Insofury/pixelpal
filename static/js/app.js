// PixelPal front-end: picking files, options and presets, running the batch, results,
// before/after comparison and the ZIP download.

import { createEngine } from "./engine.js?v=1.0.0";
import { makeZip } from "./zip.js?v=1.0.0";

const $ = (id) => document.getElementById(id);
const form = $("form");
const MAX_FILES = 50;
const EXTENSIONS = /\.(png|jpe?g|webp|gif|bmp|tiff?)$/i;
const STORAGE_KEY = "pixelpal:options";

const state = {
  engine: null,
  config: { max_upload_mb: 15, background_removal: true },
  files: [], // { id, file, url }
  results: [], // { id, file, srcUrl, status, info, blob, url, error }
  busy: false,
  nextId: 1,
};
const engineReady = createEngine().then((engine) => (state.engine = engine));

// ------------------------------------------------------------------ helpers
function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 3200);
}

function showError(message) {
  $("formError").textContent = message;
  $("formError").hidden = !message;
}

function setEngineStatus(kind, message) {
  const el = $("engineStatus");
  el.hidden = !message;
  el.className = `engine-status ${kind}`;
  el.textContent = message;
}

const FORMAT_NAMES = { png: "PNG", jpeg: "JPG", webp: "WebP", gif: "GIF", bmp: "BMP", tiff: "TIFF" };
const ICON_X = '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';

// ------------------------------------------------------------------ theme
function currentTheme() {
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr) return attr;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
$("themeBtn").addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try {
    localStorage.setItem("pixelpal:theme", next);
  } catch {
    /* ignore */
  }
});

// ------------------------------------------------------------------ files
function addFiles(list) {
  if (state.busy) return;
  const maxBytes = state.config.max_upload_mb * 1024 * 1024;
  let skippedType = 0;
  let skippedSize = 0;
  let skippedCount = 0;
  for (const file of list) {
    if (!EXTENSIONS.test(file.name) && !/^image\/(png|jpeg|webp|gif|bmp|tiff)$/.test(file.type)) {
      skippedType++;
      continue;
    }
    if (file.size > maxBytes) {
      skippedSize++;
      continue;
    }
    if (state.files.some((f) => f.file.name === file.name && f.file.size === file.size && f.file.lastModified === file.lastModified)) continue;
    if (state.files.length >= MAX_FILES) {
      skippedCount++;
      continue;
    }
    state.files.push({ id: state.nextId++, file, url: URL.createObjectURL(file) });
  }
  const problems = [];
  if (skippedType) problems.push(`${skippedType} not a supported image`);
  if (skippedSize) problems.push(`${skippedSize} over ${state.config.max_upload_mb} MB`);
  if (skippedCount) problems.push(`${skippedCount} over the ${MAX_FILES}-image limit`);
  if (problems.length) toast(`Skipped: ${problems.join(", ")}.`);
  renderFiles();
}

function removeFile(id) {
  const index = state.files.findIndex((f) => f.id === id);
  if (index < 0) return;
  URL.revokeObjectURL(state.files[index].url);
  state.files.splice(index, 1);
  renderFiles();
}

function clearFiles() {
  state.files.forEach((f) => URL.revokeObjectURL(f.url));
  state.files = [];
  renderFiles();
}

function renderFiles() {
  const grid = $("fileGrid");
  grid.innerHTML = state.files
    .map((f) => {
      const ext = (f.file.name.split(".").pop() || "").slice(0, 4);
      return `<li class="file-item">
        <img class="file-thumb" src="${f.url}" alt="" loading="lazy" data-ext="${escapeHtml(ext)}">
        <div class="file-meta"><span class="file-name" title="${escapeHtml(f.file.name)}">${escapeHtml(f.file.name)}</span><span class="file-size">${formatBytes(f.file.size)}</span></div>
        <button type="button" class="file-remove" data-remove="${f.id}" aria-label="Remove ${escapeHtml(f.file.name)}" ${state.busy ? "disabled" : ""}>${ICON_X}</button>
      </li>`;
    })
    .join("");
  // Browsers can't show TIFF: show the extension instead of a broken image.
  grid.querySelectorAll("img.file-thumb").forEach((img) =>
    img.addEventListener("error", () => {
      const box = document.createElement("div");
      box.className = "file-thumb no-preview";
      box.textContent = img.dataset.ext;
      img.replaceWith(box);
    }),
  );

  const count = state.files.length;
  const total = state.files.reduce((sum, f) => sum + f.file.size, 0);
  $("filesSummary").hidden = !count;
  $("filesSummary").textContent = `${count} image${count === 1 ? "" : "s"} · ${formatBytes(total)}`;
  $("clearBtn").hidden = !count || state.busy;
  $("dropzone").classList.toggle("compact", count > 0);
  updateProcessButton();
}

$("fileInput").addEventListener("change", (e) => {
  addFiles(e.target.files);
  e.target.value = "";
});
$("fileGrid").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-remove]");
  if (btn) removeFile(Number(btn.dataset.remove));
});
$("clearBtn").addEventListener("click", clearFiles);

const dz = $("dropzone");
["dragenter", "dragover"].forEach((type) =>
  dz.addEventListener(type, (e) => {
    e.preventDefault();
    dz.classList.add("dragging");
  }),
);
["dragleave", "drop"].forEach((type) => dz.addEventListener(type, () => dz.classList.remove("dragging")));
dz.addEventListener("drop", (e) => {
  e.preventDefault();
  if (e.dataTransfer?.files?.length) addFiles(e.dataTransfer.files);
});
// Dropping anywhere on the page shouldn't navigate away from it.
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => {
  e.preventDefault();
  if (!dz.contains(e.target) && e.dataTransfer?.files?.length) addFiles(e.dataTransfer.files);
});
// Paste images (e.g. screenshots) with Ctrl+V.
document.addEventListener("paste", (e) => {
  if (e.target.closest("input, textarea")) return;
  const files = [...(e.clipboardData?.files || [])].map((file, i) =>
    file.name && file.name !== "image.png" ? file : new File([file], `pasted-${Date.now()}-${i + 1}.png`, { type: file.type }),
  );
  if (files.length) addFiles(files);
});

// ------------------------------------------------------------------ options
const PRESETS = {
  web: { resizeOn: true, resizeMode: "fit", width: 1920, height: 1920, format: "webp", quality: 80 },
  product: { bgOn: true, bgFill: "#ffffff", resizeOn: true, resizeMode: "fit", width: 2000, height: 2000, format: "jpeg", quality: 90 },
  profile: { bgOn: true, bgFill: "transparent", resizeOn: true, resizeMode: "cover", width: 800, height: 800, format: "png" },
  instagram: { resizeOn: true, resizeMode: "cover", width: 1080, height: 1080, format: "jpeg", quality: 90 },
  small: { resizeOn: true, resizeMode: "fit", width: 1600, height: 1600, format: "jpeg", quality: 85, targetKb: 200 },
};

function setField(name, value) {
  const el = form.elements[name];
  if (!el) return;
  if (el instanceof RadioNodeList) el.value = String(value);
  else if (el.type === "checkbox") el.checked = Boolean(value);
  else el.value = value;
}

function applyPreset(key) {
  resetOptions();
  Object.entries(PRESETS[key]).forEach(([name, value]) => setField(name, value));
  syncUI();
  document.querySelectorAll("[data-preset]").forEach((chip) => chip.classList.toggle("active", chip.dataset.preset === key));
  saveOptions();
  if (PRESETS[key].bgOn && !state.config.background_removal) toast("Background removal isn't available on this server.");
}

function resetOptions() {
  form.reset(); // back to the defaults written in index.html (picked files live in `state`)
}

function numberValue(id) {
  const value = Number($(id).value);
  return Number.isFinite(value) && value > 0 ? Math.round(value) : 0;
}

function readOptions() {
  const f = form.elements;
  const bgFill = f.bgFill.value;
  const resizeMode = f.resizeMode.value;
  return {
    remove_background: $("bgOn").checked,
    background: bgFill === "transparent" ? "" : bgFill === "custom" ? $("bgColor").value : bgFill,
    resize: $("resizeOn").checked ? resizeMode : "none",
    width: resizeMode === "percent" ? 0 : numberValue("width"),
    height: resizeMode === "percent" ? 0 : numberValue("height"),
    percent: Number($("percent").value) || 100,
    upscale: $("upscale").checked,
    format: f.format.value,
    quality: Number($("quality").value),
    target_kb: numberValue("targetKb"),
    png_colors: $("pngColors").checked,
    keep_metadata: $("keepMeta").checked,
    suffix: $("suffix").value.trim(),
    watermark: $("wmOn").checked
      ? {
          text: $("wmText").value.trim(),
          position: $("wmTile").checked ? "tile" : f.wmPos.value,
          opacity: Number($("wmOpacity").value) / 100,
          size: Number($("wmSize").value),
          color: $("wmColor").value,
        }
      : { text: "" },
  };
}

function validate(options) {
  if (!state.files.length) return "Add at least one image first.";
  if (options.remove_background && !state.config.background_removal) return "Background removal isn't available on this server - turn it off to continue.";
  if (options.resize === "fit" && !options.width && !options.height) return "Enter a width or a height to resize to.";
  if (options.resize === "cover" && !(options.width && options.height)) return "Enter both a width and a height to crop to.";
  if (options.resize === "percent" && !(options.percent >= 1 && options.percent <= 1000)) return "Scale must be between 1% and 1000%.";
  if ($("wmOn").checked && !options.watermark.text) return "Type the watermark text (or turn the watermark off).";
  return "";
}

/** Show/hide the parts of the form that depend on other choices. */
function syncUI() {
  const f = form.elements;
  const tools = [
    ["bgOn", "bgBody", "bgTool"],
    ["resizeOn", "resizeBody", "resizeTool"],
    ["wmOn", "wmBody", "wmTool"],
  ];
  for (const [toggle, body, tool] of tools) {
    $(body).hidden = !$(toggle).checked;
    $(tool).classList.toggle("on", $(toggle).checked);
  }
  $("bgColor").hidden = f.bgFill.value !== "custom";

  const mode = f.resizeMode.value;
  $("dimsRow").hidden = mode === "percent";
  $("percentRow").hidden = mode !== "percent";
  $("upscaleRow").hidden = mode !== "fit";
  $("resizeHint").textContent =
    mode === "fit"
      ? "Keeps the shape. Leave width or height empty to set just one side."
      : mode === "cover"
        ? "Fills the exact size and trims the edges that don't fit (centred)."
        : "Scales width and height by the same percentage.";

  $("posGrid").classList.toggle("disabled", $("wmTile").checked);
  $("wmOpacityOut").textContent = `${$("wmOpacity").value}%`;
  $("wmSizeOut").textContent = `${$("wmSize").value}%`;

  const format = f.format.value;
  const lossy = format === "jpeg" || format === "webp" || format === "original";
  $("qualityRow").hidden = !lossy;
  $("qualityOut").textContent = $("quality").value;
  $("pngColorsRow").hidden = !(format === "png" || format === "original");
  $("formatHint").textContent = {
    original: "Keeps PNG, JPG and WebP as they are; GIF, BMP and TIFF become PNG.",
    jpeg: "Smallest for photos. No transparency (it's filled with white).",
    png: "Lossless and supports transparency. Best for logos, screenshots and cut-outs.",
    webp: "Modern format: smaller than JPG and supports transparency.",
  }[format];
  updateProcessButton();
}

function updateProcessButton() {
  const btn = $("processBtn");
  const count = state.files.length;
  btn.disabled = state.busy || !count;
  if (!state.busy) btn.querySelector(".btn-label").textContent = count ? `Process ${count} image${count === 1 ? "" : "s"}` : "Add images to start";
}

function saveOptions() {
  try {
    const values = {};
    for (const el of form.elements) {
      if (!el.name || el.type === "file") continue;
      if (el.type === "radio") {
        if (el.checked) values[el.name] = el.value;
      } else values[el.name] = el.type === "checkbox" ? el.checked : el.value;
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(values));
  } catch {
    /* storage unavailable */
  }
}

function restoreOptions() {
  try {
    const values = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (values) Object.entries(values).forEach(([name, value]) => setField(name, value));
  } catch {
    /* ignore */
  }
}

form.addEventListener("input", (e) => {
  if (e.target.type === "file") return;
  document.querySelectorAll("[data-preset].active").forEach((chip) => chip.classList.remove("active"));
  syncUI();
  saveOptions();
});
document.querySelectorAll("[data-preset]").forEach((chip) => chip.addEventListener("click", () => applyPreset(chip.dataset.preset)));
$("resetBtn").addEventListener("click", () => {
  resetOptions();
  document.querySelectorAll("[data-preset].active").forEach((chip) => chip.classList.remove("active"));
  syncUI();
  saveOptions();
});
$("bgOn").addEventListener("change", () => {
  // In the browser version, start downloading the model as soon as it's wanted.
  if ($("bgOn").checked && state.engine?.mode === "browser") state.engine.loadModel().catch(() => {});
});

// ------------------------------------------------------------------ processing
function setBusy(busy) {
  state.busy = busy;
  const btn = $("processBtn");
  btn.classList.toggle("loading", busy);
  $("fileInput").disabled = busy;
  $("dropzone").classList.toggle("disabled", busy);
  renderFiles();
}

function setLabel(text) {
  $("processBtn").querySelector(".btn-label").textContent = text;
}

function setProgress(done, total) {
  $("progress").hidden = !total;
  $("progressBar").style.width = total ? `${Math.round((done / total) * 100)}%` : "0";
}

function clearResults() {
  state.results.forEach((r) => r.url && URL.revokeObjectURL(r.url));
  state.results = [];
  $("resultGrid").innerHTML = "";
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (state.busy) return;
  const options = readOptions();
  const problem = validate(options);
  showError(problem);
  if (problem) return;

  const engine = await engineReady;
  setBusy(true);
  clearResults();
  state.results = state.files.map((f) => ({ id: f.id, file: f.file, srcUrl: f.url, status: "queued" }));
  $("results").hidden = false;
  renderResults();
  $("zipBtn").disabled = true;
  setProgress(0, state.results.length);

  try {
    if (engine.mode === "browser") {
      setLabel("Starting Python…");
      await engine.warmUp();
      if (options.remove_background) {
        setLabel("Loading the AI model…");
        await engine.loadModel();
      }
    }
    for (let i = 0; i < state.results.length; i++) {
      const item = state.results[i];
      setLabel(`Processing ${i + 1} of ${state.results.length}…`);
      item.status = "working";
      renderResult(item);
      try {
        const { info, blob } = await engine.process(item.file, options);
        Object.assign(item, { status: "done", info, blob, url: URL.createObjectURL(blob) });
      } catch (err) {
        Object.assign(item, { status: "error", error: err.message || "Something went wrong." });
      }
      renderResult(item);
      setProgress(i + 1, state.results.length);
      renderSummary();
    }
  } catch (err) {
    showError(err.message || "Couldn't start processing.");
    state.results.filter((r) => r.status === "queued" || r.status === "working").forEach((r) => {
      Object.assign(r, { status: "error", error: "Not processed." });
      renderResult(r);
    });
  } finally {
    setBusy(false);
    setProgress(0, 0);
    renderSummary();
    $("zipBtn").disabled = !state.results.some((r) => r.status === "done");
    if (state.results.length) $("results").scrollIntoView({ behavior: "smooth", block: "start" });
  }
});

// ------------------------------------------------------------------ results
function resultHtml(item) {
  const name = escapeHtml(item.file.name);
  if (item.status === "queued" || item.status === "working") {
    const label = item.status === "working" ? '<span class="spinner"></span>Processing…' : "Waiting…";
    return `<button type="button" class="result-preview checker" disabled><span class="state">${label}</span></button>
      <div class="result-body"><p class="result-name" title="${name}">${name}</p><p class="result-meta">${formatBytes(item.file.size)}</p></div>`;
  }
  if (item.status === "error") {
    return `<button type="button" class="result-preview checker" disabled><span class="state">Failed</span></button>
      <div class="result-body"><p class="result-name" title="${name}">${name}</p><p class="result-error">${escapeHtml(item.error)}</p></div>`;
  }
  const { info } = item;
  const orig = info.original;
  const saved = info.saved_percent;
  const badge = saved >= 0.5 ? `<span class="badge good">−${Math.round(saved)}%</span>` : saved <= -0.5 ? `<span class="badge warn">+${Math.round(-saved)}%</span>` : "";
  const dims = orig.width === info.width && orig.height === info.height ? `${info.width}×${info.height}` : `${orig.width}×${orig.height} → ${info.width}×${info.height}`;
  const notes = info.notes.length ? `<ul class="notes">${info.notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("")}</ul>` : "";
  const out = escapeHtml(info.filename);
  return `<button type="button" class="result-preview checker" data-compare="${item.id}" aria-label="Compare ${out} with the original"><img src="${item.url}" alt="" loading="lazy"></button>
    <div class="result-body">
      <p class="result-name" title="${out}">${out}</p>
      <p class="result-meta">${dims} · ${FORMAT_NAMES[orig.format] || orig.format} → ${FORMAT_NAMES[info.format]}${info.quality ? ` (quality ${info.quality})` : ""}</p>
      <p class="result-size"><span>${formatBytes(orig.bytes)} → ${formatBytes(info.bytes)}</span>${badge}</p>
      ${notes}
      <div class="result-actions">
        <a class="btn primary sm" href="${item.url}" download="${out}">Download</a>
        <button type="button" class="btn ghost sm" data-compare="${item.id}">Compare</button>
      </div>
    </div>`;
}

function renderResult(item) {
  const el = document.querySelector(`[data-result="${item.id}"]`);
  if (el) el.innerHTML = resultHtml(item);
}

function renderResults() {
  $("resultGrid").innerHTML = state.results.map((item) => `<li class="result-card" data-result="${item.id}">${resultHtml(item)}</li>`).join("");
  renderSummary();
}

function renderSummary() {
  const done = state.results.filter((r) => r.status === "done");
  const failed = state.results.filter((r) => r.status === "error").length;
  const total = state.results.length;
  if (!total) return ($("resultsSummary").textContent = "");
  const before = done.reduce((sum, r) => sum + r.info.original.bytes, 0);
  const after = done.reduce((sum, r) => sum + r.info.bytes, 0);
  let text = `${done.length} of ${total} image${total === 1 ? "" : "s"} done`;
  if (failed) text += ` · ${failed} failed`;
  if (done.length) {
    const saved = before ? Math.round((1 - after / before) * 100) : 0;
    text += ` · ${formatBytes(before)} → ${formatBytes(after)}`;
    if (saved > 0) text += ` (${saved}% smaller)`;
  }
  $("resultsSummary").textContent = text;
}

$("resultGrid").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-compare]");
  if (btn) openCompare(Number(btn.dataset.compare));
});

$("zipBtn").addEventListener("click", async () => {
  const done = state.results.filter((r) => r.status === "done");
  if (!done.length) return;
  const btn = $("zipBtn");
  btn.disabled = true;
  try {
    const zip = await makeZip(done.map((r) => ({ name: r.info.filename, blob: r.blob })));
    const url = URL.createObjectURL(zip);
    const a = document.createElement("a");
    a.href = url;
    a.download = `pixelpal-${new Date().toISOString().slice(0, 10)}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    toast(`Downloaded ${done.length} image${done.length === 1 ? "" : "s"} as a ZIP`);
  } catch {
    toast("Couldn't build the ZIP - try downloading the images one by one.");
  } finally {
    btn.disabled = false;
  }
});

// ------------------------------------------------------------------ compare dialog
const dialog = $("compareDialog");

function setSplit(percent) {
  $("compareStage").style.setProperty("--split", `${percent}%`);
  $("compareRange").value = percent;
}

function openCompare(id) {
  const item = state.results.find((r) => r.id === id && r.status === "done");
  if (!item) return;
  $("compareTitle").textContent = item.info.filename;
  $("compareBefore").src = item.srcUrl;
  $("compareAfter").src = item.url;
  $("compareMeta").textContent = `Before: ${item.info.original.width}×${item.info.original.height}, ${formatBytes(item.info.original.bytes)} · After: ${item.info.width}×${item.info.height}, ${formatBytes(item.info.bytes)}`;
  setSplit(50);
  dialog.showModal();
}

$("compareRange").addEventListener("input", (e) => setSplit(Number(e.target.value)));
$("compareClose").addEventListener("click", () => dialog.close());
dialog.addEventListener("click", (e) => {
  if (e.target === dialog) dialog.close(); // click on the backdrop
});
const stage = $("compareStage");
function dragTo(clientX) {
  const rect = stage.getBoundingClientRect();
  setSplit(Math.round(Math.min(100, Math.max(0, ((clientX - rect.left) / rect.width) * 100))));
}
stage.addEventListener("pointerdown", (e) => {
  stage.setPointerCapture(e.pointerId);
  dragTo(e.clientX);
});
stage.addEventListener("pointermove", (e) => {
  if (stage.hasPointerCapture(e.pointerId)) dragTo(e.clientX);
});

// ------------------------------------------------------------------ init
(async function init() {
  restoreOptions();
  syncUI();
  renderFiles();
  const engine = await engineReady;
  try {
    state.config = { ...state.config, ...(await engine.config()) };
  } catch {
    /* keep the defaults */
  }
  $("maxMb").textContent = state.config.max_upload_mb;

  if (!state.config.background_removal) {
    $("bgOn").checked = false;
    $("bgOn").disabled = true;
    $("bgTool").classList.add("disabled");
    $("bgSub").textContent = "Not available on this server (rembg isn't installed)";
    syncUI();
  }

  if (engine.mode === "browser") {
    $("privacyText").textContent = "Everything runs in your browser - your images never leave your device.";
    engine.onStatus = setEngineStatus;
    engine.warmUp().catch(() => {}); // start downloading Python now so it's ready when needed
    if ($("bgOn").checked) engine.loadModel().catch(() => {});
  }
})();
