#!/usr/bin/env bash
# scripts/gen-types.sh
#
# Portable fallback for regenerating shared TypeScript types from the
# running backend's OpenAPI spec. Use this on macOS/Linux/WSL/Git Bash or
# in CI. On Windows dev machines, prefer scripts/gen-types.ps1.
#
# Usage:
#   bash scripts/gen-types.sh
#
# Prereqs: backend running on http://localhost:8000, plus curl and Node.js
# (npx) on PATH.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="${TMPDIR:-/tmp}/sitetracker-openapi.json"

MOBILE_OUT="$ROOT/mobile/src/api/types.ts"
ADMIN_OUT="$ROOT/admin/src/api/types.ts"

echo "Fetching OpenAPI spec from http://localhost:8000/openapi.json"
curl -sSfL http://localhost:8000/openapi.json -o "$TMP"

mkdir -p "$(dirname "$MOBILE_OUT")" "$(dirname "$ADMIN_OUT")"

echo "Generating $MOBILE_OUT"
npx -y openapi-typescript "$TMP" -o "$MOBILE_OUT"

echo "Generating $ADMIN_OUT"
npx -y openapi-typescript "$TMP" -o "$ADMIN_OUT"

rm -f "$TMP"
echo "Done."
