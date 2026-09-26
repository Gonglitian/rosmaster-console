#!/bin/bash
# Install or update the hub on the car. Run ON THE CAR, from the repo root:
#   bash car/install_hub.sh
# Idempotent. Afterwards the hub starts on every boot (restart=unless-stopped)
# and the panel is at http://rosmaster.local:8080
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
BASE=rc-base:x3-20260926

# 1. Snapshot Yahboom's x3 container once, as the base image.
if ! docker image inspect "$BASE" >/dev/null 2>&1; then
  echo "[install] snapshotting container x3 -> $BASE"
  docker commit x3 "$BASE"
fi

# 2. Build the hub image (adds one Python package on top).
docker build --network host -t rc-hub:latest --build-arg BASE="$BASE" -f car/Dockerfile car

# 3. Never run two chassis drivers: the old x3 container must stay stopped.
if [ "$(docker inspect -f '{{.State.Running}}' x3 2>/dev/null || echo false)" = "true" ]; then
  echo "[install] stopping old container x3 (it would fight over the serial port)"
  docker stop x3
fi

# 4. (Re)create the hub container.
docker rm -f rc-hub >/dev/null 2>&1 || true
docker run -d --name rc-hub --init --restart unless-stopped --stop-timeout 25 \
  --privileged --network host \
  -v /dev:/dev -v "$REPO":/opt/rc \
  --tmpfs /tmp:size=64m \
  -e TZ=America/Los_Angeles -e ROS_DOMAIN_ID=32 \
  rc-hub:latest /opt/rc/car/entrypoint.sh
echo "[install] rc-hub started; logs: docker logs -f rc-hub"
