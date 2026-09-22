#!/usr/bin/env bash
# Deploy the leaderboard API on the OrbStack Docker engine.
# Run this from a shell where `docker` talks to OrbStack (usually your Mac host).
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker CLI not found." >&2
  echo "Open OrbStack, then run this script from the Mac host (or any shell where" >&2
  echo "'docker' is backed by OrbStack)." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Cannot reach the Docker daemon. Is OrbStack running?" >&2
  exit 1
fi

echo "Building and starting leaderboard on OrbStack..."
docker compose up -d --build

echo
echo "Waiting for health..."
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo
echo "Deployed on OrbStack:"
echo "  https://api.leaderboard.orb.local"
echo "  https://leaderboard.local"
echo "  http://localhost:8000"
echo
echo "Swagger: https://api.leaderboard.orb.local/docs"
echo "Health:  curl -fsS https://api.leaderboard.orb.local/healthz"
echo "Logs:    docker compose logs -f api"
echo "Stop:    docker compose down"
