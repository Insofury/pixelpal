"""Processing options: what to do to every image in a batch.

The web UI sends these as JSON (the same shape in the server and browser versions).
`Options.from_dict` validates everything and raises `OptionsError` with a message
that can be shown to the user as-is.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

FORMATS = ("original", "png", "jpeg", "webp")
RESIZE_MODES = ("none", "fit", "cover", "percent")
POSITIONS = (
    "top-left", "top", "top-right",
    "left", "center", "right",
    "bottom-left", "bottom", "bottom-right",
    "tile",
)
MAX_SIDE = 10_000
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
SUFFIX_CHARS = re.compile(r"[^A-Za-z0-9._-]")


class OptionsError(ValueError):
    """Invalid options. The message is written for end users."""


@dataclass(frozen=True)
class Watermark:
    text: str = ""
    position: str = "bottom-right"  # one of POSITIONS
    opacity: float = 0.6  # 0.05 - 1
    size: float = 5.0  # text height as a % of the image's shorter side (1 - 30)
    color: str = "#ffffff"

    @property
    def enabled(self) -> bool:
        return bool(self.text.strip())


@dataclass(frozen=True)
class Options:
    remove_background: bool = False
    background: str = ""  # after removal: "" keeps it transparent, "#rrggbb" fills it
    resize: str = "none"  # one of RESIZE_MODES
    width: int = 0  # 0 = no limit (for "fit")
    height: int = 0
    percent: float = 100.0
    upscale: bool = False  # let "fit" enlarge images smaller than the box
    format: str = "original"  # one of FORMATS
    quality: int = 82  # JPEG / WebP quality, 1 - 100
    target_kb: int = 0  # 0 = off, otherwise lower the quality until the file fits
    png_colors: bool = False  # lossy PNG: reduce to a 256-colour palette
    keep_metadata: bool = False  # False strips EXIF (camera, GPS); colour profiles are always kept
    suffix: str = ""  # added to output file names, e.g. "-web"
    watermark: Watermark = field(default_factory=Watermark)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, text: str | None) -> Options:
        try:
            raw = json.loads(text or "{}")
        except (TypeError, ValueError) as exc:
            raise OptionsError("The options must be valid JSON.") from exc
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: Any) -> Options:
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise OptionsError("The options must be a JSON object.")
        wm_raw = raw.get("watermark") or {}
        if not isinstance(wm_raw, dict):
            raise OptionsError("'watermark' must be an object.")

        text = _text(wm_raw, "text", 120, "Watermark text")
        watermark = Watermark(
            text=text,
            position=_choice(wm_raw, "position", POSITIONS, "bottom-right"),
            opacity=_number(wm_raw, "opacity", 0.6, 0.05, 1.0, "Watermark opacity"),
            size=_number(wm_raw, "size", 5.0, 1.0, 30.0, "Watermark size"),
            color=_color(wm_raw, "color", "#ffffff", allow_empty=False),
        )

        options = cls(
            remove_background=_bool(raw, "remove_background"),
            background=_color(raw, "background", "", allow_empty=True),
            resize=_choice(raw, "resize", RESIZE_MODES, "none"),
            width=int(_number(raw, "width", 0, 0, MAX_SIDE, "Width", integer=True)),
            height=int(_number(raw, "height", 0, 0, MAX_SIDE, "Height", integer=True)),
            percent=_number(raw, "percent", 100.0, 1.0, 1000.0, "Scale"),
            upscale=_bool(raw, "upscale"),
            format=_choice(raw, "format", FORMATS, "original", aliases={"jpg": "jpeg"}),
            quality=int(_number(raw, "quality", 82, 1, 100, "Quality", integer=True)),
            target_kb=int(_number(raw, "target_kb", 0, 0, 100_000, "Target size", integer=True)),
            png_colors=_bool(raw, "png_colors"),
            keep_metadata=_bool(raw, "keep_metadata"),
            suffix=SUFFIX_CHARS.sub("", str(raw.get("suffix") or ""))[:30],
            watermark=watermark,
        )

        if options.resize == "fit" and not (options.width or options.height):
            raise OptionsError("Enter a width or a height to resize to.")
        if options.resize == "cover" and not (options.width and options.height):
            raise OptionsError("Enter both a width and a height to crop to.")
        return options


# ------------------------------------------------------------------ helpers
def _bool(raw: dict, key: str) -> bool:
    value = raw.get(key, False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _choice(raw: dict, key: str, allowed: tuple[str, ...], default: str, aliases: dict | None = None) -> str:
    value = str(raw.get(key) or default).strip().lower()
    value = (aliases or {}).get(value, value)
    if value not in allowed:
        raise OptionsError(f"'{key}' must be one of: {', '.join(allowed)}.")
    return value


def _number(raw: dict, key: str, default: float, lo: float, hi: float, label: str, integer: bool = False) -> float:
    value = raw.get(key, default)
    if value in (None, ""):
        value = default
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise OptionsError(f"{label} must be a number.") from exc
    if number != number or not lo <= number <= hi:  # NaN or out of range
        raise OptionsError(f"{label} must be between {lo:g} and {hi:g}.")
    return round(number) if integer else number


def _color(raw: dict, key: str, default: str, allow_empty: bool) -> str:
    value = str(raw.get(key) if raw.get(key) is not None else default).strip()
    if not value and allow_empty:
        return ""
    if not HEX_COLOR.match(value):
        raise OptionsError(f"'{key}' must be a colour like #ffffff.")
    return value.lower()


def _text(raw: dict, key: str, limit: int, label: str) -> str:
    value = " ".join(str(raw.get(key) or "").split())  # collapse newlines / runs of spaces
    if len(value) > limit:
        raise OptionsError(f"{label} can be at most {limit} characters.")
    return value
