"""The processing pipeline shared by the API and the browser version.

    decode -> (remove background) -> (resize) -> (watermark) -> encode

`process()` takes the uploaded bytes and returns the finished file plus the numbers
the UI shows (sizes, dimensions, savings) and any notes for the user.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from PIL import Image

from .background import apply_mask
from .imaging import (
    DEFAULT_MAX_PIXELS,
    OUTPUT_FORMATS,
    ImageError,
    Loaded,
    choose_format,
    encode,
    exif_bytes,
    has_alpha,
    load_image,
    resize,
)
from .options import Options
from .watermark import add_watermark

# Given the decoded photo, return its subject mask (mode "L", same size).
MaskFn = Callable[[Image.Image], Image.Image]

UNSAFE_NAME_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]+')


@dataclass
class Result:
    data: bytes
    filename: str
    format: str
    mime: str
    width: int
    height: int
    quality: int | None
    original: dict
    notes: list[str] = field(default_factory=list)

    def info(self) -> dict:
        """Everything except the bytes - sent to the UI as JSON."""
        before = self.original["bytes"]
        return {
            "filename": self.filename,
            "format": self.format,
            "mime": self.mime,
            "width": self.width,
            "height": self.height,
            "bytes": len(self.data),
            "quality": self.quality,
            "saved_percent": round((1 - len(self.data) / before) * 100, 1) if before else 0.0,
            "original": self.original,
            "notes": self.notes,
        }


def output_name(filename: str, suffix: str, ext: str) -> str:
    """'holiday photo.HEIC.png' + '-web' + 'webp' -> 'holiday photo.HEIC-web.webp'."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name.strip(".") else name
    stem = UNSAFE_NAME_CHARS.sub("_", stem).strip(" ._")[:80] or "image"
    return f"{stem}{suffix}.{ext}"


def process(
    data: bytes,
    filename: str,
    options: Options,
    *,
    mask_fn: MaskFn | None = None,
    max_pixels: int = DEFAULT_MAX_PIXELS,
) -> Result:
    return process_loaded(load_image(data, max_pixels), filename, options, mask_fn=mask_fn, max_pixels=max_pixels)


def process_loaded(
    src: Loaded,
    filename: str,
    options: Options,
    *,
    mask_fn: MaskFn | None = None,
    max_pixels: int = DEFAULT_MAX_PIXELS,
) -> Result:
    img = src.image
    notes: list[str] = []

    if options.remove_background:
        if mask_fn is None:
            raise ImageError("Background removal isn't available here.", 503)
        img = apply_mask(img, mask_fn(img), options.background)

    img = resize(img, options, max_pixels)

    if options.watermark.enabled:
        transparent = has_alpha(img)
        img = add_watermark(img, options.watermark)
        if not transparent:
            img = img.convert("RGB")  # don't make opaque photos carry an alpha channel

    fmt, format_notes = choose_format(options, src.format, img)
    notes += format_notes
    exif = exif_bytes(src.exif) if options.keep_metadata else None
    data, quality, encode_notes = encode(img, fmt, options, icc_profile=src.icc_profile, exif=exif)
    notes += encode_notes

    _, mime, ext = OUTPUT_FORMATS[fmt]
    return Result(
        data=data,
        filename=output_name(filename, options.suffix, ext),
        format=fmt,
        mime=mime,
        width=img.width,
        height=img.height,
        quality=quality,
        original={"width": src.width, "height": src.height, "bytes": src.bytes, "format": src.format},
        notes=notes,
    )
