"""Modal deployment: BiRefNet on an L4 GPU behind the FastAPI app, with memory snapshots.

    modal secret create bg-remove BG_REMOVE_API_KEY=$(openssl rand -hex 24)
    PYTHONPATH=src modal deploy -m bg_remove.modal_app
    scripts/warmup.sh <url> <key>     # first requests of a new revision build the snapshots

Why each setting is what it is: DEPLOY.md (Parameters) and docs/lessons.md.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

import modal

if TYPE_CHECKING:
    from fastapi import FastAPI

MODEL = os.environ.get("BG_REMOVE_MODEL", "birefnet-general-lite")
# Ordered fallback: if no L4 is free, Modal takes a T4 instead of queueing for minutes.
GPU = os.environ.get("BG_REMOVE_GPU", "L4,T4").split(",")
SECRET = os.environ.get("BG_REMOVE_SECRET_NAME", "bg-remove")
# A different name deploys a separate app and URL (benchmarks, a CPU variant) next to production.
APP_NAME = os.environ.get("BG_REMOVE_APP_NAME", "bg-remove")
SCALEDOWN_S = int(os.environ.get("BG_REMOVE_SCALEDOWN_S", "10"))

# onnxruntime-gpu 1.30 needs CUDA 13 + cuDNN 9. A CUDA 12 base image loads fine and then
# silently runs on CPU. Keep the two in lockstep when bumping either.
image = (
    modal.Image.from_registry("nvidia/cuda:13.0.1-cudnn-runtime-ubuntu24.04", add_python="3.12")
    .pip_install(
        "rembg[gpu]==2.0.85", "fastapi[standard]", "python-multipart", "pillow", "pydantic-settings"
    )
    .env({"U2NET_HOME": "/models", "BG_REMOVE_MODEL": MODEL, "BG_REMOVE_DEVICE": "cuda"})
    # Weights are baked into the image at build time, never downloaded at runtime. The build
    # step has no GPU, so ask for CPU explicitly (otherwise rembg logs a scary CUDA warning).
    .run_commands(
        'python -c "from rembg import new_session; '
        f"new_session('{MODEL}', providers=['CPUExecutionProvider'])\""
    )
    .add_local_python_source("bg_remove")
)

app = modal.App(APP_NAME, image=image)


@app.cls(
    gpu=GPU,
    cpu=2,
    memory=3072,
    timeout=120,
    # Idle time, not inference, is the bill: a 300 s window cost as much as PhotoRoom.
    # 10 s pays a 2 to 6 s snapshot cold start on most requests instead.
    scaledown_window=SCALEDOWN_S,
    enable_memory_snapshot=True,
    experimental_options={"enable_gpu_snapshot": True},
    max_containers=3,
    secrets=[modal.Secret.from_name(SECRET)],
)
# Inference blocks the event loop, so inputs run one after another inside the container. A
# burst queues for ~1 s each on a warm GPU instead of each one waiting for a new GPU.
@modal.concurrent(max_inputs=4)
class Service:
    @modal.enter(snap=True)
    def load(self) -> None:
        """Runs before the snapshot is taken: the loaded, warmed-up session is part of it."""
        from bg_remove.config import get_settings
        from bg_remove.engine import RembgEngine

        t0 = time.perf_counter()
        self.engine = RembgEngine.load(get_settings())
        print(f"engine ready in {time.perf_counter() - t0:.1f}s on {self.engine.device}")

    @modal.asgi_app(label=APP_NAME)
    def web(self) -> FastAPI:
        from bg_remove.api import create_app

        return create_app(lambda _settings: self.engine)
