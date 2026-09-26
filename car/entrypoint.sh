#!/bin/bash
# Runs inside the rc-hub container (repo mounted at /opt/rc) as tini's child.
#
# It supervises the hub instead of exec'ing it, so that the container (and the
# chassis driver the hub started) outlives a hub crash:
#   hub dies -> the orphaned hf_driver stops receiving /hub/cmd_vel and its own
#   0.5 s watchdog stops the wheels -> we clean up leftovers, send one more
#   zero command over serial, and restart the hub (with all sensors off).
# If the whole container is killed, nothing here runs; see stop_wheels at start.
source /opt/ros/foxy/setup.bash
source /root/yahboomcar_ros2_ws/yahboomcar_ws/install/setup.bash
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-32}"   # same domain as the old tasl-l1 pipeline
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export ROBOT_TYPE=x3 RPLIDAR_TYPE=a1
export ROS_LOG_DIR=/tmp/roslog                  # tmpfs: keep ROS logs off the SD card
export PYTHONUNBUFFERED=1
cd /opt/rc

cleanup_children() {
  if pgrep -f 'ros2 launch' >/dev/null; then
    pkill -INT -f 'ros2 launch'
    sleep 3
  fi
  pkill -9 -f 'ros2 launch|hf_driver_x3|/opt/ros/foxy/lib/|library_ws/install|yahboomcar_ws/install|ffmpeg' 2>/dev/null
  python3 car/stop_wheels.py
}

HUB_PID=
on_term() {
  echo "[entrypoint] SIGTERM: stopping hub"
  [ -n "$HUB_PID" ] && kill -TERM "$HUB_PID" 2>/dev/null && wait "$HUB_PID"
  cleanup_children
  exit 0
}
trap on_term TERM INT

python3 car/stop_wheels.py       # the previous run may have ended with the wheels turning
while true; do
  python3 -m hub &
  HUB_PID=$!
  wait "$HUB_PID"
  rc=$?
  echo "[entrypoint] hub exited (rc=$rc); letting the driver watchdog stop the wheels"
  sleep 1
  cleanup_children
  sleep 2
done
