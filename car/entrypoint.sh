#!/bin/bash
# Runs inside the rc-hub container (repo mounted at /opt/rc).
source /opt/ros/foxy/setup.bash
source /root/yahboomcar_ros2_ws/yahboomcar_ws/install/setup.bash
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-32}"   # same domain as the old tasl-l1 pipeline
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export ROBOT_TYPE=x3 RPLIDAR_TYPE=a1
export ROS_LOG_DIR=/tmp/roslog                  # tmpfs: keep ROS logs off the SD card
export PYTHONUNBUFFERED=1
cd /opt/rc
exec python3 -m hub
