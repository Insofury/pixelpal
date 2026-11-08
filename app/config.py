"""Settings read from environment variables (or a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "static"


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader - real environment variables always take priority."""
    path = path or PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return default if not value else value in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    max_upload_bytes: int = 15 * 1024 * 1024  # per image
    max_pixels: int = 40_000_000
    images_per_minute: int = 120  # per IP
    bg_per_minute: int = 30  # background removals per IP (they're CPU heavy)
    bg_concurrency: int = 2  # background removals running at once
    bg_removal: bool = True
    bg_model: str = "u2netp"  # any rembg model: u2netp (fast), u2net, isnet-general-use, ...
    preload_model: bool = False  # load the model at startup instead of on the first request
    trust_proxy: bool = False

    @property
    def max_upload_mb(self) -> int:
        return self.max_upload_bytes // (1024 * 1024)

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        return cls(
            max_upload_bytes=max(1, _int("MAX_UPLOAD_MB", 15)) * 1024 * 1024,
            max_pixels=max(1, _int("MAX_MEGAPIXELS", 40)) * 1_000_000,
            images_per_minute=_int("IMAGES_PER_MINUTE", 120),
            bg_per_minute=_int("BG_PER_MINUTE", 30),
            bg_concurrency=max(1, _int("BG_CONCURRENCY", 2)),
            bg_removal=_bool("BG_REMOVAL", True),
            bg_model=os.environ.get("BG_MODEL", "").strip() or "u2netp",
            preload_model=_bool("PRELOAD_MODEL", True),
            trust_proxy=_bool("TRUST_PROXY", False),
        )
