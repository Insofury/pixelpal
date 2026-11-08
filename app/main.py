"""FastAPI application: the image API and the web app.

GET  /               the web app
GET  /api/health     health check
GET  /api/config     limits and whether background removal is available
POST /api/process    one image + options (JSON) -> the processed image
GET  /api/docs       interactive API docs
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
from contextlib import asynccontextmanager
from functools import partial
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .background import RembgRemover
from .config import STATIC_DIR, Settings
from .imaging import SUPPORTED_EXTENSIONS, ImageError
from .options import FORMATS, Options, OptionsError
from .pipeline import process
from .security import BodySizeLimitMiddleware, RateLimiter, SecurityHeadersMiddleware, client_ip

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("pixelpal")

_AUTO = object()


def content_disposition(filename: str) -> str:
    """attachment header that works for non-ASCII names (RFC 6266 / 5987)."""
    fallback = filename.encode("ascii", "replace").decode().replace('"', "_").replace("?", "_")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


def create_app(settings: Settings | None = None, remover=_AUTO) -> FastAPI:
    """`remover` is anything with a `.mask(image)` method; by default rembg if installed."""
    settings = settings or Settings.from_env()
    if remover is _AUTO:
        remover = RembgRemover(settings.bg_model) if settings.bg_removal and RembgRemover.available() else None
    image_limiter = RateLimiter(settings.images_per_minute)
    bg_limiter = RateLimiter(settings.bg_per_minute)
    bg_slots = asyncio.Semaphore(settings.bg_concurrency)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.preload_model and isinstance(remover, RembgRemover):
            threading.Thread(target=remover.warm_up, name="model-warm-up", daemon=True).start()
        yield

    app = FastAPI(
        title="PixelPal API",
        version=__version__,
        description="Remove backgrounds, resize, compress, convert and watermark images.",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=settings.max_upload_bytes + 256 * 1024,  # room for the form fields
        message=f"Each image must be under {settings.max_upload_mb} MB.",
    )
    app.add_middleware(SecurityHeadersMiddleware)
    logger.info(
        "PixelPal v%s starting (background removal %s)",
        __version__,
        f"ON, model {settings.bg_model}" if remover else "OFF",
    )

    def limit(limiter: RateLimiter, request: Request) -> None:
        allowed, retry_after = limiter.hit(client_ip(request, settings.trust_proxy))
        if not allowed:
            wait = max(1, math.ceil(retry_after))
            raise HTTPException(429, f"Too many requests - please wait {wait}s.", headers={"Retry-After": str(wait)})

    @app.get("/api/health", tags=["meta"])
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/api/config", tags=["meta"])
    async def config() -> dict:
        return {
            "version": __version__,
            "mode": "server",
            "background_removal": remover is not None,
            "bg_model": settings.bg_model if remover else None,
            "max_upload_mb": settings.max_upload_mb,
            "max_megapixels": settings.max_pixels // 1_000_000,
            "formats": list(FORMATS),
            "file_types": list(SUPPORTED_EXTENSIONS),
        }

    @app.post(
        "/api/process",
        tags=["images"],
        response_class=Response,
        responses={200: {"content": {"image/png": {}, "image/jpeg": {}, "image/webp": {}}}},
    )
    async def process_route(
        request: Request,
        file: UploadFile = File(..., description="PNG, JPG, WebP, GIF, BMP or TIFF"),
        options: str = Form("{}", description="Processing options as JSON (see README)"),
    ) -> Response:
        """Process one image. The result's details (size, dimensions, notes) are in the
        `X-PixelPal-Info` response header as JSON."""
        limit(image_limiter, request)
        try:
            opts = Options.from_json(options)
        except OptionsError as exc:
            raise HTTPException(400, str(exc)) from exc
        if opts.remove_background:
            if remover is None:
                raise HTTPException(503, "Background removal isn't available on this server.")
            limit(bg_limiter, request)

        data = await file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, f"Each image must be under {settings.max_upload_mb} MB.")

        job = partial(
            process,
            data,
            file.filename or "image",
            opts,
            mask_fn=remover.mask if remover else None,
            max_pixels=settings.max_pixels,
        )
        try:
            # Image work is CPU-bound: run it in a worker thread, off the event loop.
            if opts.remove_background:
                async with bg_slots:
                    result = await run_in_threadpool(job)
            else:
                result = await run_in_threadpool(job)
        except ImageError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

        return Response(
            content=result.data,
            media_type=result.mime,
            headers={
                "Content-Disposition": content_disposition(result.filename),
                "X-PixelPal-Info": json.dumps(result.info(), ensure_ascii=True),
                "Cache-Control": "no-store",
            },
        )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    return app


app = create_app()
