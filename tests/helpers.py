"""Small image factories for the tests (no fixture files needed)."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw


def make_image(
    size: tuple[int, int] = (400, 300),
    fmt: str = "JPEG",
    mode: str = "RGB",
    color=(90, 160, 220),
    **save_kwargs,
) -> bytes:
    """A background colour with a red ellipse 'subject' in the middle."""
    img = Image.new(mode, size, color if mode != "RGBA" else (*color[:3], 255))
    w, h = size
    fill = (220, 50, 50) if mode in ("RGB", "RGBA") else 128
    ImageDraw.Draw(img).ellipse((w * 0.3, h * 0.2, w * 0.7, h * 0.8), fill=fill)
    buf = io.BytesIO()
    img.save(buf, fmt, **save_kwargs)
    return buf.getvalue()


def open_bytes(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def centre_mask(img: Image.Image) -> Image.Image:
    """A fake 'AI' mask: keep the centre box, drop the edges."""
    mask = Image.new("L", img.size, 0)
    w, h = img.size
    ImageDraw.Draw(mask).rectangle((w * 0.25, h * 0.25, w * 0.75, h * 0.75), fill=255)
    return mask
