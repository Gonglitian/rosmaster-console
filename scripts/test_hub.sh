#!/bin/bash
# Run a hub on a development machine without the car, for testing the web UI
# and the policy API. No hardware is touched and Wi-Fi changes are refused
# (HF_NO_HARDWARE=1). It uses its own port and ROS domain so it cannot collide
# with the real car.
#   bash scripts/test_hub.sh start   # http://localhost:8091, ROS_DOMAIN_ID=77
#   bash scripts/test_hub.sh stop
#   bash scripts/test_hub.sh log
# (no "set -u": ROS setup.bash uses unbound variables)
cd "$(dirname "$0")/.."
PIDFILE=/tmp/rc_test_hub.pid
LOG=/tmp/rc_test_hub.log
case "${1:-start}" in
  start)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then echo "already running (pid $(cat "$PIDFILE"))"; exit 0; fi
    source /opt/ros/foxy/setup.bash
    export HF_NO_HARDWARE=1 HF_PORT="${HF_PORT:-8091}" ROS_DOMAIN_ID="${ROS_DOMAIN_ID_TEST:-77}" RMW_IMPLEMENTATION=rmw_fastrtps_cpp
    unset FASTRTPS_DEFAULT_PROFILES_FILE
    nohup python3 -m hub > "$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 4
    echo "test hub pid $(cat "$PIDFILE") on port $HF_PORT, ROS_DOMAIN_ID=$ROS_DOMAIN_ID"; tail -3 "$LOG";;
  stop)
    [ -f "$PIDFILE" ] && kill "$(cat "$PIDFILE")" 2>/dev/null && echo stopped; rm -f "$PIDFILE";;
  log)
    tail -40 "$LOG";;
esac
