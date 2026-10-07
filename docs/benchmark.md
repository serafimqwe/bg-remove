# Benchmark

Dataset: 100 real portrait photos from a production ID-badge flow (not published, they are
real people), each paired with PhotoRoom's output as ground truth. 20 of them form a dev
split used for ablations; the other 80 are the test split.

Metrics: **IoU** of the alpha mask against PhotoRoom's (threshold 0.5), mean absolute
error, boundary F-score on a 3 px band, and the share of photos with IoU below 0.90
("visibly worse"). Latency is wall time of `rembg.remove()` per image.

## Models (100 photos, CPU, Apple M5 Pro)

| Model | License | IoU | IoU < 0.90 | p50 | p99 | Weights |
|---|---|---|---|---|---|---|
| u2netp | Apache-2.0 | 0.928 | 17% | 0.09 s | 0.18 s | 5 MB |
| u2net | Apache-2.0 | 0.944 | 13% | 0.20 s | 0.30 s | 176 MB |
| u2net_human_seg | Apache-2.0 | 0.977 | 4% | 0.20 s | 0.31 s | 176 MB |
| isnet-general-use | Apache-2.0 | 0.955 | 9% | 0.45 s | 0.67 s | 176 MB |
| **birefnet-general-lite** | **MIT** | **0.985** | **0%** | 5.7 s | 11.9 s | 220 MB |
| birefnet-portrait | MIT | 0.985 | 1% | 7.5 s | 10.1 s | 930 MB |
| birefnet-general | MIT | 0.985 | 1% | 7.9 s | 10.9 s | 930 MB |
| bria-rmbg (RMBG-2.0) | CC BY-NC | 0.986 | 1% | 8.0 s | 15.5 s | 1 GB |

BiRefNet-lite is visually indistinguishable from PhotoRoom, including wavy blond hair and
stray strands. The larger BiRefNet variants add nothing on portraits. RMBG-2.0 is the
only one that is not usable commercially.

## Preprocessing ablation (20 photos)

| Variant | isnet | u2net_human_seg | birefnet-lite |
|---|---|---|---|
| baseline | 0.956 (min 0.44) | 0.970 (min 0.83) | 0.987 (min 0.94) |
| reflect-pad 15% | 0.961 (min 0.81) | **0.972 (min 0.84)** | 0.980 |
| edge-pad 15% | **0.964 (min 0.85)** | 0.966 | 0.980 |
| alpha matting | 0.953, 2x slower | 0.970, 2x slower | 0.985 |
| post_process_mask, 2x upscale, flip TTA, autocontrast | no effect | no effect | no effect |

Padding rescues the light models when the subject touches the crop border (hair and
shoulders cut by a tight face crop become "background"). BiRefNet does not need it and
gets slightly worse. Alpha matting hurts edges and doubles the time: never use it.

## Optimization loop (autoresearch-style, dev split, fixed time budget)

| # | Change | IoU | p50 CPU | Verdict |
|---|---|---|---|---|
| 0 | u2net_human_seg | 0.9700 | 0.42 s | keep |
| 1 | + reflect-pad 15% | 0.9720 | 0.47 s | keep |
| 2 | isnet + pad | 0.9606 | 0.97 s | discard |
| 3 | birefnet-lite + pad | 0.9804 | 10.1 s | over budget |
| 4 | birefnet-lite, no pad | **0.9873** | 9.6 s | keep |
| 5 | onnxruntime threads = all cores | 0.9865 | 13.2 s | discard (efficiency cores) |
| 6 | downscale input to 768 px | 0.9858 | 8.5 s | tie, 11% faster |
| 7 | downscale to 512 px | 0.9852 | 8.4 s | discard (model resamples to 1024 anyway) |
| 8 | hybrid u2net → birefnet on suspicious masks | 0.9847 | 8.7 s | discard (14/20 routed to slow path) |
| 9 | hybrid, looser thresholds | 0.9783 | 0.88 s | discard (−0.0075 IoU) |

Confirmed on the 80-photo test split: IoU 0.984, min 0.904, 0 photos below 0.90.
Conclusion: plain BiRefNet-lite is the practical ceiling; ship it without pre/post-processing.

## Where to run it

### Cloud Run, CPU only (southamerica-east1, 2026-10)

| Config | Model | infer p50 | infer p99 | Note |
|---|---|---|---|---|
| 2 vCPU / 4 GiB | u2net_human_seg | 1.50 s | 2.18 s | stable |
| 2 vCPU / 4 GiB | isnet-general-use | 3.11 s | 4.27 s | stable |
| 2 vCPU / 4 GiB | birefnet-general-lite | — | — | OOM (4.2 to 4.5 GiB) |
| 4 vCPU / 8 GiB | birefnet-general-lite | ~21 s | — | OOM after 2 to 3 requests |

Cloud Run x86 vCPUs were ~7x slower than the M5 Pro. Cold start 49 to 77 s, mostly image
pull (~2 GB). No GPU in São Paulo. BiRefNet is not viable there without heavy memory tuning.

### Modal

| Config | Model | infer p50 | infer p99 | server p50 | server p99 | cold start |
|---|---|---|---|---|---|---|
| **L4, 2 vCPU, 3 GiB** | **birefnet-general-lite** | **0.77 s** | **1.48 s** | **1.20 s** | **1.82 s** | **2.2 / 2.3 / 6.0 s** |
| L4, 2 vCPU, 3 GiB | u2net_human_seg | 0.37 s | 1.10 s | 0.75 s | 1.56 s | same container |
| CPU 4 vCPU, 6 GiB | u2net_human_seg | 0.55 s | 0.74 s | | | 2.1 s |
| CPU 4 vCPU, 6 GiB | birefnet-general-lite | 5.96 s | 6.34 s | | | 2.1 s |

"server" = time to first byte minus the `/health` round trip: upload, decode, inference,
PNG encode. End-to-end wall time from a laptop was p99 of 9 to 37 s, entirely download
bandwidth (PNGs up to 1.2 MB at ~150 KB/s); from inside a cloud it disappears.

Cold start without memory snapshots: 132 s. With snapshots (model loaded and warmed inside
the snapshot): 2 to 6 s. The first request of a new revision still takes ~150 s and the next
2 or 3 take 40 to 105 s, because Modal builds one snapshot per worker type. `scripts/warmup.sh`
pays that cost right after deploy.

Cost: L4 at US$0.000222/s, ~2 s per request → 5,000 req/month ≈ US$2.5.
