# Deployment

```
client ──x-api-key(client)──▶ Cloudflare Worker (your domain, WAF, rate limit)
                                    │  x-api-key(origin), Host rewritten
                                    ▼
                              Modal: bg-remove (L4, FastAPI, memory snapshot)
```

## 1. Modal

```bash
uv tool install modal && modal setup
modal secret create bg-remove BG_REMOVE_API_KEY=$(openssl rand -hex 24)
modal deploy -m bg_remove.modal_app
scripts/warmup.sh https://<workspace>--bg-remove.modal.run <key>
```

Settings in `modal_app.py` and why:

| Setting | Value | Reason |
|---|---|---|
| base image | `nvidia/cuda:13.0.1-cudnn-runtime-ubuntu24.04` | onnxruntime-gpu 1.30 needs CUDA 13 + cuDNN 9 |
| `gpu` | `L4` | 0.8 s per inference; cheapest GPU that fits; A10/T4 not needed |
| `cpu` / `memory` | 2 / 3 GiB | PNG decode/encode and the model are small; GPU does the work |
| `enable_memory_snapshot` + `enable_gpu_snapshot` | on | cold start 132 s → 2 to 6 s |
| `@modal.enter(snap=True)` | loads + warms the model | it must be inside the snapshot |
| `@modal.concurrent(max_inputs=1)` | 1 | one inference at a time per container; scale with containers |
| `scaledown_window` | 300 s | keep a warm container through bursts; idle costs nothing after that |
| `max_containers` | 3 | enough for ~1 req/s; raise with volume |
| weights | baked at image build | never download at runtime |

Override model, GPU or secret name at deploy time: `BG_REMOVE_MODEL=... BG_REMOVE_GPU=A10G modal deploy ...`.

The URL label is fixed to `bg-remove`, so the endpoint is
`https://<workspace>--bg-remove.modal.run`. Rename the workspace in Modal settings for a
nicer prefix. Native custom domains need Modal's Team plan.

## 2. Cloudflare in front (optional)

The Modal URL alone is a complete, authenticated, TLS-terminated deployment. Add Cloudflare
only when you need one of: your own hostname, rate limiting / WAF / geo blocking, a client
key separate from the origin key, or hiding the Modal URL. Cost of the hop: one extra
proxy, typically 20 to 50 ms, negligible against ~1 s of server time.

Plain DNS does not work: Modal routes by `Host` and serves a `*.modal.run` certificate, so
a CNAME breaks routing and TLS. Cloudflare's host-header override is Enterprise-only. A
Worker is the free way, and it also gives you WAF, rate limiting and key separation.

```bash
cd cloudflare
npm i -g wrangler && wrangler login
# edit wrangler.toml: routes pattern/zone_name
wrangler secret put ORIGIN_URL        # https://<workspace>--bg-remove.modal.run
wrangler secret put ORIGIN_API_KEY    # the Modal BG_REMOVE_API_KEY
wrangler secret put CLIENT_API_KEY    # what you give to clients
wrangler deploy
```

DNS: a proxied (orange-cloud) record for the hostname, any target (`AAAA 100::` is the
usual placeholder). The route in `wrangler.toml` binds the Worker to it.

Then in the Cloudflare dashboard:

- **Rate limiting rule**: e.g. 60 requests / minute per IP on `/v1/segment`.
- **WAF**: block countries you do not serve, bot fight mode on.
- **Cache**: nothing to do, the Worker sets `cache-control: no-store`.
- **Timeouts**: Worker fetch allows long origins; the first request after a deploy can
  take 150 s, which is why `warmup.sh` exists.

Worker free tier: 100k requests/day, more than enough for this traffic.

## 3. Clients

Swap two values wherever PhotoRoom was called:

```python
requests.post(
    "https://bg.example.com/v1/segment",  # was https://sdk.photoroom.com/v1/segment
    headers={"x-api-key": CLIENT_API_KEY},
    files={"image_file": ("image.jpg", image_bytes)},
    timeout=120,
)
# response.content is an RGBA PNG, same as PhotoRoom
```

Run both in parallel for a few days and compare samples before cancelling the old provider.

## Operations

| Task | Command |
|---|---|
| logs | `modal app logs bg-remove` |
| stop | `modal app stop bg-remove` |
| latency check | `python scripts/bench.py <url> <key> photos/` |
| rotate key | `modal secret create bg-remove BG_REMOVE_API_KEY=... --force` then `modal deploy`, then `wrangler secret put ORIGIN_API_KEY` |
