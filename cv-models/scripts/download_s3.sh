#!/bin/bash
# Download training data from S3.
#
# Usage:
#   ./scripts/download_s3.sh <S3_URI> <LOCAL_DEST>
# Example:
#   ./scripts/download_s3.sh s3://edensign-training/photos/ data/train_furnished/
#
# Optional env vars:
#   AWS_PROFILE       — use a non-default credentials profile
#   DRY_RUN=1         — show what would be downloaded, don't actually pull
#   EXTRA_ARGS        — extra args passed to `aws s3 sync` (e.g. --exclude '*.tif')

set -e

S3_URI="${1:-}"
LOCAL_DEST="${2:-}"

if [ -z "$S3_URI" ] || [ -z "$LOCAL_DEST" ]; then
  echo "Usage: $0 <S3_URI> <LOCAL_DEST>"
  echo "Example: $0 s3://edensign-training/photos/ data/train_furnished/"
  exit 1
fi

# Build profile flag if AWS_PROFILE is set
PROFILE_FLAG=""
if [ -n "$AWS_PROFILE" ]; then
  PROFILE_FLAG="--profile $AWS_PROFILE"
  echo "Using AWS profile: $AWS_PROFILE"
fi

# Resolve destination relative to cv-models/ if not absolute
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
if [[ "$LOCAL_DEST" != /* ]]; then
  LOCAL_DEST="$PROJECT_ROOT/$LOCAL_DEST"
fi
mkdir -p "$LOCAL_DEST"

echo "Source:      $S3_URI"
echo "Destination: $LOCAL_DEST"
echo "Disk free:   $(df -h "$LOCAL_DEST" | awk 'NR==2 {print $4}') available"
echo ""

# Step 1: List + size summary BEFORE downloading
echo "=== Counting objects in S3 (dry-run sync) ==="
aws $PROFILE_FLAG s3 sync "$S3_URI" "$LOCAL_DEST" --dryrun $EXTRA_ARGS 2>&1 | tee /tmp/s3_dryrun.log | tail -5
n_objects=$(grep -c '^(dryrun) download' /tmp/s3_dryrun.log || echo 0)
echo ""
echo "Would download: $n_objects objects"

# Get total size estimate from `aws s3 ls --summarize --recursive`
echo "=== Total size estimate ==="
aws $PROFILE_FLAG s3 ls "$S3_URI" --recursive --summarize 2>&1 | tail -3

if [ "$DRY_RUN" = "1" ]; then
  echo ""
  echo "DRY_RUN=1 set, stopping before actual download."
  exit 0
fi

# Step 2: Ask for confirmation if more than 500 MB
echo ""
read -p "Proceed with download? (y/N) " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
  echo "Aborted."
  exit 0
fi

# Step 3: Actual download
echo ""
echo "=== Downloading ==="
aws $PROFILE_FLAG s3 sync "$S3_URI" "$LOCAL_DEST" $EXTRA_ARGS

echo ""
echo "=== Done ==="
du -sh "$LOCAL_DEST"
find "$LOCAL_DEST" -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \) | wc -l | awk '{print "  Image files:", $1}'
