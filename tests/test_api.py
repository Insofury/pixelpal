import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import content_disposition, create_app
from tests.helpers import centre_mask, make_image, open_bytes


class FakeRemover:
    def __init__(self):
        self.calls = 0

    def mask(self, img):
        self.calls += 1
        return centre_mask(img)


def client(remover=None, **settings) -> TestClient:
    return TestClient(create_app(Settings(**settings), remover=remover))


def post(c: TestClient, data: bytes, options: dict | None = None, name: str = "photo.jpg"):
    return c.post(
        "/api/process",
        files={"file": (name, data, "application/octet-stream")},
        data={"options": json.dumps(options or {})},
    )


def test_health_and_config():
    c = client(FakeRemover())
    assert c.get("/api/health").json()["status"] == "ok"
    config = c.get("/api/config").json()
    assert config["background_removal"] is True and config["mode"] == "server" and config["max_upload_mb"] == 15
    assert client(None).get("/api/config").json()["background_removal"] is False


def test_process_returns_the_image_and_info():
    res = post(client(), make_image((800, 600)), {"resize": "fit", "width": 400, "format": "webp"}, name="Été photo.jpg")
    assert res.status_code == 200 and res.headers["content-type"] == "image/webp"
    info = json.loads(res.headers["x-pixelpal-info"])
    assert (info["width"], info["height"]) == (400, 300) and info["filename"] == "Été photo.webp"
    assert info["bytes"] == len(res.content) and open_bytes(res.content).size == (400, 300)
    assert "filename*=UTF-8''%C3%89t%C3%A9%20photo.webp" in res.headers["content-disposition"]
    assert res.headers["cache-control"] == "no-store"


def test_background_removal_uses_the_remover():
    remover = FakeRemover()
    res = post(client(remover), make_image(), {"remove_background": True})
    assert res.status_code == 200 and remover.calls == 1
    assert open_bytes(res.content).getpixel((2, 2))[3] == 0


def test_background_removal_unavailable():
    res = post(client(None), make_image(), {"remove_background": True})
    assert res.status_code == 503 and "isn't available" in res.json()["detail"]


def test_errors():
    c = client()
    assert post(c, make_image(), {"resize": "fit"}).status_code == 400
    assert post(c, b"not an image").status_code == 400
    assert c.post("/api/process", data={"options": "{}"}).status_code == 422  # no file
    small = client(max_upload_bytes=1000)
    assert post(small, make_image((300, 300), "PNG")).status_code == 413
    assert post(client(max_pixels=10_000), make_image((300, 300))).status_code == 413


def test_rate_limits():
    c = client(FakeRemover(), images_per_minute=2, bg_per_minute=1)
    data = make_image((60, 40))
    assert post(c, data, {"remove_background": True}).status_code == 200
    assert post(c, data, {"remove_background": True}).status_code == 429  # background limit
    res = post(c, data)
    assert res.status_code == 429 and "Retry-After" in res.headers  # overall limit (3rd request)


def test_site_and_security_headers():
    c = client()
    res = c.get("/")
    assert res.status_code == 200 and "PixelPal" in res.text
    assert "blob:" in res.headers["content-security-policy"] and res.headers["x-frame-options"] == "DENY"
    assert c.head("/").status_code == 200
    assert c.get("/static/js/app.js").status_code == 200
    assert c.get("/browser-mode.json").status_code == 404  # so the UI uses the server engine


def test_content_disposition_fallback():
    header = content_disposition('naïve "quote".png')
    assert header.startswith('attachment; filename="na_ve _quote_.png"')
