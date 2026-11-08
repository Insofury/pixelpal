// PixelPal browser engine (GitHub Pages version), running in a Web Worker so the page
// stays responsive while images are processed.
//
//  * Pyodide (Python compiled to WebAssembly) runs the same Python pipeline as the
//    server: app/pipeline.py with Pillow and NumPy.
//  * onnxruntime-web runs the U²-Net (u2netp) background-removal model.
//
// Messages in:  { id, type: "init", cfg } | { id, type: "model" } | { id, type: "process", name, buffer, options }
// Messages out: { id, ok, value | error }  and  { type: "status", kind, message }

let cfg = null;
let py = null;
let api = null;
let session = null;
let ready = null;
let modelReady = null;
let counter = 0;

const READY_TEXT = "Ready - your images never leave this device.";
const status = (kind, message) => postMessage({ type: "status", kind, message });

async function init() {
  status("loading", "Loading the Python engine (first visit only)…");
  importScripts(`${cfg.pyodide_base}pyodide.js`);
  py = await self.loadPyodide({ indexURL: cfg.pyodide_base });

  status("loading", "Loading Pillow and NumPy…");
  await py.loadPackage(cfg.packages);

  status("loading", "Loading PixelPal…");
  py.FS.mkdirTree("/home/pyodide/app");
  await Promise.all(
    cfg.modules.map(async (name) => {
      const res = await fetch(`${cfg.site_base}py/app/${name}`);
      if (!res.ok) throw new Error(`Missing module ${name}`);
      py.FS.writeFile(`/home/pyodide/app/${name}`, await res.text());
    }),
  );
  py.runPython("import sys\nsys.path.insert(0, '/home/pyodide')");
  api = py.pyimport("app.browser");
  status("ready", READY_TEXT);
}

const MODEL_INPUT = [1, 3, 320, 320];
let modelBytes = null;
let backend = "cpu";

async function hasWebGPU() {
  try {
    return Boolean(self.navigator.gpu && (await self.navigator.gpu.requestAdapter()));
  } catch {
    return false;
  }
}

async function createSession(providers) {
  return self.ort.InferenceSession.create(modelBytes, { executionProviders: providers, graphOptimizationLevel: "all" });
}

async function loadModel() {
  status("loading", `Loading the background-removal model (${(cfg.model.bytes / 1048576).toFixed(1)} MB, first time only)…`);
  // Use the GPU when the browser offers WebGPU (much faster); otherwise WebAssembly on the CPU.
  const gpu = await hasWebGPU();
  if (!self.ort) importScripts(gpu ? cfg.ort_webgpu_url : cfg.ort_url);
  self.ort.env.wasm.wasmPaths = cfg.ort_base;
  // Multi-threading needs cross-origin isolation, which GitHub Pages doesn't provide.
  self.ort.env.wasm.numThreads = self.crossOriginIsolated ? Math.min(4, navigator.hardwareConcurrency || 2) : 1;
  const res = await fetch(`${cfg.site_base}${cfg.model.path}`);
  if (!res.ok) throw new Error("Couldn't download the background-removal model.");
  modelBytes = new Uint8Array(await res.arrayBuffer());

  if (gpu) {
    try {
      session = await createSession(["webgpu"]);
      backend = "gpu";
      status("loading", "Preparing the GPU…");
      await session.run({ [session.inputNames[0]]: new self.ort.Tensor("float32", new Float32Array(3 * 320 * 320), MODEL_INPUT) });
    } catch {
      session = null; // fall back to the CPU below
    }
  }
  if (!session) {
    session = await createSession(["wasm"]);
    backend = "cpu";
  }
  status("ready", `Ready - background removal runs on your ${backend === "gpu" ? "GPU" : "CPU"}. Images never leave this device.`);
}

function ensureReady() {
  if (!ready) ready = init().catch((err) => ((ready = null), Promise.reject(err)));
  return ready;
}

function ensureModel() {
  if (!modelReady) modelReady = loadModel().catch((err) => ((modelReady = null), Promise.reject(err)));
  return modelReady;
}

function removeFile(path) {
  try {
    py.FS.unlink(path);
  } catch {
    /* already gone */
  }
}

function check(json) {
  const out = JSON.parse(json);
  if (!out.ok) throw new Error(out.error);
  return out;
}

async function runModel(tensorPath) {
  const bytes = py.FS.readFile(tensorPath);
  py.FS.unlink(tensorPath);
  const input = new Float32Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
  const feeds = () => ({ [session.inputNames[0]]: new self.ort.Tensor("float32", input, MODEL_INPUT) });
  let outputs;
  try {
    outputs = await session.run(feeds());
  } catch (err) {
    if (backend !== "gpu") throw err;
    session = await createSession(["wasm"]); // e.g. the GPU device was lost: retry on the CPU
    backend = "cpu";
    outputs = await session.run(feeds());
  }
  const prediction = outputs[session.outputNames[0]].data; // 1×1×320×320 floats
  return new Uint8Array(prediction.buffer, prediction.byteOffset, prediction.byteLength);
}

async function processImage({ name, buffer, options }) {
  await ensureReady();
  const n = ++counter;
  const src = `/tmp/src-${n}`;
  const out = `/tmp/out-${n}`;
  const maxPixels = cfg.max_megapixels * 1_000_000;
  py.FS.writeFile(src, new Uint8Array(buffer));

  let maskPath = null;
  if (options.remove_background) {
    try {
      await ensureModel();
      const tensorPath = `/tmp/in-${n}`;
      check(api.prepare_background(src, tensorPath, maxPixels));
      maskPath = `/tmp/mask-${n}`;
      py.FS.writeFile(maskPath, await runModel(tensorPath));
    } catch (err) {
      for (const path of [src, `/tmp/in-${n}`, `/tmp/mask-${n}`]) removeFile(path);
      throw err;
    }
  }

  const { result } = check(api.process_file(src, name, JSON.stringify(options), out, maskPath, maxPixels));
  const data = py.FS.readFile(out);
  py.FS.unlink(out);
  return { info: result, data };
}

async function handle(msg) {
  const { id, type } = msg;
  try {
    let value = null;
    let transfer = [];
    if (type === "init") {
      cfg = msg.cfg;
      await ensureReady();
    } else if (type === "model") {
      await ensureReady();
      await ensureModel();
    } else if (type === "process") {
      value = await processImage(msg);
      transfer = [value.data.buffer];
    } else {
      throw new Error(`Unknown request ${type}`);
    }
    postMessage({ id, ok: true, value }, transfer);
  } catch (err) {
    postMessage({ id, ok: false, error: (err && err.message) || String(err) });
  }
}

// One job at a time, in order.
let queue = Promise.resolve();
self.onmessage = (event) => {
  queue = queue.then(() => handle(event.data));
};
