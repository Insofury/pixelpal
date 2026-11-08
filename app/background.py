"""Background removal with the U²-Net family of models.

How it works: the model looks at a 320×320 copy of the photo and predicts, for every
pixel, how likely it is to belong to the main subject. That prediction is scaled back
to the photo's size and used as the alpha (transparency) channel.

* Server: `RembgRemover` lets the rembg library download the model and run it with
  onnxruntime (any rembg model can be used, e.g. u2netp, u2net or isnet-general-use).
* Browser: JavaScript runs the same u2netp model with onnxruntime-web; this module
  prepares the input (`prepare_input`) and turns the output into a mask
  (`mask_from_output`), mirroring what rembg does for u2netp.
"""

from __future__ import annotations

import importlib.util
import logging
import threading

import numpy as np
from PIL import Image, ImageChops

from .imaging import flatten

MODEL_SIZE = 320
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def prepare_input(img: Image.Image) -> np.ndarray:
    """Photo -> model input: a float32 tensor of shape (1, 3, 320, 320)."""
    small = img.convert("RGB").resize((MODEL_SIZE, MODEL_SIZE), Image.Resampling.LANCZOS, reducing_gap=3.0)
    arr = np.asarray(small, dtype=np.float32)
    arr = arr / max(float(arr.max()), 1e-6)
    arr = (arr - MEAN) / STD
    return np.ascontiguousarray(arr.transpose(2, 0, 1)[np.newaxis], dtype=np.float32)


def mask_from_output(prediction: np.ndarray, size: tuple[int, int]) -> Image.Image:
    """Model output (1×1×320×320 floats) -> an 8-bit mask the size of the photo."""
    pred = np.asarray(prediction, dtype=np.float32).reshape(MODEL_SIZE, MODEL_SIZE)
    lo, hi = float(pred.min()), float(pred.max())
    pred = (pred - lo) / max(hi - lo, 1e-6)
    mask = Image.fromarray(np.clip(pred * 255, 0, 255).astype(np.uint8), mode="L")
    return mask.resize(size, Image.Resampling.LANCZOS)


def apply_mask(img: Image.Image, mask: Image.Image, background: str = "") -> Image.Image:
    """Use the mask as transparency. Keeps any transparency the image already had.

    With `background` ("#rrggbb") the cut-out is placed on that colour instead.
    """
    if mask.size != img.size:
        mask = mask.resize(img.size, Image.Resampling.LANCZOS)
    rgba = img.convert("RGBA")
    alpha = ImageChops.multiply(rgba.getchannel("A"), mask.convert("L"))
    rgba.putalpha(alpha)
    return flatten(rgba, background) if background else rgba


class RembgRemover:
    """Server-side background removal using rembg (loaded lazily on first use)."""

    def __init__(self, model: str = "u2netp"):
        self.model = model
        self._session = None
        self._lock = threading.Lock()

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("rembg") is not None

    def _get_session(self):
        with self._lock:  # the first call downloads the model (~4.5 MB for u2netp)
            if self._session is None:
                from rembg import new_session

                self._session = new_session(self.model)
            return self._session

    def warm_up(self) -> None:
        """Import rembg and load the model ahead of the first request (the first import
        compiles some numba code and can take a while on a cold machine)."""
        try:
            self._get_session()
        except Exception:  # noqa: BLE001 - a failed warm-up is retried on the first real request
            logging.getLogger("pixelpal").exception("Background model warm-up failed")

    def mask(self, img: Image.Image) -> Image.Image:
        """Return the subject mask (mode "L", same size as `img`)."""
        masks = self._get_session().predict(img.convert("RGB"))
        return masks[0].convert("L")
