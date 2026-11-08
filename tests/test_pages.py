"""The GitHub Pages (browser / Pyodide) entry point and build."""

import json
import re

import numpy as np
import pytest

from app.background import MODEL_SIZE
from app.browser import prepare_background, process_file
from scripts.build_pages import BROWSER_MODULES, MAXPOOL_LAYERS, build, patch_model
from tests.helpers import make_image, open_bytes


def test_process_file(tmp_path):
    src, out = tmp_path / "src.bin", tmp_path / "out.bin"
    src.write_bytes(make_image((300, 200)))
    result = json.loads(process_file(str(src), "photo.jpg", '{"format": "png"}', str(out)))
    assert result["ok"] and result["result"]["filename"] == "photo.png"
    assert open_bytes(out.read_bytes()).size == (300, 200)
    assert not src.exists()  # the upload copy is deleted right away


def test_background_flow(tmp_path):
    src, tensor, mask, out = (tmp_path / n for n in ("src", "tensor", "mask", "out"))
    src.write_bytes(make_image((300, 200)))
    assert json.loads(prepare_background(str(src), str(tensor))) == {"ok": True}
    assert tensor.stat().st_size == 3 * MODEL_SIZE * MODEL_SIZE * 4  # float32

    # Pretend the model said "the middle is the subject" (what onnxruntime-web returns).
    pred = np.zeros((1, 1, MODEL_SIZE, MODEL_SIZE), np.float32)
    pred[..., 80:240, 80:240] = 1
    pred.tofile(mask)
    result = json.loads(process_file(str(src), "a.jpg", '{"remove_background": true}', str(out), str(mask)))
    img = open_bytes(out.read_bytes())
    assert result["ok"] and img.mode == "RGBA"
    assert img.getpixel((2, 2))[3] == 0 and img.getpixel((150, 100))[3] == 255
    assert not src.exists() and not mask.exists()


def test_errors_come_back_as_messages(tmp_path):
    src = tmp_path / "src"
    src.write_bytes(b"nope")
    assert json.loads(process_file(str(src), "a.jpg", "{}", str(tmp_path / "o"))) == {
        "ok": False,
        "error": "That file isn't an image PixelPal can read. Use PNG, JPG, WebP, GIF, BMP or TIFF.",
    }
    src.write_bytes(make_image())
    bad = json.loads(process_file(str(src), "a.jpg", '{"resize": "fit"}', str(tmp_path / "o")))
    assert bad == {"ok": False, "error": "Enter a width or a height to resize to."}


def test_build_output(tmp_path):
    site = build(tmp_path / "_site", model_bytes=b"fake model")
    for path in ("index.html", "static/js/worker.js", "static/js/zip.js", "static/css/styles.css", ".nojekyll", "models/u2netp.onnx"):
        assert (site / path).is_file(), path
    config = json.loads((site / "browser-mode.json").read_text())
    assert config["mode"] == "browser" and config["modules"] == BROWSER_MODULES
    assert config["packages"] == ["Pillow", "numpy"] and config["model"]["bytes"] == len(b"fake model")
    assert all((site / "py" / "app" / name).is_file() for name in BROWSER_MODULES)
    # Relative asset paths so the site works under /pixelpal/ on GitHub Pages.
    assert 'src="/static' not in (site / "index.html").read_text()


def test_patch_model_checks_the_layout():
    with pytest.raises(SystemExit, match="Unexpected model layout"):
        patch_model(b"no pooling layers here")
    fake = b"\n\tceil_mode\x18\x01\xa0\x01\x02" * MAXPOOL_LAYERS
    with pytest.raises(SystemExit, match="checksum"):  # right layout, wrong file
        patch_model(fake)


def test_browser_modules_only_use_what_pyodide_has():
    """Everything shipped to the browser must run in Pyodide: no FastAPI, no rembg."""
    allowed = {
        "__future__", "collections", "dataclasses", "importlib", "io", "json", "logging", "math", "os", "re",
        "threading", "typing", "PIL", "numpy",
    }
    for name in BROWSER_MODULES:
        source = open(f"app/{name}", encoding="utf-8").read()
        top_level = re.findall(r"^(?:from|import)\s+([\w.]+)", source, re.MULTILINE)
        third_party = {i.split(".")[0] for i in top_level if not i.startswith(".")} - allowed
        assert not third_party, f"{name} imports {third_party}"
