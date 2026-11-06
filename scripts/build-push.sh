#!/usr/bin/env bash
# Manual alternative to the GitHub Actions build: build the image from this
# checkout and push it to Artifact Registry, tagged with the pyproject.toml
# version. The normal path is the workflow in .github/workflows/build-image.yml.
#
#   scripts/build-push.sh       # reads GOOGLE_CLOUD_PROJECT and REGION from .env
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/lib.sh

version=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n1)

gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet

# --platform matters on Apple Silicon: GKE nodes are amd64.
docker buildx build --platform linux/amd64 --tag "$IMAGE:$version" --push .

echo "Pushed $IMAGE:$version"
