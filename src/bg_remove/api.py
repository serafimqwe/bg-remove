"""HTTP surface. Same contract as PhotoRoom's /v1/segment so clients only swap URL and key:

POST /v1/segment   multipart field `image_file`, header `x-api-key`  ->  image/png (RGBA)
GET  /health
"""

import io
import logging
import secrets
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from PIL import Image, UnidentifiedImageError

from bg_remove import __version__
from bg_remove.config import Settings, get_settings
from bg_remove.engine import Engine

log = logging.getLogger(__name__)

EngineFactory = Callable[[Settings], Engine]


def create_app(engine_factory: EngineFactory, settings: Settings | None = None) -> FastAPI:
    """Build the app around an engine factory so tests can inject a fake engine."""
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = engine_factory(cfg)
        yield

    app = FastAPI(
        title="bg-remove", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan
    )

    def engine(request: Request) -> Engine:
        return request.app.state.engine  # type: ignore[no-any-return]

    def authorize(x_api_key: str | None = Header(default=None)) -> None:
        if not cfg.require_auth:
            return
        expected = cfg.api_key.get_secret_value()
        if x_api_key is None or not secrets.compare_digest(x_api_key, expected):
            raise HTTPException(status_code=401, detail="invalid api key")

    @app.get("/health")
    def health(eng: Annotated[Engine, Depends(engine)]) -> dict[str, object]:
        return {
            "ok": True,
            "version": __version__,
            "model": eng.model,
            "device": eng.device,
            "load_s": round(eng.load_seconds, 2),
        }

    @app.post("/v1/segment", dependencies=[Depends(authorize)])
    async def segment(
        image_file: Annotated[UploadFile, File()], eng: Annotated[Engine, Depends(engine)]
    ) -> Response:
        data = await image_file.read()
        if not data:
            raise HTTPException(status_code=400, detail="empty image_file")
        if len(data) > cfg.max_upload_bytes:
            raise HTTPException(status_code=413, detail=f"image_file over {cfg.max_upload_mb} MB")
        try:
            Image.open(io.BytesIO(data)).verify()
        except (UnidentifiedImageError, OSError) as e:
            raise HTTPException(status_code=422, detail=f"not an image: {e}") from e
        t0 = time.perf_counter()
        png = eng.remove(data)
        return Response(
            png,
            media_type="image/png",
            headers={"X-Infer-S": f"{time.perf_counter() - t0:.3f}", "X-Model": eng.model},
        )

    return app


def default_app() -> FastAPI:
    """Entry point: `uvicorn --factory bg_remove.api:default_app`."""
    from bg_remove.engine import RembgEngine

    logging.basicConfig(level=logging.INFO)
    return create_app(RembgEngine.load)
