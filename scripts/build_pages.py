"""Build the static GitHub Pages version of PixelPal into ./_site

GitHub Pages can only host static files, so this build ships the front-end, the
pure-Python image modules and the background-removal model. In the browser, a Web
Worker loads Pyodide (Python compiled to WebAssembly) with Pillow and NumPy and runs
the exact same pipeline the server uses; onnxruntime-web runs the U²-Net model.

    python scripts/build_pages.py            # writes ./_site
    python -m http.server -d _site 8040      # preview at http://localhost:8040
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PYODIDE_VERSION = "0.26.4"  # Python 3.12, Pillow 10.2 and NumPy 1.26 in the browser
BROWSER_PACKAGES = ["Pillow", "numpy"]  # built into Pyodide's package index
ORT_VERSION = "1.19.2"  # onnxruntime-web
BROWSER_MODULES = [
    "__init__.py",
    "background.py",
    "browser.py",
    "imaging.py",
    "options.py",
    "pipeline.py",
    "watermark.py",
]
BROWSER_MAX_UPLOAD_MB = 15
BROWSER_MAX_MEGAPIXELS = 30  # lower than the server: browser memory is tighter

# U²-Netp (Apache-2.0, https://github.com/xuebinqin/U-2-Net), the same file rembg uses.
MODEL = {
    "name": "u2netp",
    "url": "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx",
    "sha256": "309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8",
    # After patch_model() - see below.
    "patched_sha256": "f311c376a87578199c3705c4d8682e90cf2319e2bac1ee5bc927d13a66de3b23",
}
MODEL_CACHE = ROOT / ".cache" / "u2netp.onnx"

# The model's 33 MaxPool layers use ceil_mode=1, which onnxruntime-web's WebGPU backend
# can't run. With a 320×320 input every pooled size is even (320, 160, ... 10), so
# floor and ceil give the same result: switching to ceil_mode=0 changes nothing except
# letting the model run on the GPU. The serialized attribute is
#   name="ceil_mode" (field 1), i=1 (field 3), type=INT (field 20)
# so we flip one byte per layer; the file size stays the same.
CEIL_MODE_ON = b"\n\tceil_mode\x18\x01\xa0\x01\x02"
CEIL_MODE_OFF = b"\n\tceil_mode\x18\x00\xa0\x01\x02"
MAXPOOL_LAYERS = 33


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def patch_model(data: bytes) -> bytes:
    if data.count(CEIL_MODE_ON) != MAXPOOL_LAYERS:
        raise SystemExit("Unexpected model layout - refusing to patch it.")
    patched = data.replace(CEIL_MODE_ON, CEIL_MODE_OFF)
    if hashlib.sha256(patched).hexdigest() != MODEL["patched_sha256"]:
        raise SystemExit("Patched model checksum mismatch.")
    return patched


def fetch_model() -> Path:
    """Download the model once (checked against its SHA-256) and cache it in .cache/."""
    candidates = [MODEL_CACHE, Path.home() / ".u2net" / "u2netp.onnx"]  # rembg's own cache
    for path in candidates:
        if path.is_file() and _sha256(path) == MODEL["sha256"]:
            return path
    MODEL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MODEL_CACHE.with_suffix(".part")
    print(f"Downloading {MODEL['url']} ...")
    with urllib.request.urlopen(MODEL["url"], timeout=120) as response, tmp.open("wb") as out:  # noqa: S310
        shutil.copyfileobj(response, out)
    if _sha256(tmp) != MODEL["sha256"]:
        tmp.unlink()
        raise SystemExit("Model checksum mismatch - refusing to publish it.")
    tmp.replace(MODEL_CACHE)
    return MODEL_CACHE


def prepared_model() -> bytes:
    """The verified original model, patched for WebGPU and verified again."""
    return patch_model(fetch_model().read_bytes())


def build(out: Path = ROOT / "_site", model_bytes: bytes | None = None) -> Path:
    from app import __version__

    if model_bytes is None:
        model_bytes = prepared_model()
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "static", out / "static")
    shutil.copy2(ROOT / "static" / "index.html", out / "index.html")
    shutil.copy2(ROOT / "static" / "favicon.svg", out / "favicon.ico")

    modules_dir = out / "py" / "app"
    modules_dir.mkdir(parents=True)
    for name in BROWSER_MODULES:
        shutil.copy2(ROOT / "app" / name, modules_dir / name)

    (out / "models").mkdir()
    model_out = out / "models" / "u2netp.onnx"
    model_out.write_bytes(model_bytes)

    ort_base = f"https://cdn.jsdelivr.net/npm/onnxruntime-web@{ORT_VERSION}/dist/"
    config = {
        "mode": "browser",
        "version": __version__,
        "pyodide_base": f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/",
        "packages": BROWSER_PACKAGES,
        "modules": BROWSER_MODULES,
        "ort_url": f"{ort_base}ort.min.js",  # CPU (WebAssembly) only
        "ort_webgpu_url": f"{ort_base}ort.webgpu.min.js",  # adds the GPU backend (larger download)
        "ort_base": ort_base,
        "model": {"name": MODEL["name"], "path": "models/u2netp.onnx", "bytes": model_out.stat().st_size},
        "max_upload_mb": BROWSER_MAX_UPLOAD_MB,
        "max_megapixels": BROWSER_MAX_MEGAPIXELS,
    }
    (out / "browser-mode.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")  # serve files as-is (no Jekyll processing)
    return out


if __name__ == "__main__":
    site = build()
    files = sorted(p.relative_to(site).as_posix() for p in site.rglob("*") if p.is_file())
    print(f"Built {len(files)} files into {site}")
