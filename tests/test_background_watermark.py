import importlib.util
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.background import MODEL_SIZE, RembgRemover, apply_mask, mask_from_output, prepare_input
from app.imaging import load_image
from app.options import Options, Watermark
from app.pipeline import process
from app.watermark import add_watermark
from tests.helpers import centre_mask, make_image, open_bytes


# ------------------------------------------------------------------ background removal
def test_prepare_input_shape_and_normalisation():
    tensor = prepare_input(Image.new("RGB", (640, 480), (255, 255, 255)))
    assert tensor.shape == (1, 3, MODEL_SIZE, MODEL_SIZE) and tensor.dtype == np.float32
    # white -> (1 - mean) / std for each channel
    assert np.allclose(tensor[0, :, 0, 0], [(1 - 0.485) / 0.229, (1 - 0.456) / 0.224, (1 - 0.406) / 0.225], atol=1e-4)


def test_mask_from_output_scales_to_photo():
    pred = np.zeros((1, 1, MODEL_SIZE, MODEL_SIZE), np.float32)
    pred[..., 100:220, 100:220] = 7.5  # raw model values are unbounded; they get min-max scaled
    mask = mask_from_output(pred, (640, 480))
    assert mask.size == (640, 480) and mask.mode == "L"
    assert mask.getpixel((320, 240)) == 255 and mask.getpixel((5, 5)) == 0


def test_apply_mask_keeps_existing_transparency():
    img = Image.new("RGBA", (10, 10), (255, 0, 0, 128))
    out = apply_mask(img, Image.new("L", (10, 10), 255))
    assert out.getpixel((0, 0))[3] == 128  # 128 * 255/255
    out = apply_mask(img, Image.new("L", (10, 10), 0))
    assert out.getpixel((0, 0))[3] == 0


def test_pipeline_with_a_mask_function():
    data = make_image((400, 300))
    result = process(data, "cat.jpg", Options.from_dict({"remove_background": True}), mask_fn=centre_mask)
    out = open_bytes(result.data)
    assert result.format == "png" and out.mode == "RGBA"  # JPG -> PNG to keep transparency
    assert out.getpixel((5, 5))[3] == 0 and out.getpixel((200, 150))[3] == 255
    assert any("PNG" in note for note in result.notes)

    white = process(data, "cat.jpg", Options.from_dict({"remove_background": True, "background": "#ffffff"}), mask_fn=centre_mask)
    assert white.format == "jpeg" and open_bytes(white.data).getpixel((5, 5)) == (255, 255, 255)


def test_background_removal_needs_a_model():
    from app.imaging import ImageError

    with pytest.raises(ImageError) as info:
        process(make_image(), "a.jpg", Options.from_dict({"remove_background": True}))
    assert info.value.status == 503


MODEL_FILE = next(
    (p for p in (Path.home() / ".rembg/models/u2netp/u2netp.onnx", Path.home() / ".u2net/u2netp.onnx") if p.is_file()),
    None,
)
needs_model = pytest.mark.skipif(
    importlib.util.find_spec("rembg") is None or MODEL_FILE is None,
    reason="rembg / the u2netp model isn't installed",
)


@needs_model
def test_browser_maths_matches_rembg():
    """The browser version runs the model itself (onnxruntime-web) with our own pre- and
    post-processing. Check that it produces the same mask as rembg on the server."""
    import onnxruntime as ort

    img = load_image(make_image((640, 480))).image
    session = ort.InferenceSession(str(MODEL_FILE), providers=["CPUExecutionProvider"])
    pred = session.run(None, {session.get_inputs()[0].name: prepare_input(img)})[0]
    ours = np.asarray(mask_from_output(pred, img.size), dtype=np.int16)
    theirs = np.asarray(RembgRemover("u2netp").mask(img), dtype=np.int16)
    diff = np.abs(ours - theirs)
    assert diff.mean() < 2 and np.percentile(diff, 99.9) < 40


@needs_model
def test_patched_model_gives_identical_output():
    """build_pages.py flips MaxPool ceil_mode so the model runs on WebGPU. It must not
    change the result for the 320×320 input."""
    import onnxruntime as ort

    from scripts.build_pages import patch_model

    original = MODEL_FILE.read_bytes()
    x = np.random.default_rng(0).standard_normal((1, 3, MODEL_SIZE, MODEL_SIZE)).astype(np.float32)
    a = ort.InferenceSession(original, providers=["CPUExecutionProvider"]).run(None, {"input.1": x})[0]
    b = ort.InferenceSession(patch_model(original), providers=["CPUExecutionProvider"]).run(None, {"input.1": x})[0]
    assert np.array_equal(a, b)


# ------------------------------------------------------------------ watermark
def _changed(a: Image.Image, b: Image.Image, box) -> bool:
    return np.abs(np.asarray(a.crop(box), np.int16) - np.asarray(b.crop(box), np.int16)).sum() > 0


def test_corner_watermark_only_touches_its_corner():
    img = Image.new("RGB", (600, 400), (40, 90, 160))
    out = add_watermark(img, Watermark(text="© PixelPal", position="bottom-right", opacity=1, size=8)).convert("RGB")
    assert _changed(img, out, (300, 300, 600, 400))
    assert not _changed(img, out, (0, 0, 300, 200))


def test_tiled_watermark_covers_the_image():
    img = Image.new("RGB", (600, 400), (40, 90, 160))
    out = add_watermark(img, Watermark(text="SAMPLE", position="tile", opacity=0.5, size=6)).convert("RGB")
    for box in ((0, 0, 300, 200), (300, 0, 600, 200), (0, 200, 300, 400), (300, 200, 600, 400)):
        assert _changed(img, out, box)


def test_long_text_is_shrunk_to_fit():
    img = Image.new("RGB", (120, 80), (0, 0, 0))
    out = add_watermark(img, Watermark(text="A very long watermark line indeed", position="center", size=30))
    assert out.size == img.size


def test_watermarked_photo_stays_opaque():
    result = process(make_image((300, 200), "PNG"), "a.png", Options.from_dict({"watermark": {"text": "hi"}}))
    assert open_bytes(result.data).mode == "RGB"
