#!/bin/bash
# Push this checkout to the car and restart the hub. Run on the dev machine:
#   bash scripts/deploy_car.sh            # code only, restart hub
#   bash scripts/deploy_car.sh --install  # also (re)build the image and container
set -euo pipefail
CAR="${CAR:-pi@rosmaster.local}"
cd "$(dirname "$0")/.."
rsync -a --delete --exclude .git --exclude __pycache__ --exclude '*.pyc' ./ "$CAR":rosmaster-console/
if [ "${1:-}" = "--install" ]; then
  ssh "$CAR" 'bash rosmaster-console/car/install_hub.sh'
else
  ssh "$CAR" 'docker restart rc-hub >/dev/null && echo "rc-hub restarted"'
fi
