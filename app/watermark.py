"""Text watermarks: in a corner/edge/centre, or tiled diagonally across the whole image."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from .imaging import hex_to_rgb
from .options import Watermark

TILE_ANGLE = 30


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont | None:
    """Pillow's built-in scalable font (no font file to ship). None without FreeType."""
    try:
        return ImageFont.load_default(size=size)
    except (TypeError, ImportError, OSError, AttributeError):
        return None


def _draw_text(text: str, font, fill, stroke_fill, stroke: int) -> Image.Image:
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), text, font=font, stroke_width=stroke)
    tile = Image.new("RGBA", (right - left + 2, bottom - top + 2), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text(
        (1 - left, 1 - top), text, font=font, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill
    )
    return tile


def render_text(text: str, size: int, fill: tuple, stroke_fill: tuple) -> Image.Image:
    """Draw the text on a transparent tile, with a thin contrasting outline so it stays
    readable on both light and dark photos."""
    font = _font(size)
    if font is None:  # Pillow built without FreeType: draw small, then scale up
        tile = _draw_text(text, ImageFont.load_default(), fill, stroke_fill, 1)
        scale = size / max(1, tile.height)
        return tile.resize((max(1, round(tile.width * scale)), max(1, size)), Image.Resampling.BICUBIC)
    return _draw_text(text, font, fill, stroke_fill, max(1, round(size / 18)))


def _luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (c / 255 for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def add_watermark(img: Image.Image, wm: Watermark) -> Image.Image:
    base = img.convert("RGBA")
    width, height = base.size
    short_side = min(width, height)
    size = max(8, round(short_side * wm.size / 100))
    rgb = hex_to_rgb(wm.color)
    alpha = round(255 * wm.opacity)
    outline = (0, 0, 0) if _luminance(rgb) > 0.5 else (255, 255, 255)
    tile = render_text(wm.text.strip(), size, (*rgb, alpha), (*outline, round(alpha * 0.45)))

    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    if wm.position == "tile":
        tile = tile.rotate(TILE_ANGLE, expand=True, resample=Image.Resampling.BICUBIC)
        step_x, step_y = tile.width + size * 2, tile.height + size
        for row, y in enumerate(range(-tile.height // 2, height, step_y)):
            shift = (step_x // 2) * (row % 2)  # offset every other row (brick pattern)
            for x in range(-tile.width // 2 - shift, width, step_x):
                layer.paste(tile, (x, y))  # tiles never overlap, so a plain paste is exact
    else:
        margin = round(short_side * 0.03)
        max_width = width - 2 * margin
        if tile.width > max_width > 0:  # long text on a small image: shrink to fit
            scale = max_width / tile.width
            tile = tile.resize((max_width, max(1, round(tile.height * scale))), Image.Resampling.LANCZOS)
        pos = wm.position
        horizontal = "left" if pos.endswith("left") else "right" if pos.endswith("right") else "center"
        vertical = "top" if pos.startswith("top") else "bottom" if pos.startswith("bottom") else "center"
        x = {"left": margin, "center": (width - tile.width) // 2, "right": width - tile.width - margin}[horizontal]
        y = {"top": margin, "center": (height - tile.height) // 2, "bottom": height - tile.height - margin}[vertical]
        layer.paste(tile, (x, y))
    return Image.alpha_composite(base, layer)
