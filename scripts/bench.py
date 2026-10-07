"""Latency bench against a running endpoint. Prints p50/p95/p99 of server time and inference.

    python scripts/bench.py URL KEY dir_with_images/ [--n 20]

Server time = time-to-first-byte minus the /health round trip, i.e. upload + decode +
inference + PNG encode. Wall time is dominated by your own download bandwidth (PNGs are
0.3-1.5 MB), so it is reported but not the number to look at.
"""

from __future__ import annotations

import statistics
import subprocess
import sys
from pathlib import Path


def curl(args: list[str]) -> str:
    return subprocess.run(
        ["curl", "-4", "-sS", "-L", *args], capture_output=True, text=True, check=True
    ).stdout


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(p / 100 * (len(xs) - 1)))]


def main() -> None:
    url, key, folder = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 20
    images = sorted(
        p for p in folder.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )[:n]
    rtt = statistics.median(
        float(curl(["-o", "/dev/null", "-w", "%{time_total}", f"{url}/health"])) for _ in range(3)
    )
    ttfb, wall, infer = [], [], []
    for img in images:
        out = curl(
            [
                "-o",
                "/dev/null",
                "-w",
                "%{time_starttransfer} %{time_total} %header{x-infer-s} %{http_code}",
                "-H",
                f"x-api-key: {key}",
                "-F",
                f"image_file=@{img}",
                f"{url}/v1/segment",
            ]
        ).split()
        if out[3] != "200":
            print(f"{img.name}: http {out[3]}", file=sys.stderr)
            continue
        ttfb.append(float(out[0]) - rtt)
        wall.append(float(out[1]))
        infer.append(float(out[2]))
        print(
            f"{img.name:30s} server={ttfb[-1]:.2f}s infer={infer[-1]:.2f}s wall={wall[-1]:.2f}s",
            file=sys.stderr,
        )
    for name, xs in (("inference", infer), ("server", ttfb), ("wall", wall)):
        p50, p95, p99 = pct(xs, 50), pct(xs, 95), pct(xs, 99)
        print(f"{name:10s} n={len(xs)} p50={p50:.2f}s p95={p95:.2f}s p99={p99:.2f}s")


if __name__ == "__main__":
    main()
