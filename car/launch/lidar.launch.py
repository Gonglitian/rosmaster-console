"""RPLIDAR-A1 bringup, started and stopped by the hub.

Same parameters as sllidar_ros2/launch/sllidar_launch.py, which Yahboom's
laser_bringup_launch.py includes for RPLIDAR_TYPE=a1. The hub watches this
process's output for "current scan mode" (success) or "Can not start scan"
(failure, usually the A1 motor not spinning) and retries after a USB reset.
"""
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(package='sllidar_ros2', executable='sllidar_node', name='sllidar_node',
             parameters=[{'serial_port': '/dev/rplidar',
                          'serial_baudrate': 115200,
                          'frame_id': 'laser',
                          'inverted': False,
                          'angle_compensate': True}],
             output='screen'),
    ])
