# Lessons learned

Things that cost us hours, so you do not pay for them again.

## "The GPU is slower than the CPU"

It was not. Inference was running on the container's 2 vCPUs. Two bugs stacked:

1. **Wrong CUDA major version.** `onnxruntime-gpu` 1.30 requires CUDA 13 and cuDNN 9. On
   a CUDA 12.4 base image the CUDA provider library fails to load, rembg prints a warning
   nobody reads, and everything runs on CPU. Fix: `nvidia/cuda:13.0.1-cudnn-runtime-ubuntu24.04`.
2. **Flag not propagated to the container.** The code read a `GPU` env var to decide
   whether to request `CUDAExecutionProvider`. That variable existed on the laptop that ran
   `modal deploy`, not inside the container. Fix: bake runtime config into the image env
   (`.env({...})` in `modal_app.py`).

Guard that catches both now: `engine.py` reads `session.inner_session.get_providers()` and
raises if CUDA was requested but is not first. A box that boots is a box that runs on GPU.

## Measuring latency from the wrong place

End-to-end wall time from a laptop in Brazil had a p99 of 37 s against a 1.5 s server
time. Download of a 1.2 MB PNG at 150 KB/s. Always separate: inference (header from the
server), server time (TTFB minus RTT), and wall. Only the first two describe the service.

## Memory snapshots

- Load the model **inside** `@modal.enter(snap=True)`, including a warm-up inference. The
  session then comes back from the snapshot already hot. Loading after restore adds 7 to
  20 s on GPU.
- `experimental_options={"enable_gpu_snapshot": True}` is required for the CUDA context
  to be captured; without it the GPU session is created after restore.
- A new revision answers HTTP 303 on its very first request while the snapshot is built
  (~150 s). Clients must follow redirects (`curl -L`, `requests` does by default).
- Snapshots are per worker type; the first 2 to 4 requests after a deploy are slow. Warm up.

## Idle time is the bill

Inference is under 1 s, yet with `scaledown_window=300` the Modal bill matched PhotoRoom's:
with sparse traffic every photo kept an L4 up for ~5 minutes. Dropping the window to 10 s
cut that to ~15 s per photo, but most requests became cold starts (7 to 16 s to first byte
from Brazil, not the 2 to 6 s restore alone), and because snapshots are per worker type, a
cold container occasionally lands on a worker type without one and takes ~150 s even after
a warmup. Short windows need clients with a long timeout (180 s) that follow redirects.

## Cloud Run

- BiRefNet-lite OOMs at 4 GiB and even at 8 GiB on CPU with default onnxruntime arenas.
  Light models (u2net_human_seg, 2 GiB) are fine. No GPU in `southamerica-east1`.
- Cold start is dominated by image pull. Bake weights into the image anyway: downloading
  220 MB at startup is worse.
- `requests` from a Mac to a Cloud Run URL may hang on IPv6; `curl -4` does not.

## onnxruntime on Apple Silicon

Setting `intra_op_num_threads` to all cores made BiRefNet 40% slower: the efficiency cores
drag the matmuls. Leave the default.

## Pairing inputs with outputs from object storage

When outputs are only stored on success, pair each output with the last preceding input
within a window (600 s), one-to-one, and verify with a content diff. The naive "nearest
timestamp" gave 24% wrong pairs and inflated the error of every model.
