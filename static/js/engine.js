// The image "engine" behind the UI. Two interchangeable implementations:
//
//  ServerEngine  - the FastAPI version: uploads each image to /api/process.
//  BrowserEngine - the GitHub Pages version: a Web Worker (worker.js) runs the same
//                  Python pipeline with Pyodide, plus the background-removal model
//                  with onnxruntime-web. Images never leave the visitor's device.
//
// createEngine() picks one: the Pages build ships a browser-mode.json file, the
// server does not.

async function apiError(res) {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join("; ");
  } catch {
    /* not JSON */
  }
  return `Something went wrong (HTTP ${res.status}).`;
}

class ServerEngine {
  constructor() {
    this.mode = "server";
    this.onStatus = () => {};
  }

  async config() {
    const res = await fetch("api/config");
    if (!res.ok) throw new Error(await apiError(res));
    return res.json();
  }

  warmUp() {
    return Promise.resolve();
  }

  loadModel() {
    return Promise.resolve();
  }

  async process(file, options) {
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("options", JSON.stringify(options));
    const res = await fetch("api/process", { method: "POST", body: form });
    if (!res.ok) throw new Error(await apiError(res));
    const info = JSON.parse(res.headers.get("X-PixelPal-Info") || "{}");
    return { info, blob: await res.blob() };
  }
}

class BrowserEngine {
  constructor(cfg) {
    this.mode = "browser";
    this.cfg = { ...cfg, site_base: new URL(".", window.location.href).href };
    this.onStatus = () => {};
    this.worker = null;
    this.calls = new Map();
    this.seq = 0;
    this.ready = null;
    this.modelReady = null;
  }

  async config() {
    return {
      version: this.cfg.version,
      mode: "browser",
      background_removal: true,
      bg_model: this.cfg.model.name,
      max_upload_mb: this.cfg.max_upload_mb,
      max_megapixels: this.cfg.max_megapixels,
    };
  }

  _worker() {
    if (this.worker) return this.worker;
    this.worker = new Worker(new URL(`./worker.js?v=${this.cfg.version}`, import.meta.url));
    this.worker.onmessage = (event) => {
      const msg = event.data;
      if (msg.type === "status") return this.onStatus(msg.kind, msg.message);
      const call = this.calls.get(msg.id);
      if (!call) return;
      this.calls.delete(msg.id);
      if (msg.ok) call.resolve(msg.value);
      else call.reject(new Error(msg.error));
    };
    this.worker.onerror = (event) => {
      event.preventDefault();
      const error = new Error("The image engine stopped unexpectedly. Please reload the page.");
      for (const call of this.calls.values()) call.reject(error);
      this.calls.clear();
      this.worker.terminate();
      this.worker = null;
      this.ready = this.modelReady = null;
      this.onStatus("error", error.message);
    };
    return this.worker;
  }

  _call(type, payload = {}, transfer = []) {
    const worker = this._worker();
    return new Promise((resolve, reject) => {
      const id = ++this.seq;
      this.calls.set(id, { resolve, reject });
      worker.postMessage({ id, type, ...payload }, transfer);
    });
  }

  /** Start loading Python now (cached by the browser after the first visit). */
  warmUp() {
    if (!this.ready) {
      this.ready = this._call("init", { cfg: this.cfg }).catch((err) => {
        this.ready = null;
        this.onStatus("error", "Couldn't load the Python engine - check your connection and reload the page.");
        throw err;
      });
    }
    return this.ready;
  }

  loadModel() {
    if (!this.modelReady) {
      this.modelReady = this.warmUp()
        .then(() => this._call("model"))
        .catch((err) => {
          this.modelReady = null;
          this.onStatus("error", "Couldn't load the background-removal model - check your connection and try again.");
          throw err;
        });
    }
    return this.modelReady;
  }

  async process(file, options) {
    await this.warmUp();
    if (options.remove_background) await this.loadModel();
    const buffer = await file.arrayBuffer();
    const { info, data } = await this._call("process", { name: file.name, buffer, options }, [buffer]);
    return { info, blob: new Blob([data], { type: info.mime }) };
  }
}

export async function createEngine() {
  try {
    const res = await fetch("browser-mode.json", { cache: "no-cache" });
    if (res.ok) {
      const cfg = await res.json();
      if (cfg.mode === "browser") return new BrowserEngine(cfg);
    }
  } catch {
    /* fall through to the server engine */
  }
  return new ServerEngine();
}
