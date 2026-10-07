#!/usr/bin/env bash
# Run after every `modal deploy`. Modal builds 2-3 memory snapshots per GPU worker type, and
# each of those first requests takes 30-150 s. Pay that here, not on a user.
# Usage: scripts/warmup.sh <url> <api-key> [image.jpg]
set -euo pipefail
URL=${1:?url}; KEY=${2:?api key}; IMG=${3:-}
if [[ -z "$IMG" ]]; then
  IMG=$(mktemp -t warmup).jpg
  python3 -c "from PIL import Image; Image.new('RGB',(800,1000),(90,120,160)).save('$IMG')"
fi
for k in 1 2 3 4; do
  curl -4 -sS -L -o /dev/null -m 600 \
    -w "warmup#$k http=%{http_code} wall=%{time_total}s infer=%header{x-infer-s}\n" \
    -H "x-api-key: $KEY" -F "image_file=@$IMG" "$URL/v1/segment"
  sleep 70
done
curl -sS "$URL/health"; echo
