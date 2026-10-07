"""Model wrapper around rembg/onnxruntime. Fails fast when the requested device is not active.

Lesson learned the hard way: onnxruntime silently falls back to CPU when the CUDA provider
cannot load (wrong CUDA major version, missing cuDNN, flag not propagated to the container).
A GPU box running on CPU looks "slow" instead of "broken". So we check the active providers.
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from typing import Protocol

from PIL import Image

from bg_remove.config import Device, Settings

log = logging.getLogger(__name__)


class Engine(Protocol):
    model: str
    device: str
    load_seconds: float

    def remove(self, image_bytes: bytes) -> bytes: ...


@dataclass
class RembgEngine:
    """Loads one rembg session, warms it up, and converts images to RGBA PNG without background."""

    model: str
    device: str
    load_seconds: float
    _session: object

    @classmethod
    def load(cls, settings: Settings) -> RembgEngine:
        from rembg import new_session, remove

        t0 = time.perf_counter()
        providers = ["CPUExecutionProvider"]
        if settings.device is Device.CUDA:
            providers.insert(0, "CUDAExecutionProvider")
        session = new_session(settings.model, providers=providers)
        active: list[str] = session.inner_session.get_providers()
        if settings.device is Device.CUDA and "CUDAExecutionProvider" not in active:
            raise RuntimeError(
                f"CUDA requested but onnxruntime is running on {active}. "
                "Check CUDA/cuDNN major versions against the onnxruntime-gpu build."
            )
        # Warm-up: first inference compiles kernels and allocates arenas (seconds on GPU).
        buf = io.BytesIO()
        Image.new("RGB", (640, 640), (120, 120, 120)).save(buf, format="JPEG")
        remove(buf.getvalue(), session=session)
        load_seconds = time.perf_counter() - t0
        log.info("model=%s providers=%s load=%.1fs", settings.model, active, load_seconds)
        return cls(
            model=settings.model, device=active[0], load_seconds=load_seconds, _session=session
        )

    def remove(self, image_bytes: bytes) -> bytes:
        from rembg import remove

        return bytes(remove(image_bytes, session=self._session))
