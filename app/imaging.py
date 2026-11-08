"""Reading, resizing and writing images with Pillow.

Everything here works on bytes in memory and runs unchanged on the server and in the
browser (Pyodide), so both versions of PixelPal produce identical files.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field

from PIL import Image, ImageOps, UnidentifiedImageError

from .options import Options

# Pillow format name -> our short name. MPO is how Pillow reports many camera JPEGs.
INPUT_FORMATS = {
    "PNG": "png",
    "JPEG": "jpeg",
    "MPO": "jpeg",
    "WEBP": "webp",
    "GIF": "gif",
    "BMP": "bmp",
    "TIFF": "tiff",
}
# our name -> (Pillow format, MIME type, file extension)
OUTPUT_FORMATS = {
    "png": ("PNG", "image/png", "png"),
    "jpeg": ("JPEG", "image/jpeg", "jpg"),
    "webp": ("WEBP", "image/webp", "webp"),
}
SUPPORTED_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff")
DEFAULT_MAX_PIXELS = 40_000_000
PNG_OPTIMIZE_MAX_PIXELS = 2_500_000
MIN_QUALITY = 5
SUPPORTED_TEXT = "PNG, JPG, WebP, GIF, BMP or TIFF"

# We check the pixel count ourselves (with a friendlier message), so turn off
# Pillow's own decompression-bomb warning/exception.
Image.MAX_IMAGE_PIXELS = None


class ImageError(ValueError):
    """A problem with the user's image. `status` is the HTTP status the API returns."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Loaded:
    """A decoded image (already rotated upright) plus facts about the original file."""

    image: Image.Image
    format: str
    bytes: int
    icc_profile: bytes | None = None
    exif: Image.Exif | None = None
    width: int = field(init=False)
    height: int = field(init=False)

    def __post_init__(self) -> None:
        self.width, self.height = self.image.size


# ------------------------------------------------------------------ reading
def load_image(data: bytes, max_pixels: int = DEFAULT_MAX_PIXELS) -> Loaded:
    """Decode an uploaded file. Raises ImageError for anything unreadable or too big."""
    if not data:
        raise ImageError("The file is empty.")
    try:
        img = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise ImageError(f"That file isn't an image PixelPal can read. Use {SUPPORTED_TEXT}.") from exc

    fmt = INPUT_FORMATS.get(img.format or "")
    if fmt is None:
        raise ImageError(f"{img.format or 'This'} images aren't supported. Use {SUPPORTED_TEXT}.")
    width, height = img.size
    if width * height > max_pixels:
        raise ImageError(
            f"This image is {width}×{height} ({width * height / 1e6:.0f} MP). "
            f"The limit is {max_pixels / 1e6:.0f} megapixels.",
            413,
        )

    icc_profile = img.info.get("icc_profile")
    try:
        exif = img.getexif()
        img.load()  # animated GIF / WebP: the first frame
        img = ImageOps.exif_transpose(img) or img  # phone photos: apply the rotation flag
        img, kept_colours = _normalise_mode(img)
    except ImageError:
        raise
    except Exception as exc:  # truncated or corrupt data surfaces here
        raise ImageError("The image looks damaged or incomplete.") from exc
    if not kept_colours:
        icc_profile = None  # e.g. a CMYK profile no longer matches the converted RGB pixels
    return Loaded(image=img, format=fmt, bytes=len(data), icc_profile=icc_profile, exif=exif or None)


def _normalise_mode(img: Image.Image) -> tuple[Image.Image, bool]:
    """Convert to RGB or RGBA. Returns (image, whether the colour profile still applies)."""
    mode = img.mode
    if mode in ("RGB", "RGBA"):
        return img, True
    if mode == "P":
        return img.convert("RGBA" if "transparency" in img.info else "RGB"), True
    if mode in ("LA", "PA", "La", "RGBa"):
        return img.convert("RGBA"), False
    if mode in ("L", "1"):
        return img.convert("RGB"), False  # a grey ICC profile can't describe RGB pixels
    if mode.startswith("I") or mode == "F":  # 16/32-bit greyscale (scientific / medical PNG, TIFF)
        img = img.convert("I") if mode != "F" else img
        lo, hi = img.getextrema()
        scale = 255 / (hi - lo) if hi > lo else 1
        return img.point(lambda v: (v - lo) * scale).convert("L").convert("RGB"), False
    try:
        return img.convert("RGB"), False  # CMYK, YCbCr, ...
    except (ValueError, OSError) as exc:
        raise ImageError(f"Images in {mode} colour mode aren't supported.") from exc


def has_alpha(img: Image.Image) -> bool:
    """True when the image has at least one pixel that isn't fully opaque."""
    return img.mode == "RGBA" and img.getchannel("A").getextrema()[0] < 255


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def flatten(img: Image.Image, color: str = "#ffffff") -> Image.Image:
    """Put a (possibly transparent) image on a solid background."""
    if img.mode != "RGBA":
        return img.convert("RGB")
    background = Image.new("RGB", img.size, hex_to_rgb(color or "#ffffff"))
    background.paste(img, mask=img.getchannel("A"))
    return background


# ------------------------------------------------------------------ resizing
def target_size(size: tuple[int, int], options: Options) -> tuple[int, int]:
    """The output size for the 'fit' and 'percent' modes (cover is exact)."""
    width, height = size
    if options.resize == "percent":
        scale = options.percent / 100
    elif options.resize == "fit":
        sx = options.width / width if options.width else math.inf
        sy = options.height / height if options.height else math.inf
        scale = min(sx, sy)
        if not options.upscale:
            scale = min(scale, 1.0)
    elif options.resize == "cover":
        return options.width, options.height
    else:
        return size
    return max(1, round(width * scale)), max(1, round(height * scale))


def resize(img: Image.Image, options: Options, max_pixels: int = DEFAULT_MAX_PIXELS) -> Image.Image:
    if options.resize == "none":
        return img
    new_size = target_size(img.size, options)
    if new_size[0] * new_size[1] > max_pixels:
        raise ImageError(
            f"The resized image would be {new_size[0]}×{new_size[1]}, which is over the "
            f"{max_pixels / 1e6:.0f} megapixel limit.",
            413,
        )
    if options.resize == "cover":
        # Scale to cover the box, then crop the overflow equally from both sides.
        return ImageOps.fit(img, new_size, Image.Resampling.LANCZOS)
    if new_size == img.size:
        return img
    # LANCZOS gives the sharpest result; reducing_gap makes big downscales much faster.
    # Pillow premultiplies alpha while resizing, so transparent edges don't go dark.
    return img.resize(new_size, Image.Resampling.LANCZOS, reducing_gap=3.0)


# ------------------------------------------------------------------ writing
def choose_format(options: Options, source_format: str, img: Image.Image) -> tuple[str, list[str]]:
    """Pick the output format. "original" keeps PNG/JPG/WebP and turns the rest into PNG."""
    notes: list[str] = []
    if options.format != "original":
        return options.format, notes
    fmt = source_format if source_format in OUTPUT_FORMATS else "png"
    if fmt == "jpeg" and has_alpha(img):
        fmt = "png"
        notes.append("Saved as PNG to keep the transparent background (JPG can't store transparency).")
    return fmt, notes


def save(
    img: Image.Image,
    fmt: str,
    quality: int,
    *,
    png_colors: bool = False,
    icc_profile: bytes | None = None,
    exif: bytes | None = None,
) -> bytes:
    """Encode once with the given settings."""
    params: dict = {}
    if icc_profile:
        params["icc_profile"] = icc_profile
    if exif:
        params["exif"] = exif
    if fmt == "jpeg":
        img = img if img.mode == "RGB" else flatten(img)
        params.update(quality=quality, optimize=True, progressive=True)
    elif fmt == "webp":
        # method 2 is 2-3x faster than the default (4) for about the same file size.
        params.update(quality=quality, method=2)
    elif fmt == "png":
        # Full optimisation is slow on big images (seconds per photo in the browser) for a
        # few % saving, so large images use zlib level 6.
        if img.width * img.height <= PNG_OPTIMIZE_MAX_PIXELS:
            params.update(optimize=True)
        else:
            params.update(compress_level=6)
        if png_colors:
            # Lossy PNG: 256 colours (with alpha) usually cuts the size by 60-80%.
            img = img.quantize(256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.FLOYDSTEINBERG)
            params.pop("icc_profile", None)  # palette images and ICC profiles don't mix well
    else:
        raise ValueError(f"unknown output format {fmt!r}")
    buffer = io.BytesIO()
    img.save(buffer, OUTPUT_FORMATS[fmt][0], **params)
    return buffer.getvalue()


def encode(
    img: Image.Image,
    fmt: str,
    options: Options,
    *,
    icc_profile: bytes | None = None,
    exif: bytes | None = None,
) -> tuple[bytes, int | None, list[str]]:
    """Encode the final image. With a size target, binary-search the highest quality that fits.

    Returns (data, quality used or None for PNG, notes for the user).
    """
    notes: list[str] = []
    if fmt == "jpeg" and has_alpha(img):
        img = flatten(img, options.background or "#ffffff")
        notes.append("JPG can't store transparency, so transparent areas were filled with white.")

    def run(quality: int) -> bytes:
        return save(img, fmt, quality, png_colors=options.png_colors, icc_profile=icc_profile, exif=exif)

    if fmt == "png":
        if options.target_kb:
            notes.append("The size target only applies to JPG and WebP. For PNG, try 'Reduce colours' or resizing.")
        return run(options.quality), None, notes

    best = run(options.quality)
    if not options.target_kb or len(best) <= options.target_kb * 1024:
        return best, options.quality, notes

    target = options.target_kb * 1024
    lo, hi = MIN_QUALITY, options.quality - 1
    found: tuple[bytes, int] | None = None
    while lo <= hi:  # ~6 encodes for quality 1-100
        mid = (lo + hi) // 2
        data = run(mid)
        if len(data) <= target:
            found, lo = (data, mid), mid + 1
        else:
            hi = mid - 1
    if found:
        return found[0], found[1], notes
    smallest = run(MIN_QUALITY)
    notes.append(f"Couldn't get under {options.target_kb} KB even at the lowest quality. Try resizing it as well.")
    return smallest, MIN_QUALITY, notes


def exif_bytes(exif: Image.Exif | None) -> bytes | None:
    """EXIF to write back when the user keeps metadata. The rotation flag is reset because
    the pixels have already been rotated upright."""
    if not exif:
        return None
    exif[0x0112] = 1  # Orientation: normal
    return exif.tobytes()
