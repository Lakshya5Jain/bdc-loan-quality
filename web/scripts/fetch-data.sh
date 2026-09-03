#!/usr/bin/env bash
# Populate web/public/data for a static build.
#
# Locally, `uv run soi export-static` writes the tree directly and this script is a no-op.
# On Vercel (no DuckDB, 100 MB upload cap) the tree is downloaded from the GitHub release
# asset that scripts/publish.sh uploads. Override with DATA_BUNDLE_URL; set FORCE_FETCH=1 to
# replace an existing tree.
set -euo pipefail
cd "$(dirname "$0")/.."

URL="${DATA_BUNDLE_URL:-https://github.com/Lakshya5Jain/bdc-loan-quality/releases/download/data/data.tar.gz}"

if [ -f public/data/manifest.json ] && [ -z "${FORCE_FETCH:-}" ]; then
  echo "fetch-data: public/data present ($(cat public/data/manifest.json)); skipping"
  exit 0
fi

echo "fetch-data: downloading $URL"
rm -rf public/data
mkdir -p public
curl -fsSL --retry 3 "$URL" | tar xz -C public
echo "fetch-data: $(cat public/data/manifest.json)"
