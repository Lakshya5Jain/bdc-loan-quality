#!/usr/bin/env bash
# Refresh the public site after the local database changes.
#
#   1. export the static JSON tree from data/soi.duckdb   (uv run soi export-static)
#   2. upload it as data.tar.gz on the GitHub release "data" (replaces the previous bundle)
#   3. deploy web/ to Vercel production; its build downloads the bundle (web/scripts/fetch-data.sh)
#
# Flags: --no-export  reuse web/public/data as is
#        --no-deploy  upload the bundle but do not deploy
set -euo pipefail
cd "$(dirname "$0")/.."

EXPORT=1
DEPLOY=1
for arg in "$@"; do
  case "$arg" in
    --no-export) EXPORT=0 ;;
    --no-deploy) DEPLOY=0 ;;
    *) echo "unknown flag $arg" >&2; exit 2 ;;
  esac
done

if [ "$EXPORT" = 1 ]; then
  uv run soi export-static
fi
[ -f web/public/data/manifest.json ] || { echo "web/public/data missing; run without --no-export" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "publish: packing bundle"
# COPYFILE_DISABLE / --no-xattrs: keep macOS extended attributes out (they spam GNU tar on Vercel)
# xattrs are dropped by piping through GNU-compatible options and stripping macOS metadata first
xattr -rc web/public/data 2>/dev/null || true
COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -czf "$TMP/data.tar.gz" -C web/public data
ls -la "$TMP/data.tar.gz"

if ! gh release view data >/dev/null 2>&1; then
  gh release create data --title "Static data bundle" \
    --notes "Rolling asset: JSON tree from \`uv run soi export-static\`, downloaded by the Vercel build. Replaced on every publish."
fi
echo "publish: uploading to GitHub release 'data'"
gh release upload data "$TMP/data.tar.gz" --clobber
gh release edit data --notes "Rolling asset from \`uv run soi export-static\`. $(cat web/public/data/manifest.json)"

if [ "$DEPLOY" = 1 ]; then
  echo "publish: deploying to Vercel"
  vercel deploy --prod --yes --cwd web
fi
