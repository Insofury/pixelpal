"""Entry point for the GitHub Pages build: JavaScript calls these functions via Pyodide.

Files move through Pyodide's in-memory file system, so nothing leaves the browser:

1. JS writes the picked image to /tmp/...
2. Background removal only: `prepare_background()` writes the model input; JS runs the
   U²-Net model with onnxruntime-web and writes the model output next to it.
3. `process_file()` runs the same pipeline as the server and writes the finished file.

Both functions return JSON: {"ok": true, ...} or {"ok": false, "error": "..."}.
"""

from __future__ import annotations

import json
import os

import numpy as np

from .background import mask_from_output, prepare_input
from .imaging import DEFAULT_MAX_PIXELS, ImageError, Loaded, load_image
from .options import Options, OptionsError
from .pipeline import process_loaded

# Decoded images kept between step 2 and step 3, so big photos are decoded only once.
_decoded: dict[str, Loaded] = {}


def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _remove(*paths: str | None) -> None:
    for path in paths:
        if path and os.path.exists(path):
            os.remove(path)


def _error(exc: BaseException) -> str:
    if isinstance(exc, (ImageError, OptionsError)):
        message = str(exc)
    elif isinstance(exc, MemoryError):
        message = "This image is too big to process in the browser. Try a smaller one."
    else:
        message = f"Something went wrong while processing this image ({type(exc).__name__})."
    return json.dumps({"ok": False, "error": message})


def prepare_background(src_path: str, tensor_path: str, max_pixels: int = DEFAULT_MAX_PIXELS) -> str:
    try:
        src = load_image(_read(src_path), max_pixels)
        _decoded[src_path] = src
        prepare_input(src.image).tofile(tensor_path)
        return json.dumps({"ok": True})
    except Exception as exc:  # noqa: BLE001 - every error goes back to the UI as a message
        _decoded.pop(src_path, None)
        _remove(src_path, tensor_path)
        return _error(exc)


def process_file(
    src_path: str,
    filename: str,
    options_json: str,
    out_path: str,
    mask_path: str | None = None,
    max_pixels: int = DEFAULT_MAX_PIXELS,
) -> str:
    try:
        options = Options.from_json(options_json)
        src = _decoded.pop(src_path, None) or load_image(_read(src_path), max_pixels)
        mask_fn = None
        if mask_path:
            prediction = np.fromfile(mask_path, dtype=np.float32)
            mask_fn = lambda img: mask_from_output(prediction, img.size)  # noqa: E731
        result = process_loaded(src, filename, options, mask_fn=mask_fn, max_pixels=max_pixels)
        with open(out_path, "wb") as fh:
            fh.write(result.data)
        return json.dumps({"ok": True, "result": result.info()})
    except Exception as exc:  # noqa: BLE001
        return _error(exc)
    finally:
        _decoded.pop(src_path, None)
        _remove(src_path, mask_path)
