# Deploy

The Modal deployment is complete on its own: one URL, authenticated, TLS-terminated. The
Cloudflare Worker in front is optional and only adds a custom domain and edge protection.

```
client ──x-api-key (client key)──▶ Cloudflare Worker  (optional: your domain, WAF, rate limit)
                                         │  x-api-key (origin key), Host rewritten
                                         ▼
                                   Modal: bg-remove   (L4, FastAPI, memory snapshot)
```

## 1. Modal

You need a [Modal](https://modal.com) account. The free monthly credit covers ~5k photos/month.

```bash
uv sync --extra dev && uv tool install modal && modal setup

# 1. The API key clients will send in x-api-key. Save it: Modal never shows it again.
KEY=$(openssl rand -hex 24); echo "$KEY"
modal secret create bg-remove BG_REMOVE_API_KEY=$KEY

# 2. Build the image and deploy. Prints https://<workspace>--bg-remove.modal.run
PYTHONPATH=src modal deploy -m bg_remove.modal_app

# 3. Warm up: builds the GPU memory snapshots (~150 s first request, then faster).
URL=https://<workspace>--bg-remove.modal.run
scripts/warmup.sh $URL $KEY

# 4. Try it.
curl -L $URL/health
curl -L -X POST $URL/v1/segment -H "x-api-key: $KEY" \
  -F image_file=@docs/assets/example-input.jpg -o out.png
```

`PYTHONPATH=src` is needed because the package lives under `src/` and `modal deploy -m`
imports it from the local environment.

Run `warmup.sh` after **every** deploy. Each new revision has to build its memory snapshots
again, and without the warmup a real user pays that cost: a ~150 s request.

The URL label is fixed to `bg-remove`, so the endpoint is always
`https://<workspace>--bg-remove.modal.run`. Rename the workspace in Modal settings for a
nicer prefix, or put a custom domain in front (section 2).

### Parameters

**Deploy time.** Read by `modal_app.py` on your machine when you run `modal deploy`:

| Variable | Default | What it does |
|---|---|---|
| `BG_REMOVE_MODEL` | `birefnet-general-lite` | rembg model baked into the image. See the [benchmark](docs/benchmark.md) before changing it |
| `BG_REMOVE_GPU` | `L4,T4` | Modal GPU types, in order of preference. If no L4 is free Modal takes a T4 (cheaper, a little slower) instead of making the request wait for an L4 |
| `BG_REMOVE_SCALEDOWN_S` | `10` | Seconds an idle container stays up. See the trade-off below |
| `BG_REMOVE_SECRET_NAME` | `bg-remove` | Modal secret that holds `BG_REMOVE_API_KEY` |

```bash
BG_REMOVE_SCALEDOWN_S=60 PYTHONPATH=src modal deploy -m bg_remove.modal_app
```

**Runtime.** Read inside the container (and locally from `.env`), validated at startup by
[`config.py`](src/bg_remove/config.py). Modal sets these in the image; you only provide the key:

| Variable | Default | What it does |
|---|---|---|
| `BG_REMOVE_API_KEY` | required | Shared secret clients send in `x-api-key` |
| `BG_REMOVE_MODEL` | `birefnet-general-lite` | Any rembg model name |
| `BG_REMOVE_DEVICE` | `cpu` (`cuda` on Modal) | `cuda` fails at startup if onnxruntime cannot load CUDA, instead of silently running on CPU |
| `BG_REMOVE_MAX_UPLOAD_MB` | `15` | Larger uploads get 413 |
| `BG_REMOVE_REQUIRE_AUTH` | `true` | `false` only for local development |

**Fixed in `modal_app.py`**, and why:

| Setting | Value | Reason |
|---|---|---|
| base image | `nvidia/cuda:13.0.1-cudnn-runtime-ubuntu24.04` | onnxruntime-gpu 1.30 needs CUDA 13 + cuDNN 9; on CUDA 12 it silently runs on CPU |
| weights | baked into the image | never downloaded at runtime |
| `cpu` / `memory` | 2 / 3 GiB | PNG decode/encode and the model are small; the GPU does the work |
| `enable_memory_snapshot` + `enable_gpu_snapshot` | on | cold start 132 s → seconds; the model is loaded and warmed inside the snapshot |
| `@modal.concurrent(max_inputs=4)` | 4 | a burst queues inside a warm container (~1 s per photo) instead of each photo waiting for a new GPU, which took 1 to 2 min in production |
| `max_containers` | 3 | enough for ~1 req/s; raise with volume |
| `timeout` | 120 s | per request |

### The cost trade-off: `scaledown_window`

Inference is under 1 s, but Modal bills every second a container is up. With sparse traffic
each photo costs roughly `cold start + inference + scaledown_window` of GPU time, so the
window, not the model, sets the bill:

| `BG_REMOVE_SCALEDOWN_S` | GPU time per isolated photo | Latency |
|---|---|---|
| 300 | ~300 s; cost about the same as PhotoRoom | almost always warm, ~1.2 s |
| 60 | ~70 s | warm if photos arrive within a minute of each other |
| **10** (default) | ~15 s, ~US$15/month at 5k photos | most requests are cold: 7 to 16 s |

With a short window, almost every request starts a container. Modal keeps one snapshot per
GPU worker type, so now and then a cold container lands on a worker type without one and
takes ~150 s. Give clients a timeout of at least 180 s and let them follow redirects.

## 2. Custom domain with Cloudflare (optional)

Add Cloudflare only when you need your own hostname, rate limiting / WAF / geo blocking, a
client key separate from the origin key, or to hide the Modal URL. The extra hop costs 20
to 50 ms.

Plain DNS does not work: Modal routes by `Host` and serves a `*.modal.run` certificate, so
a CNAME breaks routing and TLS. Cloudflare's host-header override is Enterprise-only. The
Worker in [`cloudflare/`](cloudflare/) is the free way.

Your domain must already be on Cloudflare.

```bash
cd cloudflare
npm i -g wrangler && wrangler login

# 1. In wrangler.toml, set the route to your hostname:
#    routes = [{ pattern = "bg.yourdomain.com/*", zone_name = "yourdomain.com" }]

# 2. Secrets (prompted, never written to a file):
wrangler secret put ORIGIN_URL        # https://<workspace>--bg-remove.modal.run
wrangler secret put ORIGIN_API_KEY    # the Modal BG_REMOVE_API_KEY
wrangler secret put CLIENT_API_KEY    # a new key, the one you give to clients

wrangler deploy
```

3. **DNS:** add a proxied (orange cloud) record for the hostname. The target does not
   matter because the Worker answers; `AAAA bg → 100::` is the usual placeholder.
4. **Dashboard (recommended):**
   - Rate limiting rule: e.g. 60 requests/minute per IP on `/v1/segment`.
   - WAF: block countries you do not serve, turn on bot fight mode.

The Worker only exposes `POST /v1/segment` and `GET /health`, checks `CLIENT_API_KEY`,
swaps in `ORIGIN_API_KEY`, follows Modal's redirects and sets `cache-control: no-store`.
The free tier (100k requests/day) is more than enough.

## Operations

| Task | Command |
|---|---|
| logs | `modal app logs bg-remove` |
| running containers | `modal container list` |
| deploy history | `modal app history bg-remove` |
| stop | `modal app stop bg-remove` |
| latency | `python scripts/bench.py <url> <key> photos/` (p50/p95/p99) |
| rotate key | `modal secret create bg-remove BG_REMOVE_API_KEY=<new> --force`, redeploy, warm up, then `wrangler secret put ORIGIN_API_KEY` |
