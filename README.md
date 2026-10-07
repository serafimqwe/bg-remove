# bg-remove

Self-hosted background removal API. A drop-in replacement for PhotoRoom's `/v1/segment`:
same request (multipart `image_file`, header `x-api-key`), same response (RGBA PNG).
Runs **BiRefNet-general-lite** (MIT) on a **Modal L4 GPU**. The `*.modal.run` URL is all
you need; a **Cloudflare Worker** for a custom domain and edge protection is an optional
add-on, kept in [`cloudflare/`](cloudflare/).

Built after replacing PhotoRoom in a production ID-badge flow (~5k photos/month).
Everything we learned is in [`docs/`](docs/): the model benchmark, the real numbers from
Cloud Run and Modal, and the mistakes that made a GPU look slower than a CPU.

## Results in one table

Measured on 100 real portrait photos, against PhotoRoom's output as ground truth
(IoU = mask overlap, 1.0 = identical). Latency is per request on the deployed service.

| | PhotoRoom | bg-remove (BiRefNet-lite, L4) |
|---|---|---|
| Mask agreement (IoU, mean / min) | 1.0 / 1.0 | **0.984 / 0.904** |
| Cases visibly worse | — | 0 / 100 |
| Inference p50 / p99 | — | **0.77 s / 1.48 s** |
| Server time p50 / p99 (upload + infer + encode) | ~2 s | **1.2 s / 1.8 s** |
| Cold start after idle | — | 2 to 6 s (memory snapshot) |
| Cost at 5k req/month | ~US$100 | **~US$2.5** (inside Modal's free credit) |
| License | commercial API | MIT (model and code) |

Full tables, other models (u2net, isnet, BiRefNet-portrait, RMBG-2.0), CPU numbers and
preprocessing ablations: [docs/benchmark.md](docs/benchmark.md).

## Quick start

```bash
uv sync --extra cpu --extra dev               # local CPU, for development and tests
cp .env.example .env                          # set BG_REMOVE_API_KEY
uv run uvicorn --factory bg_remove.api:default_app --port 8000
curl -X POST localhost:8000/v1/segment -H "x-api-key: $KEY" -F image_file=@photo.jpg -o out.png
```

## Deploy to Modal (GPU)

```bash
uv tool install modal && modal setup
modal secret create bg-remove BG_REMOVE_API_KEY=$(openssl rand -hex 24)
modal deploy -m bg_remove.modal_app           # prints https://<workspace>--bg-remove.modal.run
scripts/warmup.sh <url> <key>                 # builds the memory snapshots (30-150 s each, once per revision)
python scripts/bench.py <url> <key> photos/   # p50/p95/p99
```

That is a complete deployment. If you later want your own domain, rate limiting or WAF in
front, [docs/deployment.md](docs/deployment.md) covers the optional Cloudflare Worker,
what it adds (one extra hop, ~20 to 50 ms) and why plain DNS is not enough.

## Project layout

```
src/bg_remove/config.py     typed settings, every env var validated at startup (BG_REMOVE_*)
src/bg_remove/engine.py     rembg/onnxruntime session; fails fast if CUDA was requested but is not active
src/bg_remove/api.py        FastAPI app factory (engine injected, so tests use a fake)
src/bg_remove/modal_app.py  Modal deployment: CUDA 13 image, weights baked in, GPU memory snapshot
cloudflare/                 Worker + wrangler.toml for a custom domain and edge auth
scripts/                    warmup.sh (post-deploy), bench.py (latency)
docs/                       benchmark.md, deployment.md, lessons.md
```

## API

| Method | Path | Auth | Body | Response |
|---|---|---|---|---|
| POST | `/v1/segment` | `x-api-key` | multipart `image_file` (jpeg/png/webp, ≤15 MB) | `image/png` RGBA, same size as input. Headers `X-Infer-S`, `X-Model` |
| GET | `/health` | none | — | `{ok, version, model, device, load_s}` |

Errors: 401 bad key, 400 empty file, 413 too large, 422 not an image.

## Configuration

All settings are environment variables with the `BG_REMOVE_` prefix, parsed and validated
by `pydantic-settings` ([`config.py`](src/bg_remove/config.py)). See [`.env.example`](.env.example).

## License

MIT. BiRefNet weights are MIT as well. Do not switch to `bria-rmbg` (RMBG-2.0) for
commercial use: it is CC BY-NC.
