#!/usr/bin/env bash
# download_data.sh — Pre-fetch large data files that aren't in the repo.
#
# bi/app/services/redfin_market_data.py auto-downloads this file on first
# API call, so this script is OPTIONAL. Use it to pre-warm the cache (e.g.,
# during deployment) so the first user request doesn't take ~30 seconds.

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

REDFIN_URL="https://redfin-public-data.s3.us-west-2.amazonaws.com/redfin_market_tracker/zip_code_market_tracker.tsv000.gz"
REDFIN_DEST="bi/data/redfin_zip_market.tsv.gz"

mkdir -p bi/data

if [ -f "$REDFIN_DEST" ]; then
  age_days=$(( ( $(date +%s) - $(stat -f %m "$REDFIN_DEST" 2>/dev/null || stat -c %Y "$REDFIN_DEST") ) / 86400 ))
  if [ "$age_days" -lt 30 ]; then
    echo "==> Redfin TSV exists and is $age_days days old (max 30). Skipping."
    exit 0
  fi
  echo "==> Redfin TSV is $age_days days old. Re-downloading..."
fi

echo "==> Downloading Redfin national market data (~1.4 GB)..."
curl -fL -o "$REDFIN_DEST" "$REDFIN_URL"

echo "==> Done. $REDFIN_DEST"
ls -lh "$REDFIN_DEST"
