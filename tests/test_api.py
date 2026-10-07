import io
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from bg_remove.api import create_app
from bg_remove.config import Device, Settings


@dataclass
class FakeEngine:
    model: str = "fake"
    device: str = "CPUExecutionProvider"
    load_seconds: float = 0.0

    def remove(self, image_bytes: bytes) -> bytes:
        im = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        out = io.BytesIO()
        im.save(out, format="PNG")
        return out.getvalue()


def settings(**overrides) -> Settings:
    base = dict(api_key=SecretStr("k"), device=Device.CPU, max_upload_mb=1, models_dir="/tmp")
    return Settings(**{**base, **overrides})


def jpeg(size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def client():
    app = create_app(lambda _cfg: FakeEngine(), settings())
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["model"] == "fake"


def test_requires_api_key(client):
    r = client.post("/v1/segment", files={"image_file": ("a.jpg", jpeg(), "image/jpeg")})
    assert r.status_code == 401
    r = client.post(
        "/v1/segment", headers={"x-api-key": "wrong"}, files={"image_file": ("a.jpg", jpeg())}
    )
    assert r.status_code == 401


def test_segment_returns_rgba_png(client):
    r = client.post(
        "/v1/segment", headers={"x-api-key": "k"}, files={"image_file": ("a.jpg", jpeg())}
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert "X-Infer-S" in r.headers
    assert Image.open(io.BytesIO(r.content)).mode == "RGBA"


def test_rejects_non_image(client):
    r = client.post(
        "/v1/segment", headers={"x-api-key": "k"}, files={"image_file": ("a.txt", b"hello")}
    )
    assert r.status_code == 422


def test_rejects_oversized(client):
    big = b"\xff" * (2 * 1024 * 1024)
    r = client.post("/v1/segment", headers={"x-api-key": "k"}, files={"image_file": ("a.jpg", big)})
    assert r.status_code == 413


def test_auth_can_be_disabled():
    app = create_app(lambda _cfg: FakeEngine(), settings(require_auth=False))
    with TestClient(app) as c:
        r = c.post("/v1/segment", files={"image_file": ("a.jpg", jpeg())})
    assert r.status_code == 200


def test_empty_api_key_rejected():
    with pytest.raises(ValueError):
        settings(api_key=SecretStr("  "))
