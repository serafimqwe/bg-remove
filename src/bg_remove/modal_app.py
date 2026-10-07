"""Modal deployment: BiRefNet on an L4 GPU behind the FastAPI app, with memory snapshots.

    modal secret create bg-remove BG_REMOVE_API_KEY=$(openssl rand -hex 24)
    modal deploy -m bg_remove.modal_app
    scripts/warmup.sh <url> <key>     # first requests of a new revision build the snapshots

Why each setting is what it is: docs/deployment.md and docs/lessons.md.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

import modal

if TYPE_CHECKING:
    from fastapi import FastAPI

MODEL = os.environ.get("BG_REMOVE_MODEL", "birefnet-general-lite")
GPU = os.environ.get("BG_REMOVE_GPU", "L4")
SECRET = os.environ.get("BG_REMOVE_SECRET_NAME", "bg-remove")

# onnxruntime-gpu 1.30 needs CUDA 13 + cuDNN 9. A CUDA 12 base image loads fine and then
# silently runs on CPU. Keep the two in lockstep when bumping either.
image = (
    modal.Image.from_registry("nvidia/cuda:13.0.1-cudnn-runtime-ubuntu24.04", add_python="3.12")
    .pip_install(
        "rembg[gpu]==2.0.85", "fastapi[standard]", "python-multipart", "pillow", "pydantic-settings"
    )
    .env({"U2NET_HOME": "/models", "BG_REMOVE_MODEL": MODEL, "BG_REMOVE_DEVICE": "cuda"})
    # Weights are baked into the image at build time, never downloaded at runtime.
    .run_commands(f"python -c \"from rembg import new_session; new_session('{MODEL}')\"")
    .add_local_python_source("bg_remove")
)

app = modal.App("bg-remove", image=image)


@app.cls(
    gpu=GPU,
    cpu=2,
    memory=3072,
    timeout=120,
    scaledown_window=300,  # keep a warm container 5 min after the last request
    enable_memory_snapshot=True,
    experimental_options={"enable_gpu_snapshot": True},
    max_containers=3,
    secrets=[modal.Secret.from_name(SECRET)],
)
@modal.concurrent(max_inputs=1)  # one inference per container; scale horizontally
class Service:
    @modal.enter(snap=True)
    def load(self) -> None:
        """Runs before the snapshot is taken: the loaded, warmed-up session is part of it."""
        from bg_remove.config import get_settings
        from bg_remove.engine import RembgEngine

        t0 = time.perf_counter()
        self.engine = RembgEngine.load(get_settings())
        print(f"engine ready in {time.perf_counter() - t0:.1f}s on {self.engine.device}")

    @modal.asgi_app(label="bg-remove")
    def web(self) -> FastAPI:
        from bg_remove.api import create_app

        return create_app(lambda _settings: self.engine)
